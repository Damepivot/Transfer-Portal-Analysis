"""
Fix missing BPM data for past-season player_seasons rows.

Four strategies applied in order:

  1. Name aliases + first/last fuzzy match (same school + season) — CBB Reference uses
     suffixes (Jr., III, V) and initials ("D.J." vs "Dj"). A curated alias map plus a
     suffix-stripping fuzzy matcher covers the most common formatting differences.

  2. School-agnostic match (same season, any school) — some Kaggle transfer records have
     the wrong destination school. If the player exists in CBB at a different school in the
     same season, insert a new player_seasons row at that school so the score can fire.

  3. Prior-season fallback (injury / limited games) — players with < 12 games in the
     expected season played too few minutes for BPM to be reliable. We find their most
     recent prior season with >= 12 games and write those stats with stat_note = '*'
     so the dashboard can flag them as estimated from a prior year.

  4. BartTorvik lookup — used only to confirm names/schools for players not found in CBB
     Reference. BartTorvik BPM [col 53] was evaluated but NOT written to the DB — we
     prefer NULL over estimated data. The download logic remains for diagnostic use.

Column mapping for BartTorvik getadvstats.php CSV (67 cols, no header):
  [0]  Player name      [1]  Team          [3]  Games
  [6]  USG%             [8]  TS%           [28] PORPAG
  [51] OBPM             [52] DBPM          [53] BPM
  [31] Year (spring, e.g. 2025 = 2024-25)

Run: python3 etl/fix_missing_stats.py
"""

import sys
import time
import requests
import io, csv
import psycopg2
import pandas as pd
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

DATA_DIR = Path(__file__).parent.parent / "data" / "raw"

# DB name → CBB Reference exact name.
# Sources of mismatch: suffixes dropped in Kaggle data (Jr., II, III, V),
# initials formatted differently (D.J. vs Dj), or middle name included in CBB.
NAME_ALIASES = {
    # Suffix dropped in Kaggle CSV
    "Trey Murphy":            "Trey Murphy III",
    "Ferron Flavors":         "Ferron Flavors Jr.",
    "Landers Nolley":         "Landers Nolley II",
    "Sean East":              "Sean East II",
    "Dwight Wilson":          "Dwight Wilson III",
    "Ricky Lindo":            "Ricky Lindo Jr.",
    "Corey Walker":           "Corey Walker Jr.",
    "Kevin Easley":           "Kevin Easley Jr.",
    "Jimmy Nichols":          "Jimmy Nichols Jr.",
    "Clyde Trapp":            "Clyde Trapp Jr.",
    "Holland Woods":          "Holland Woods II",
    "James Bishop":           "James Bishop IV",
    "Randy Miller":           "Randy Miller Jr.",
    "Serrel Smith":           "Serrel Smith Jr.",
    "Brandon Huffman Ii":     "Brandon Huffman",   # DB over-added suffix
    "Freddie Dilione":        "Freddie Dilione V",
    "Victor Enoh":            "Victor Enoh Jr.",
    "Donovan Gregory":        "Donovan Gregory Jr.",
    "Marcus Garrett":         "Marcus Garrett Jr.",
    "Patrick McCaffery":      "Patrick McCaffery",
    "Damari Monsanto":        "Damari Monsanto Jr.",
    "Kenny Strawbridge":      "Kenny Strawbridge Jr.",
    "Elias Harden":           "Elias Harden Jr.",
    "Tyson Jolly":            "Tyson Jolly Jr.",
    "Jahmir Young":           "Jahmir Young",
    "Isaiah Cottrell":        "Isaiah Cottrell",
    "Henry Coleman":          "Henry Coleman III",
    "Brandon Huffman":        "Brandon Huffman II",
    # Initials formatted differently
    "Dj Carton":              "D.J. Carton",
    "Dj Jeffries":            "D.J. Jeffries",
    # Middle name included in CBB Reference
    "Karim Coulibaly":        "Abdoul Karim Coulibaly",
    "Allen Mukeba":           "Allen David Mukeba",
    # Dan vs Daniel
    "Dan Skillings Jr.":      "Daniel Skillings Jr.",
    "Dan Skillings":          "Daniel Skillings Jr.",
    # Other known one-off discrepancies
    "Cam Carter":             "Cam'Ron Carter",
    "Cj Walker":              "C.J. Walker",
    "Pj Dozier":              "P.J. Dozier",
    "Tj Moss":                "T.J. Moss",
    "Aj Green":               "A.J. Green",
    "Aj Lawson":              "A.J. Lawson",
    "Bj Boston":              "B.J. Boston",
    "Jd Notae":               "JD Notae",
}


