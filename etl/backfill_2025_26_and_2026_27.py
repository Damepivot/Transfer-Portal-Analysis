"""
Incremental backfill — adds 2025-26 real CBB Reference stats and the 2026-27
in-progress portal class WITHOUT touching the existing TRUNCATE+rebuild path
in load_real_data.py (too risky to run against a live/production DB).

Background:
  - player_seasons had placeholder (all-NULL) rows for season 2025-26 because
    scrape_cbb_reference.py had never been run for that season. Now it has.
  - Barttorvik's "year=2026" trans page used to return a one-off batch of
    mid-cycle entries that got filed under season 2025-26. As of 2026-06,
    barttorvik rolled its live "current" pointer to year=2027, and comparing
    that fresh pull against the old 2025-26 batch shows 1851 of 1895 entries
    are the *same* player+route pair — i.e. they were never 2025-26 transfers
    at all, they were 2026-27 commits that got mislabeled under the old
    "exception" mapping. We reclassify those, then insert the genuinely-new
    2026-27 entries, then backfill real 2025-26 BPM for what's left.

Run order matters: reclassify BEFORE backfilling stats, so the stats backfill
only ever touches transfers that are actually 2025-26.

Run: python3 etl/backfill_2025_26_and_2026_27.py            (targets db.py's DB_CONFIG)
     python3 etl/backfill_2025_26_and_2026_27.py --local     (forces local Postgres via .env,
                                                                bypassing db.py's Streamlit-secrets-
                                                                first resolution — db.py finds
                                                                .streamlit/secrets.toml even when
                                                                run as a plain script, not just
                                                                under `streamlit run`)
"""

import os
import sys
import psycopg2
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv
sys.path.insert(0, str(Path(__file__).parent.parent))

if "--local" in sys.argv:
    load_dotenv(Path(__file__).parent.parent / ".env")
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

# Same manual overrides as load_real_data.py's SCHOOL_NAME_MAP (duplicated
# here rather than imported, since that dict is a local var inside main()).
SCHOOL_NAME_MAP = {
    "Alabama State":          "Alabama St.",
    "Alcorn State":           "Alcorn St.",
    "Appalachian State":      "Appalachian St.",
    "Arizona State":          "Arizona St.",
    "Arkansas State":         "Arkansas St.",
    "Arkansas-Little Rock":   "Little Rock",
    "Ball State":             "Ball St.",
    "Bethune-Cookman":        "Bethune Cookman",
    "Boise State":            "Boise St.",
    "Cal State Bakersfield":  "Cal St. Bakersfield",
    "Cal State Fullerton":    "Cal St. Fullerton",
    "Cal State Northridge":   "Cal St. Northridge",
    "California Baptist":     "Cal Baptist",
    "Chicago State":          "Chicago St.",
    "Cleveland State":        "Cleveland St.",
    "Colorado State":         "Colorado St.",
    "Coppin State":           "Coppin St.",
    "Delaware State":         "Delaware St.",
    "East Tennessee State":   "East Tennessee St.",
    "Florida State":          "Florida St.",
    "Fresno State":           "Fresno St.",
    "Georgia State":          "Georgia St.",
    "Grambling State":        "Grambling St.",
    "Idaho State":            "Idaho St.",
    "Illinois State":         "Illinois St.",
    "Indiana State":          "Indiana St.",
    "Iowa State":             "Iowa St.",
    "Jackson State":          "Jackson St.",
    "Jacksonville State":     "Jacksonville St.",
    "Kansas State":           "Kansas St.",
    "Kennesaw State":         "Kennesaw St.",
    "Kent State":             "Kent St.",
    "Long Beach State":       "Long Beach St.",
    "McNeese State":          "McNeese St.",
    "Michigan State":         "Michigan St.",
    "Mississippi State":      "Mississippi St.",
    "Mississippi Valley State":"Mississippi Valley St.",
    "Missouri State":         "Missouri St.",
    "Montana State":          "Montana St.",
    "Morehead State":         "Morehead St.",
    "Morgan State":           "Morgan St.",
    "Murray State":           "Murray St.",
    "New Mexico State":       "New Mexico St.",
    "Norfolk State":          "Norfolk St.",
    "North Carolina State":   "North Carolina St.",
    "NC State":               "North Carolina St.",
    "North Dakota State":     "North Dakota St.",
    "Ohio State":             "Ohio St.",
    "Oklahoma State":         "Oklahoma St.",
    "Oregon State":           "Oregon St.",
    "Penn State":             "Penn St.",
    "Portland State":        "Portland St.",
    "Sacramento State":       "Sacramento St.",
    "San Diego State":        "San Diego St.",
    "Sam Houston State":      "Sam Houston St.",
    "South Carolina State":   "South Carolina St.",
    "South Dakota State":     "South Dakota St.",
    "Southeast Missouri State":"Southeast Missouri St.",
    "Southern Illinois":      "SIU Edwardsville",
    "Stephen F. Austin":      "SF Austin",
    "Tennessee State":        "Tennessee St.",
    "Texas State":            "Texas St.",
    "Utah State":             "Utah St.",
    "Washington State":       "Washington St.",
    "Weber State":            "Weber St.",
    "Wichita State":          "Wichita St.",
    "Wright State":           "Wright St.",
    "Youngstown State":       "Youngstown St.",
    "UConn":                  "Connecticut",
    "UCF":                    "UCF",
    "Central Florida":        "UCF",
    "Texas Christian":        "TCU",
    "Virginia Commonwealth":  "VCU",
    "Southern Methodist":     "SMU",
    "UTEP":                   "Texas El Paso",
    "UTSA":                   "UTSA",
    "VCU":                    "VCU",
    "SMU":                    "SMU",
    "TCU":                    "TCU",
    "BYU":                    "BYU",
    "LSU":                    "LSU",
    "Ole Miss":               "Mississippi",
    "Miami (FL)":             "Miami FL",
    "Miami (Ohio)":           "Miami OH",
    "Miami (OH)":             "Miami OH",
    "Loyola (Chi) Ramblers":  "Loyola Chicago",
    "Loyola (Chi)":           "Loyola Chicago",
    "Loyola-Chicago":         "Loyola Chicago",
    "USF":                    "South Florida",
    "Louisiana-Monroe":       "Louisiana Monroe",
    "Louisiana":              "Louisiana Lafayette",
    "Gardner-Webb":           "Gardner Webb",
    "San Jose State":         "San Jose St.",
    "UT Martin":              "Tennessee Martin",
    "Nicholls State":         "Nicholls St.",
    "Detroit Mercy":          "Detroit",
    "Pennsylvania":           "Penn",
    "Cleveland State University": "Cleveland St.",
    "Tarleton State":         "Tarleton St.",
    "Long Island":            "Long Island University",
}


