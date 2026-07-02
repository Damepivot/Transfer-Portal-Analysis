"""
Tier 2 gap-fill: download Barttorvik bulk stats for seasons 2021-2026 and
upsert BPM/USG%/TS% into player_seasons rows that currently have NULL bpm.

Barttorvik has wider D1 coverage than CBB Reference — it includes players
with fewer games and schools CBB Reference partially misses. We use it
as a fallback: ONLY writes where bpm IS NULL (never overwrites CBB data).

Column mapping for getadvstats.php CSV (67 cols, 0-indexed):
  [0]  Player   [1]  Team     [3]  Games   [6]  USG%
  [8]  TS%      [51] OBPM     [52] DBPM    [53] BPM
  [31] Year (spring, e.g. 2026 = 2025-26)

Run: python3 etl/backfill_barttorvik_stats.py [--local]
"""

import io, csv, sys, time, requests
import psycopg2
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

if "--local" in sys.argv:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
    import os
    DB_CONFIG = {
        "dbname":   os.getenv("DB_NAME", "ncaa_transfers"),
        "user":     os.getenv("DB_USER", ""),
        "host":     os.getenv("DB_HOST", "localhost"),
        "port":     int(os.getenv("DB_PORT", 5432)),
        "password": os.getenv("DB_PASSWORD", ""),
    }
else:
    from db import DB_CONFIG

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
YEARS   = [2021, 2022, 2023, 2024, 2025, 2026]

BPM_CAP  = 15.0
MIN_GAMES = 1   # include all games — coaches want to see even partial seasons


def year_to_season(y: int) -> str:
    return f"{y-1}-{str(y)[2:]}"


def name_key(n: str) -> tuple[str, str]:
    clean = n.lower().replace(".", "").replace("'", "")
    parts = [w for w in clean.split() if w not in ("jr","sr","ii","iii","iv","v")]
    if len(parts) >= 2:
        return parts[0], parts[-1]
    return (parts[0], parts[0]) if parts else ("", "")


def fetch_barttorvik(year: int) -> list[dict]:
    url = f"https://barttorvik.com/getadvstats.php?year={year}&csv=1"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    rows = list(csv.reader(io.StringIO(r.text)))
    out = []
    for row in rows:
        if len(row) < 54:
            continue
        try:
            games = int(float(row[3])) if row[3] else 0
            bpm   = float(row[53]) if row[53] else None
            if bpm is None or games < MIN_GAMES:
                continue
            bpm   = max(-BPM_CAP, min(BPM_CAP, bpm))
            obpm  = float(row[51]) if row[51] else None
            dbpm  = float(row[52]) if row[52] else None
            usg   = float(row[6])  if row[6]  else None
            ts    = float(row[8])  if row[8]  else None
            if obpm: obpm = max(-BPM_CAP, min(BPM_CAP, obpm))
            if dbpm: dbpm = max(-BPM_CAP, min(BPM_CAP, dbpm))
            if ts:   ts   = ts / 100.0  # Barttorvik gives TS% as 56.3, DB stores as 0.563
            out.append({
                "name":   row[0].strip(),
                "team":   row[1].strip(),
                "season": year_to_season(year),
                "games":  games,
                "bpm":    bpm,
                "obpm":   obpm,
                "dbpm":   dbpm,
                "usg":    usg,
                "ts":     ts,
                "fk":     name_key(row[0].strip()),
                "team_lower": row[1].strip().lower(),
            })
        except (ValueError, IndexError):
            continue
    return out


def build_bart_index(rows: list[dict]) -> dict:
    """Index by (first, last, season) and (first, last, team_lower, season) for fast lookup."""
    idx = {}
    for r in rows:
        # key1: name + season (may have multiple teams — keep best by games)
        k1 = (*r["fk"], r["season"])
        if k1 not in idx or r["games"] > idx[k1]["games"]:
            idx[k1] = r
        # key2: name + team + season
        k2 = (*r["fk"], r["team_lower"], r["season"])
        idx[k2] = r
    return idx


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()

    # Load ALL Barttorvik seasons
    print("Downloading Barttorvik bulk stats...")
    all_rows = []
    for year in YEARS:
        print(f"  Year {year} ({year_to_season(year)})...", end=" ", flush=True)
        try:
            rows = fetch_barttorvik(year)
            all_rows.extend(rows)
            print(f"{len(rows)} players")
        except Exception as e:
            print(f"FAILED: {e}")
        time.sleep(1.5)

    print(f"\nTotal Barttorvik rows loaded: {len(all_rows)}")
    bart_idx = build_bart_index(all_rows)

    # Get all player_seasons rows with NULL bpm (D1 only, any season)
    cur.execute("""
        SELECT ps.player_season_id, ps.player_id, ps.team_id,
               p.full_name, t.name AS school, ps.season
        FROM player_seasons ps
        JOIN players p  ON ps.player_id = p.player_id
        JOIN teams t    ON ps.team_id   = t.team_id
        JOIN conferences c ON t.conference_id = c.conference_id
        WHERE ps.bpm IS NULL
          AND c.tier NOT IN ('sub_d1','international')
          AND ps.season != '2026-27'
        ORDER BY ps.season, p.full_name
    """)
    missing = cur.fetchall()
    print(f"Player-seasons with NULL bpm to fill: {len(missing)}")

    filled = 0
    for psid, pid, tid, full_name, school, season in missing:
        fk = name_key(full_name)
        team_lower = school.lower()

        # Try exact: name + team + season
        row = bart_idx.get((*fk, team_lower, season))
        # Fallback: name + season only (different school name normalization)
        if row is None:
            row = bart_idx.get((*fk, season))

        if row is None:
            continue

        cur.execute("""
            UPDATE player_seasons SET
                bpm      = %s,
                obpm     = COALESCE(obpm,    %s),
                dbpm     = COALESCE(dbpm,    %s),
                usg_pct  = COALESCE(usg_pct, %s),
                ts_pct   = COALESCE(ts_pct,  %s),
                games    = COALESCE(games,   %s),
                stat_note = COALESCE(stat_note, 'barttorvik')
            WHERE player_season_id = %s AND bpm IS NULL
        """, (row["bpm"], row["obpm"], row["dbpm"], row["usg"], row["ts"], row["games"], psid))
        if cur.rowcount > 0:
            filled += 1

    conn.commit()
    print(f"Filled {filled} player-seasons with Barttorvik BPM data")

    # Refresh the materialized view
    print("Refreshing materialized views...")
    cur.execute("REFRESH MATERIALIZED VIEW tier_pair_expectations")
    cur.execute("REFRESH MATERIALIZED VIEW tier_pair_fallback")
    cur.execute("REFRESH MATERIALIZED VIEW individual_transfer_scores")
    conn.commit()

    # Report final gap count
    cur.execute("""
        SELECT COUNT(*) - COUNT(skill_index_before)
        FROM individual_transfer_scores
        WHERE from_tier NOT IN ('sub_d1','international')
    """)
    remaining = cur.fetchone()[0]
    print(f"Remaining skill_index_before gaps after Tier 2: {remaining}")

    conn.close()


if __name__ == "__main__":
    main()