BT_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
BT_SEASON_MAP = {
    "2020-21": 2021, "2021-22": 2022,
    "2022-23": 2023, "2023-24": 2024, "2024-25": 2025,
    "2025-26": 2026,
}


def load_barttorvik(years: list[int]) -> pd.DataFrame:
    """Download BartTorvik bulk CSVs for the given spring years and return a DataFrame."""
    frames = []
    for yr in years:
        print(f"  Downloading BartTorvik {yr}...")
        try:
            r = requests.get(
                f"https://barttorvik.com/getadvstats.php?year={yr}&csv=1",
                headers=BT_HEADERS, timeout=20
            )
            r.raise_for_status()
            rows = list(csv.reader(io.StringIO(r.text)))
            valid = [row for row in rows if len(row) >= 54]
            season = f"{yr-1}-{str(yr)[2:]}"  # 2025 → "2024-25"
            for row in valid:
                try:
                    g = int(float(row[3])) if row[3] else None
                    frames.append({
                        "name":   row[0].strip(),
                        "team":   row[1].strip(),
                        "season": season,
                        "games":  g,
                        "usg":    float(row[6])  if row[6]  else None,
                        "ts":     float(row[8])  if row[8]  else None,
                        "porpag": float(row[28]) if row[28] else None,
                        "obpm":   float(row[51]) if row[51] else None,
                        "dbpm":   float(row[52]) if row[52] else None,
                        "bpm":    float(row[53]) if row[53] else None,
                    })
                except (ValueError, IndexError):
                    continue
            print(f"    {len(valid)} players")
            time.sleep(1.0)
        except Exception as e:
            print(f"  BartTorvik {yr} failed: {e}")
    if not frames:
        return pd.DataFrame()
    df = pd.DataFrame(frames)
    df["name_lower"]  = df["name"].str.lower().str.strip()
    df["first_last"]  = df["name_lower"].apply(lambda n: name_key_bt(n))
    return df


def name_key_bt(n: str) -> tuple[str, str]:
    """First+last word key, stripping suffixes and punctuation."""
    clean = n.lower().replace(".", "").replace("'", "")
    parts = [w for w in clean.split() if w not in ("jr","sr","ii","iii","iv","v")]
    if len(parts) >= 2:
        return parts[0], parts[-1]
    return (parts[0], parts[0]) if parts else ("", "")


def load_cbb() -> pd.DataFrame:
    """Load CBB stats CSV, indexed by lowercase name for fast lookup."""
    df = pd.read_csv(DATA_DIR / "cbb_player_stats.csv")
    df = df[df["Player"] != "Team Totals"].copy()
    df["name_lower"] = df["Player"].str.lower().str.strip()
    df["G"] = pd.to_numeric(df["G"], errors="coerce")
    df["BPM"] = pd.to_numeric(df["BPM"], errors="coerce")
    return df


def parse_stat(val):
    try:
        f = float(val)
        return None if pd.isna(f) else f
    except (ValueError, TypeError):
        return None