def normalize_school(raw: str) -> str:
    raw = str(raw).strip()
    return SCHOOL_NAME_MAP.get(raw, raw)


def build_team_index(cur):
    cur.execute("SELECT team_id, name, season, conference_id FROM teams")
    team_id_map  = {}     # (name, season) -> team_id
    team_conf    = {}     # name -> most-recently-seen conference_id
    all_names    = set()
    for tid, name, season, conf_id in cur.fetchall():
        team_id_map[(name, season)] = tid
        all_names.add(name)
        if conf_id is not None:
            team_conf[name] = conf_id   # later rows overwrite — fine, any season's conf is a reasonable guess
    return team_id_map, all_names, team_conf


def match_team(raw, season, team_id_map, all_names):
    if not raw or str(raw).strip().lower() in ("nan", "none", ""):
        return None
    normalized = normalize_school(str(raw).strip())
    if (normalized, season) in team_id_map:
        return team_id_map[(normalized, season)]
    lower = normalized.lower()
    for name in all_names:
        if name.lower() == lower:
            return team_id_map.get((name, season))
    for name in all_names:
        if lower in name.lower() or name.lower() in lower:
            return team_id_map.get((name, season))
    return None


def get_or_create_team(raw, season, team_id_map, all_names, team_conf, cur):
    tid = match_team(raw, season, team_id_map, all_names)
    if tid:
        return tid
    normalized = normalize_school(str(raw).strip())
    conf_id = team_conf.get(normalized)
    cur.execute(
        "INSERT INTO teams (name, conference_id, season) VALUES (%s,%s,%s) RETURNING team_id",
        (normalized, conf_id, season)
    )
    tid = cur.fetchone()[0]
    team_id_map[(normalized, season)] = tid
    all_names.add(normalized)
    return tid


def parse_stat(val):
    try:
        f = float(val)
        return None if pd.isna(f) else f
    except (ValueError, TypeError):
        return None


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    team_id_map, all_names, team_conf = build_team_index(cur)

    # ── PART 1: reclassify mislabeled 2025-26 -> 2026-27 transfers ───────────
    print("=== Part 1: reclassify mislabeled 2025-26 -> 2026-27 transfers ===")
    bart = pd.read_csv(DATA_DIR / "barttorvik_transfers.csv")
    b2526 = bart[bart["season"] == "2025-26"]
    b2627 = bart[bart["season"] == "2026-27"]
    dup_keys = set(
        zip(b2526["player_name"], b2526["from_school"], b2526["to_school"])
    ) & set(
        zip(b2627["player_name"], b2627["from_school"], b2627["to_school"])
    )
    print(f"  {len(dup_keys)} player+route pairs confirmed as mislabeled (appear in both buckets)")

    cur.execute("SELECT player_id, LOWER(full_name) FROM players")
    name_to_pid = {name: pid for pid, name in cur.fetchall()}

    reclassified = reclass_skipped = 0
    for player_name, from_school, to_school in dup_keys:
        pid = name_to_pid.get(str(player_name).strip().lower())
        if not pid:
            reclass_skipped += 1
            continue

        from_tid_old = match_team(from_school, "2025-26", team_id_map, all_names)
        to_tid_old = match_team(to_school, "2025-26", team_id_map, all_names)
        if not from_tid_old or not to_tid_old:
            reclass_skipped += 1
            continue

        cur.execute(
            """SELECT transfer_id FROM transfers
               WHERE player_id=%s AND from_team_id=%s AND to_team_id=%s AND season='2025-26'""",
            (pid, from_tid_old, to_tid_old)
        )
        row = cur.fetchone()
        if not row:
            reclass_skipped += 1
            continue
        transfer_id = row[0]

        from_tid_new = get_or_create_team(from_school, "2026-27", team_id_map, all_names, team_conf, cur)
        to_tid_new = get_or_create_team(to_school, "2026-27", team_id_map, all_names, team_conf, cur)

        # Drop the bogus 2025-26 placeholder stats row for this (player, old destination) —
        # they never actually played there in 2025-26.
        cur.execute(
            "DELETE FROM player_seasons WHERE player_id=%s AND team_id=%s AND season='2025-26' AND bpm IS NULL",
            (pid, to_tid_old)
        )

        # Idempotency guard: if the reclassified tuple already exists (e.g. a
        # re-run, or Part 2 beat us to it), drop the stale 2025-26 row instead
        # of violating the (player_id, from_team_id, to_team_id, season) unique
        # constraint on transfers.
        cur.execute(
            """SELECT 1 FROM transfers
               WHERE player_id=%s AND from_team_id=%s AND to_team_id=%s AND season='2026-27'""",
            (pid, from_tid_new, to_tid_new)
        )
        if cur.fetchone():
            cur.execute("DELETE FROM transfers WHERE transfer_id=%s", (transfer_id,))
            reclassified += 1
            continue

        cur.execute(
            """UPDATE transfers SET from_team_id=%s, to_team_id=%s, season='2026-27'
               WHERE transfer_id=%s""",
            (from_tid_new, to_tid_new, transfer_id)
        )
        cur.execute(
            "INSERT INTO player_seasons (player_id,team_id,season) VALUES (%s,%s,'2026-27') ON CONFLICT (player_id,team_id,season) DO NOTHING",
            (pid, to_tid_new)
        )
        reclassified += 1

    conn.commit()
    print(f"  Reclassified {reclassified} transfers to 2026-27, skipped {reclass_skipped} (no DB match)")

    # ── PART 2: insert genuinely-new 2026-27 entries ──────────────────────────
    print("=== Part 2: insert new 2026-27 transfers ===")
    new_keys = set(
        zip(b2627["player_name"], b2627["from_school"], b2627["to_school"])
    ) - dup_keys
    new_inserted = new_skipped = 0
    for player_name, from_school, to_school in new_keys:
        name = str(player_name).strip().title()
        cur.execute(
            "INSERT INTO players (full_name) VALUES (%s) ON CONFLICT (full_name) DO NOTHING",
            (name,)
        )
        pid = name_to_pid.get(name.lower())
        if not pid:
            cur.execute("SELECT player_id FROM players WHERE full_name=%s", (name,))
            r = cur.fetchone()
            if r:
                pid = r[0]
                name_to_pid[name.lower()] = pid
        if not pid:
            new_skipped += 1
            continue

        from_tid = get_or_create_team(from_school, "2026-27", team_id_map, all_names, team_conf, cur)
        to_tid = get_or_create_team(to_school, "2026-27", team_id_map, all_names, team_conf, cur)
        if not from_tid or not to_tid or from_tid == to_tid:
            new_skipped += 1
            continue

        cur.execute(
            """INSERT INTO transfers (player_id, from_team_id, to_team_id, season, transfer_type)
               VALUES (%s,%s,%s,'2026-27','portal') ON CONFLICT DO NOTHING""",
            (pid, from_tid, to_tid)
        )
        new_inserted += cur.rowcount
        cur.execute(
            "INSERT INTO player_seasons (player_id,team_id,season) VALUES (%s,%s,'2026-27') ON CONFLICT (player_id,team_id,season) DO NOTHING",
            (pid, to_tid)
        )

    conn.commit()
    print(f"  Inserted {new_inserted} new 2026-27 transfers, skipped {new_skipped}")

    # ── PART 3: backfill real 2025-26 CBB Reference stats ─────────────────────
    print("=== Part 3: backfill real 2025-26 BPM/usage/TS% ===")
    cbb_df = pd.read_csv(DATA_DIR / "cbb_player_stats.csv")
    cbb_df = cbb_df[(cbb_df["Player"] != "Team Totals") & (cbb_df["season"] == "2025-26")].copy()
    print(f"  {len(cbb_df)} 2025-26 player rows to apply")

    cur.execute("SELECT DISTINCT player_id FROM transfers")
    transfer_pids = {r[0] for r in cur.fetchall()}

    lastname_to_pids: dict[str, list[tuple]] = {}
    for full_name_lower, pid in name_to_pid.items():
        parts = full_name_lower.strip().split()
        if parts:
            lastname_to_pids.setdefault(parts[-1], []).append((pid, full_name_lower))

    upserted = stat_skipped = 0
    for _, row in cbb_df.iterrows():
        player_name = str(row["Player"]).strip()
        school = str(row["school"]).strip()
        season = str(row["season"]).strip()

        pid = name_to_pid.get(player_name.lower())
        if not pid:
            last = player_name.lower().split()[-1] if player_name else ""
            candidates = [p for p, fn in lastname_to_pids.get(last, []) if p in transfer_pids]
            if len(candidates) == 1:
                pid = candidates[0]
            elif len(candidates) > 1:
                for cand_pid in candidates:
                    tid_check = match_team(school, season, team_id_map, all_names)
                    if tid_check:
                        cur.execute(
                            "SELECT 1 FROM player_seasons WHERE player_id=%s AND team_id=%s AND season=%s LIMIT 1",
                            (cand_pid, tid_check, season)
                        )
                        if cur.fetchone():
                            pid = cand_pid
                            break

        if not pid or pid not in transfer_pids:
            stat_skipped += 1
            continue

        bpm = parse_stat(row.get("BPM"))
        obpm = parse_stat(row.get("OBPM"))
        dbpm = parse_stat(row.get("DBPM"))
        ts_pct = parse_stat(row.get("TS%"))
        usg_pct = parse_stat(row.get("USG%"))
        ws_per_40 = parse_stat(row.get("WS/40"))
        ws = parse_stat(row.get("WS"))
        games = int(row["G"]) if pd.notna(row.get("G")) else None

        if bpm is None or abs(bpm) > 15 or (games is not None and games < 12):
            stat_skipped += 1
            continue
        if obpm is not None and abs(obpm) > 15:
            obpm = None
        if dbpm is not None and abs(dbpm) > 15:
            dbpm = None
        if ws_per_40 is not None and (ws_per_40 < -0.1 or ws_per_40 > 0.5):
            ws_per_40 = None
        if ws is not None and (ws < -1 or ws > 15):
            ws = None

        tid = get_or_create_team(school, season, team_id_map, all_names, team_conf, cur)

        cur.execute(
            """INSERT INTO player_seasons (player_id, team_id, season, bpm, obpm, dbpm, ts_pct, usg_pct, games, ws_per_40, ws)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (player_id, team_id, season) DO UPDATE SET
                   bpm       = EXCLUDED.bpm,
                   obpm      = COALESCE(EXCLUDED.obpm,      player_seasons.obpm),
                   dbpm      = COALESCE(EXCLUDED.dbpm,      player_seasons.dbpm),
                   ts_pct    = COALESCE(EXCLUDED.ts_pct,    player_seasons.ts_pct),
                   usg_pct   = COALESCE(EXCLUDED.usg_pct,   player_seasons.usg_pct),
                   games     = COALESCE(EXCLUDED.games,     player_seasons.games),
                   ws_per_40 = COALESCE(EXCLUDED.ws_per_40, player_seasons.ws_per_40),
                   ws        = COALESCE(EXCLUDED.ws,        player_seasons.ws)
            """,
            (pid, tid, season, bpm, obpm, dbpm, ts_pct, usg_pct, games, ws_per_40, ws)
        )
        upserted += 1

    conn.commit()
    print(f"  {upserted} player-seasons upserted with real stats, {stat_skipped} skipped")

    cur.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