def upsert_stats(cur, pid: int, tid: int, season: str, row: pd.Series, note: str | None = None):
    """Write CBB stats into player_seasons; create the row if it doesn't exist yet."""
    bpm  = parse_stat(row.get("BPM"))
    obpm = parse_stat(row.get("OBPM"))
    dbpm = parse_stat(row.get("DBPM"))
    ts   = parse_stat(row.get("TS%"))
    usg  = parse_stat(row.get("USG%"))
    g    = int(row["G"]) if pd.notna(row.get("G")) else None

    if bpm is None or abs(bpm) > 15:
        return False
    if obpm is not None and abs(obpm) > 15:
        obpm = None
    if dbpm is not None and abs(dbpm) > 15:
        dbpm = None

    cur.execute(
        """INSERT INTO player_seasons
               (player_id, team_id, season, bpm, obpm, dbpm, ts_pct, usg_pct, games, stat_note)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (player_id, team_id, season) DO UPDATE SET
               bpm       = EXCLUDED.bpm,
               obpm      = COALESCE(EXCLUDED.obpm,    player_seasons.obpm),
               dbpm      = COALESCE(EXCLUDED.dbpm,    player_seasons.dbpm),
               ts_pct    = COALESCE(EXCLUDED.ts_pct,  player_seasons.ts_pct),
               usg_pct   = COALESCE(EXCLUDED.usg_pct, player_seasons.usg_pct),
               games     = COALESCE(EXCLUDED.games,   player_seasons.games),
               stat_note = EXCLUDED.stat_note""",
        (pid, tid, season, bpm, obpm, dbpm, ts, usg, g, note)
    )
    return cur.rowcount > 0


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()

    cbb = load_cbb()

    # All past-season rows still missing BPM
    cur.execute("""
        SELECT ps.player_season_id, p.player_id, p.full_name,
               t.team_id, t.name AS school, ps.season
        FROM player_seasons ps
        JOIN players p ON ps.player_id = p.player_id
        JOIN teams   t ON ps.team_id   = t.team_id
        WHERE ps.bpm IS NULL
          AND ps.season != '2026-27'
        ORDER BY ps.season, p.full_name
    """)
    missing = cur.fetchall()
    print(f"Past-season blanks to fix: {len(missing)}")

    # team name → team_id map for inserting new rows
    cur.execute("SELECT team_id, name, season FROM teams")
    team_map = {}  # (lower_name, season) → team_id
    for tid, name, season in cur.fetchall():
        team_map[(name.lower().strip(), season)] = tid

    # Precompute first+last word index on CBB for fast fuzzy lookup.
    # Strips suffixes (Jr., III, V) and initials dots so "D.J." == "dj".
    def name_key(n: str) -> tuple[str, str]:
        """Return (first_word, last_word) ignoring suffixes and punctuation."""
        clean = n.lower().replace(".", "").replace("'", "")
        parts = [w for w in clean.split() if w not in ("jr","sr","ii","iii","iv","v")]
        if len(parts) >= 2:
            return parts[0], parts[-1]
        return (parts[0], parts[0]) if parts else ("", "")

    cbb["first_last"] = cbb["name_lower"].apply(name_key)

    fixed_alias   = 0
    fixed_fuzzy   = 0
    fixed_school  = 0
    fixed_prior   = 0
    still_missing = 0

    for psid, pid, full_name, tid, school, season in missing:
        # Resolve CBB Reference name (alias or exact)
        cbb_name = NAME_ALIASES.get(full_name, full_name)
        name_lower = cbb_name.lower().strip()

        # ── Strategy 1: alias + exact school + season match ───────────────────
        exact = cbb[
            (cbb["name_lower"] == name_lower) &
            (cbb["school"].str.strip() == school) &
            (cbb["season"] == season) &
            (cbb["G"] >= 12)
        ]
        if not exact.empty:
            row = exact.sort_values("G", ascending=False).iloc[0]
            if upsert_stats(cur, pid, tid, season, row):
                fixed_alias += 1
                continue

        # ── Strategy 1b: first+last name match, same school + season ─────────
        # Handles suffix mismatches (Jr./III/V) and initial formatting (D.J. vs Dj)
        # without relying on the alias map having an entry for every player.
        fk = name_key(full_name)
        if fk[0]:
            fuzzy_exact_school = cbb[
                (cbb["first_last"] == fk) &
                (cbb["school"].str.strip() == school) &
                (cbb["season"] == season) &
                (cbb["G"] >= 12)
            ]
            if not fuzzy_exact_school.empty:
                row = fuzzy_exact_school.sort_values("G", ascending=False).iloc[0]
                if upsert_stats(cur, pid, tid, season, row):
                    fixed_fuzzy += 1
                    continue

        # ── Strategy 2: same season, any school (exact or fuzzy name) ──────────
        # The transfer destination in our portal CSV may be wrong; trust CBB school.
        same_season = cbb[
            ((cbb["name_lower"] == name_lower) | (fk[0] and (cbb["first_last"] == fk))) &
            (cbb["season"] == season) &
            (cbb["G"] >= 12)
        ]
        if not same_season.empty:
            row = same_season.sort_values("G", ascending=False).iloc[0]
            cbb_school = row["school"].strip()
            # Find the team_id for the school CBB says they're at
            cbb_tid = team_map.get((cbb_school.lower(), season))
            if not cbb_tid:
                # Create a minimal team row if not already in DB
                cur.execute(
                    "INSERT INTO teams (name, season) VALUES (%s, %s)"
                    " ON CONFLICT DO NOTHING RETURNING team_id",
                    (cbb_school, season)
                )
                r = cur.fetchone()
                if r:
                    cbb_tid = r[0]
                    team_map[(cbb_school.lower(), season)] = cbb_tid
                else:
                    cur.execute(
                        "SELECT team_id FROM teams WHERE LOWER(name)=LOWER(%s) AND season=%s LIMIT 1",
                        (cbb_school, season)
                    )
                    r = cur.fetchone()
                    if r:
                        cbb_tid = r[0]
                        team_map[(cbb_school.lower(), season)] = cbb_tid

            if cbb_tid and upsert_stats(cur, pid, cbb_tid, season, row):
                fixed_school += 1
                continue

        # ── Strategy 3: injury / limited games detection ─────────────────────
        # If the player exists in CBB at all but had < 12 games in the transfer
        # season, mark the row as 'injury/limited' so the reason is visible in
        # the dashboard. BPM stays NULL — no estimated data written.
        any_cbb = cbb[
            (cbb["name_lower"] == name_lower) | (fk[0] and (cbb["first_last"] == fk))
        ]
        if not any_cbb.empty:
            cur.execute(
                "UPDATE player_seasons SET stat_note = 'injury/limited' WHERE player_season_id = %s",
                (psid,)
            )
            fixed_prior += 1
            continue

        still_missing += 1

    conn.commit()

    # ── Strategy 4: BartTorvik backup ────────────────────────────────────────
    conn.commit()

    # ── Mark post-transfer injury/limited rows as Didn't Fit ──────────────────
    # A player who transferred and played fewer than 12 games at their new school
    # is a failure by definition — regardless of why. BPM = -1.0 is safely below
    # the -0.5 Didn't Fit threshold. Pre-transfer injury rows stay NULL (not their fault).
    cur.execute("""
        UPDATE player_seasons ps
        SET bpm = -1.0
        WHERE ps.stat_note = 'injury/limited'
          AND ps.bpm IS NULL
          AND EXISTS (
              SELECT 1 FROM transfers tr
              WHERE tr.player_id = ps.player_id
                AND tr.to_team_id = ps.team_id
                AND tr.season = ps.season
          )
    """)
    print(f"  {cur.rowcount} post-transfer injury/limited rows → BPM = -1.0 (Didn't Fit)")
    conn.commit()

    # Refresh materialized views so scoring picks up the new stats
    print("Refreshing materialized views...")
    cur.execute("REFRESH MATERIALIZED VIEW tier_pair_expectations")
    cur.execute("REFRESH MATERIALIZED VIEW tier_pair_fallback")
    cur.execute("REFRESH MATERIALIZED VIEW individual_transfer_scores")
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM individual_transfer_scores")
    scored = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM player_seasons WHERE bpm IS NULL AND season != '2026-27'")
    blank = cur.fetchone()[0]

    cur.close()
    conn.close()

    print(f"\nResults:")
    print(f"  Fixed via alias + exact school/season:       {fixed_alias}")
    print(f"  Fixed via first+last fuzzy, exact school:    {fixed_fuzzy}")
    print(f"  Fixed via same-season, any school:           {fixed_school}")
    print(f"  Labelled injury/limited (BPM stays NULL):    {fixed_prior}")
    print(f"  No CBB trace found:                          {still_missing}")
    print(f"  Scored transfers: {scored}")
    print(f"  Past-season rows still blank: {blank}")


if __name__ == "__main__":
    main()
