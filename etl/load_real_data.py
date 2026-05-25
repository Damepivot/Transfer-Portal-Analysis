"""
Load real Kaggle data into PostgreSQL.
  - Transfer records: collegeBasketBallTransferMen.csv (2016-2022)
  - Team stats:       cbb21.csv through cbb25.csv (wins, ADJOE, ADJDE)
  - Player stats:     estimated from team ADJOE + player recruiting rating
  - Real player stats override: data/raw/cbb_player_stats.csv (run scrape_cbb_reference.py first)

Run: python etl/load_real_data.py
"""

import random
import sys
import psycopg2
import pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from db import DB_CONFIG

random.seed(99)

DATA_DIR = Path(__file__).parent.parent / "data" / "raw"

# CBB Reference conf abbreviation → our DB abbreviation
CONF_MAP = {
    "ACC": "ACC", "B10": "Big Ten", "B12": "Big 12", "SEC": "SEC",
    "BE":  "Big East", "P12": "Pac-12", "WCC": "WCC", "MWC": "MWC",
    "Amer":"AAC", "A10": "A-10", "MVC": "MVC", "MAC": "MAC",
    "CUSA":"CUSA", "SB":  "Sun Belt", "CAA": "CAA", "Horz":"Horizon",
    "BW":  "Big West", "SC":  "SoCon", "BSth":"Big South", "NEC": "NEC",
    "OVC": "OVC", "SWAC":"SWAC", "MEAC":"MEAC", "Pat": "Patriot",
    "AE":  "America East", "WAC": "WAC", "Ivy": "Ivy", "ASun":"ASUN",
    "MAAC":"MAAC", "Slnd":"Southland", "Sum": "Summit", "AmEast":"America East",
    "GWC": "WAC", "Ind": "Independent",
}

POSITION_MAP = {
    "PG": "G", "SG": "G", "G": "G", "PG/SG": "G", "SG/PG": "G",
    "SF": "F", "PF": "F", "F": "F", "SF/PF": "F", "PF/SF": "F",
    "C":  "C", "PF/C": "F/C", "C/PF": "F/C",
    "SG/SF": "G/F", "SF/SG": "G/F", "G/F": "G/F",
}

YEAR_TO_SEASON = {
    2019: "2019-20", 2020: "2020-21",
    2021: "2021-22", 2022: "2022-23",
}

CBB_YEAR_TO_SEASON = {
    2021: "2021-22", 2022: "2022-23",
    2023: "2023-24", 2024: "2024-25", 2025: "2024-25",
}

CBB_FILE_TO_SEASON = {
    "cbb21.csv": "2020-21", "cbb22.csv": "2021-22",
    "cbb23.csv": "2022-23", "cbb24.csv": "2023-24",
    "cbb25.csv": "2024-25", "cbb26.csv": "2025-26",
}

# On3 portal year → season the player transfers INTO
ON3_YEAR_TO_SEASON = {
    2023: "2023-24",
    2024: "2024-25",
    2025: "2025-26",
}

ON3_POSITION_MAP = {
    "PG": "G", "SG": "G", "CG": "G", "G": "G",
    "SF": "F", "PF": "F", "F": "F",
    "C":  "C",
    "SG/SF": "G/F", "SF/SG": "G/F", "PG/SG": "G",
    "PF/C": "F/C", "C/PF": "F/C",
}


def parse_height_in(height_str) -> int | None:
    """Convert On3 height string '6-4' or '6-04' to total inches."""
    try:
        parts = str(height_str).strip().split("-")
        if len(parts) == 2:
            return int(parts[0]) * 12 + int(parts[1])
    except (ValueError, TypeError):
        pass
    return None


def rnd(lo, hi, decimals=2):
    return round(random.uniform(lo, hi), decimals)


def estimate_player_stats(adjoe: float, player_rate: float, position: str,
                          bpm_before: float | None = None) -> dict:
    """
    Estimate individual player stats from team offensive efficiency and player rating.
    adjoe:       team adjusted offensive efficiency (avg ~100, range 85-130)
    player_rate: 0-1 recruiting composite (0.80 = borderline D1, 0.95 = elite)
    bpm_before:  player's BPM at prior school; when provided, 60% weight given to
                 the player's track record so upward transfers don't get inflated stats
                 just from landing on a good team.
    """
    team_quality = (adjoe - 100) / 15        # normalised: 0 = avg, 1 = great
    player_quality = (player_rate - 0.85) * 10 if player_rate else 0
    team_bpm = team_quality * 2 + player_quality

    if bpm_before is not None:
        # Estimated post-transfer BPM is purely the player's own track record,
        # regressed 65% toward mean. Team quality is intentionally excluded here
        # because it already lives in context_score — including it twice inflates
        # verdicts for upward transfers regardless of actual individual performance.
        bpm = round(bpm_before * 0.65 + rnd(-0.8, 0.8), 2)
    else:
        bpm = round(team_bpm + rnd(-1.5, 1.5), 2)
    ppg = round(max(2.0, 10 + team_quality * 3 + player_quality * 0.8 + rnd(-3, 3)), 1)

    return {
        "games":         random.randint(22, 35),
        "games_started": random.randint(5, 30),
        "mpg":           rnd(14, 34),
        "ppg":           ppg,
        "rpg":           rnd(1.5, 8.5),
        "apg":           rnd(0.5, 6.0),
        "spg":           rnd(0.3, 1.8),
        "bpg":           rnd(0.1, 2.0),
        "fg_pct":        rnd(0.38, 0.55),
        "three_pct":     rnd(0.28, 0.43),
        "ft_pct":        rnd(0.62, 0.85),
        "ts_pct":        rnd(0.49, 0.63),
        "efg_pct":       rnd(0.44, 0.60),
        "usg_pct":       rnd(14.0, 30.0),
        "bpm":           bpm,
        "porpag":        round(bpm * 0.7 + rnd(-0.3, 0.3), 2),
    }


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()

    # ── Wipe synthetic data, keep conferences ─────────────────────────────────
    print("Clearing synthetic data...")
    cur.execute("TRUNCATE transfers, player_seasons, team_seasons, players, teams RESTART IDENTITY CASCADE")
    conn.commit()

    # ── Load conference lookup ────────────────────────────────────────────────
    cur.execute("SELECT name, abbreviation, conference_id FROM conferences")
    rows = cur.fetchall()
    conf_by_abbr = {abbr: cid for _, abbr, cid in rows}
    conf_by_name = {name: cid for name, _, cid in rows}

    def get_conf_id(raw_abbr: str):
        mapped = CONF_MAP.get(raw_abbr, raw_abbr)
        return conf_by_abbr.get(mapped) or conf_by_name.get(mapped)

    # ── Load CBB team stats → teams + team_seasons ────────────────────────────
    print("Loading team stats from CBB CSVs...")
    team_id_map   = {}   # (canonical_name, season) → team_id
    team_adjoe    = {}   # (canonical_name, season) → ADJOE

    for fname, season in CBB_FILE_TO_SEASON.items():
        fpath = DATA_DIR / fname
        if not fpath.exists():
            continue
        df = pd.read_csv(fpath)
        df.columns = [c.strip().upper() for c in df.columns]

        for _, row in df.iterrows():
            team_name = str(row["TEAM"]).strip()
            conf_raw  = str(row.get("CONF", "")).strip()
            wins      = int(row.get("W", 0)) if pd.notna(row.get("W")) else 0
            adjoe     = float(row.get("ADJOE", 100)) if pd.notna(row.get("ADJOE")) else 100.0
            adjde     = float(row.get("ADJDE", 100)) if pd.notna(row.get("ADJDE")) else 100.0
            adj_eff   = round(adjoe - adjde, 2)

            conf_id = get_conf_id(conf_raw)

            # Insert team row
            cur.execute(
                "INSERT INTO teams (name, conference_id, season) VALUES (%s,%s,%s) RETURNING team_id",
                (team_name, conf_id, season)
            )
            tid = cur.fetchone()[0]
            team_id_map[(team_name, season)] = tid
            team_adjoe[(team_name, season)]  = adjoe

            # Insert team_season
            cur.execute(
                """INSERT INTO team_seasons (team_id, season, wins, adj_efficiency)
                   VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (tid, season, wins, adj_eff)
            )

    conn.commit()
    print(f"  {len(team_id_map)} team-season rows loaded")

    # ── Fuzzy team name matcher ───────────────────────────────────────────────
    all_team_names = list({name for name, _ in team_id_map.keys()})

    # Manual overrides: portal name → CBB name
    SCHOOL_NAME_MAP = {
        "Alabama State":          "Alabama St.",
        "Alcorn State":           "Alcorn St.",
        "Appalachian State":      "Appalachian St.",
        "Arizona State":          "Arizona St.",
        "Arkansas State":         "Arkansas St.",
        "Arkansas-Little Rock":   "Little Rock",
        "Ball State":             "Ball St.",
        "Bethune-Cookman":        "Bethune-Cookman",
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
        "North Carolina State":   "NC State",
        "NC State":               "NC State",
        "North Dakota State":     "North Dakota St.",
        "Ohio State":             "Ohio St.",
        "Oklahoma State":         "Oklahoma St.",
        "Oregon State":           "Oregon St.",
        "Penn State":             "Penn St.",
        "Portland State":         "Portland St.",
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
        "UCF":                    "Central Florida",
        "UTEP":                   "Texas El Paso",
        "UTSA":                   "UT San Antonio",
        "VCU":                    "Virginia Commonwealth",
        "SMU":                    "Southern Methodist",
        "TCU":                    "Texas Christian",
        "BYU":                    "Brigham Young",
        "LSU":                    "Louisiana State",
        "Ole Miss":               "Mississippi",
        "Miami (FL)":             "Miami FL",
        "Miami (Ohio)":           "Miami OH",
        "Loyola (Chi) Ramblers":  "Loyola Chicago",
        "Loyola (Chi)":           "Loyola Chicago",
        "Loyola-Chicago":         "Loyola Chicago",
    }

    def normalize_school(raw: str) -> str:
        raw = str(raw).strip()
        return SCHOOL_NAME_MAP.get(raw, raw)

    def match_team(raw: str, season: str):
        """Return team_id for raw school name + season, with fuzzy fallback."""
        if not raw or str(raw).strip().lower() in ("nan", "none", ""):
            return None
        normalized = normalize_school(str(raw).strip())
        if (normalized, season) in team_id_map:
            return team_id_map[(normalized, season)]
        # Case-insensitive exact
        lower = normalized.lower()
        for name in all_team_names:
            if name.lower() == lower:
                return team_id_map.get((name, season))
        # Partial match
        for name in all_team_names:
            if lower in name.lower() or name.lower() in lower:
                return team_id_map.get((name, season))
        return None

    # ── Load transfer portal CSV ──────────────────────────────────────────────
    print("Loading transfer portal CSV...")
    portal_path = DATA_DIR / "collegeBasketBallTransferMen.csv"
    portal_df   = pd.read_csv(portal_path)

    # Filter to seasons we have team data for
    portal_df = portal_df[portal_df["Year"].isin(YEAR_TO_SEASON.keys())].copy()
    portal_df["season"] = portal_df["Year"].map(YEAR_TO_SEASON)
    print(f"  {len(portal_df)} transfers after season filter")

    # Parse recruiting composite (0-1 scale → 0-100)
    def parse_rate(val):
        try:
            return round(float(str(val).strip().strip("()").strip()) * 100, 1)
        except (ValueError, TypeError):
            return None

    portal_df["composite"] = portal_df["Rate"].apply(parse_rate)
    portal_df["pos_clean"] = portal_df["Position"].apply(
        lambda x: POSITION_MAP.get(str(x).strip().upper(), "G")
    )

    # ── Insert players ────────────────────────────────────────────────────────
    print("Inserting players...")
    player_id_map = {}  # name → player_id
    for _, row in portal_df.iterrows():
        name = str(row["Name"]).strip().title()
        if name in player_id_map:
            continue
        cur.execute(
            """INSERT INTO players (full_name, position, class_year, recruiting_composite)
               VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING player_id""",
            (name, row["pos_clean"], str(row.get("Eligibility", "")).strip()[:10] or None, row["composite"])
        )
        result = cur.fetchone()
        if result:
            player_id_map[name] = result[0]

    # Fetch any that were skipped by ON CONFLICT
    cur.execute("SELECT player_id, full_name FROM players")
    for pid, pname in cur.fetchall():
        player_id_map[pname] = pid

    conn.commit()
    print(f"  {len(player_id_map)} players")

    # ── Insert transfers + player_seasons ────────────────────────────────────
    print("Inserting transfers and player seasons...")
    inserted_transfers = skipped = 0

    for _, row in portal_df.iterrows():
        season     = row["season"]
        name       = str(row["Name"]).strip().title()
        from_name  = str(row.get("PreCollege", "")).strip()
        to_name    = str(row.get("AftCollege", "")).strip()

        player_id  = player_id_map.get(name)
        from_tid   = match_team(from_name, season)
        to_tid     = match_team(to_name, season)

        if not player_id or not from_tid or not to_tid or from_tid == to_tid:
            skipped += 1
            continue

        cur.execute(
            """INSERT INTO transfers (player_id, from_team_id, to_team_id, season, transfer_type)
               VALUES (%s,%s,%s,%s,'portal') ON CONFLICT DO NOTHING""",
            (player_id, from_tid, to_tid, season)
        )
        inserted_transfers += cur.rowcount

        # Player season BEFORE transfer (at from_school, prev season)
        prev_seasons = [s for s in CBB_FILE_TO_SEASON.values() if s < season]
        if prev_seasons:
            prev_season = max(prev_seasons)
            prev_tid    = match_team(from_name, prev_season)
            if prev_tid:
                adjoe_prev = team_adjoe.get((from_name, prev_season), 100.0)
                composite  = row["composite"] / 100 if pd.notna(row["composite"]) else 0.85
                stats_before = estimate_player_stats(adjoe_prev, composite, row["pos_clean"])
                cur.execute(
                    """INSERT INTO player_seasons
                       (player_id,team_id,season,games,games_started,mpg,ppg,rpg,apg,spg,bpg,
                        fg_pct,three_pct,ft_pct,ts_pct,efg_pct,usg_pct,bpm,porpag)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (player_id,team_id,season) DO NOTHING""",
                    (player_id, prev_tid, prev_season,
                     stats_before["games"], stats_before["games_started"], stats_before["mpg"],
                     stats_before["ppg"], stats_before["rpg"], stats_before["apg"],
                     stats_before["spg"], stats_before["bpg"], stats_before["fg_pct"],
                     stats_before["three_pct"], stats_before["ft_pct"], stats_before["ts_pct"],
                     stats_before["efg_pct"], stats_before["usg_pct"],
                     stats_before["bpm"], stats_before["porpag"])
                )

        # Player season AFTER transfer (at to_school, transfer season)
        adjoe_after = team_adjoe.get((to_name, season), 100.0)
        composite   = row["composite"] / 100 if pd.notna(row["composite"]) else 0.85
        prior_bpm   = stats_before["bpm"] if prev_seasons and prev_tid else None
        stats_after = estimate_player_stats(adjoe_after, composite, row["pos_clean"], bpm_before=prior_bpm)
        cur.execute(
            """INSERT INTO player_seasons
               (player_id,team_id,season,games,games_started,mpg,ppg,rpg,apg,spg,bpg,
                fg_pct,three_pct,ft_pct,ts_pct,efg_pct,usg_pct,bpm,porpag)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (player_id,team_id,season) DO NOTHING""",
            (player_id, to_tid, season,
             stats_after["games"], stats_after["games_started"], stats_after["mpg"],
             stats_after["ppg"], stats_after["rpg"], stats_after["apg"],
             stats_after["spg"], stats_after["bpg"], stats_after["fg_pct"],
             stats_after["three_pct"], stats_after["ft_pct"], stats_after["ts_pct"],
             stats_after["efg_pct"], stats_after["usg_pct"],
             stats_after["bpm"], stats_after["porpag"])
        )

    conn.commit()
    print(f"  {inserted_transfers} transfers inserted, {skipped} skipped (unmatched schools)")

    # ── Load On3 transfers (2023-2025) ────────────────────────────────────────
    on3_path = DATA_DIR / "on3_transfers_combined.csv"
    if on3_path.exists():
        print("Loading On3 transfers (2023-2025)...")
        on3_df = pd.read_csv(on3_path)
        on3_df = on3_df[on3_df["committed"] == True].copy()
        on3_df = on3_df[on3_df["year"].isin(ON3_YEAR_TO_SEASON.keys())].copy()
        on3_df["season"]   = on3_df["year"].map(ON3_YEAR_TO_SEASON)
        on3_df["pos_clean"]= on3_df["position"].apply(
            lambda x: ON3_POSITION_MAP.get(str(x).strip().upper(), "G")
        )
        print(f"  {len(on3_df)} committed On3 transfers to load")

        on3_inserted = on3_skipped = 0
        for _, row in on3_df.iterrows():
            season    = row["season"]
            name      = str(row["player_name"]).strip().title()
            from_name = str(row["from_school"]).strip()
            to_name   = str(row["to_school"]).strip()

            if not from_name or not to_name or to_name.lower() == "nan":
                on3_skipped += 1
                continue

            # Insert player if new
            if name not in player_id_map:
                composite  = row["recruiting_composite"] if pd.notna(row.get("recruiting_composite")) else None
                height_in  = parse_height_in(row.get("height")) if pd.notna(row.get("height", None) or float("nan")) else None
                weight_lbs = int(row["weight"]) if pd.notna(row.get("weight")) and row.get("weight") else None
                birth_year = int(row["birth_year"]) if pd.notna(row.get("birth_year")) and row.get("birth_year") else None
                cur.execute(
                    """INSERT INTO players (full_name, position, class_year, recruiting_composite, height_in, weight_lbs, birth_year)
                       VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING player_id""",
                    (name, row["pos_clean"], str(row.get("class_year", ""))[:10] or None, composite, height_in, weight_lbs, birth_year)
                )
                result = cur.fetchone()
                if result:
                    player_id_map[name] = result[0]
                else:
                    cur.execute("SELECT player_id FROM players WHERE full_name=%s", (name,))
                    r = cur.fetchone()
                    if r:
                        player_id_map[name] = r[0]

            player_id = player_id_map.get(name)
            from_tid  = match_team(from_name, season)
            to_tid    = match_team(to_name, season)

            if not player_id or not from_tid or not to_tid or from_tid == to_tid:
                on3_skipped += 1
                continue

            cur.execute(
                """INSERT INTO transfers (player_id, from_team_id, to_team_id, season, transfer_type)
                   VALUES (%s,%s,%s,%s,'portal') ON CONFLICT DO NOTHING""",
                (player_id, from_tid, to_tid, season)
            )
            on3_inserted += cur.rowcount

            # Before season player stats
            composite_rate = row["recruiting_composite"] / 100 if pd.notna(row.get("recruiting_composite")) else 0.85
            prev_seasons = [s for s in CBB_FILE_TO_SEASON.values() if s < season]
            if prev_seasons:
                prev_season = max(prev_seasons)
                prev_tid    = match_team(from_name, prev_season)
                if prev_tid:
                    adjoe_prev = team_adjoe.get((from_name, prev_season), 100.0)
                    stats_before = estimate_player_stats(adjoe_prev, composite_rate, row["pos_clean"])
                    cur.execute(
                        """INSERT INTO player_seasons
                           (player_id,team_id,season,games,games_started,mpg,ppg,rpg,apg,spg,bpg,
                            fg_pct,three_pct,ft_pct,ts_pct,efg_pct,usg_pct,bpm,porpag)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (player_id,team_id,season) DO NOTHING""",
                        (player_id, prev_tid, prev_season,
                         stats_before["games"], stats_before["games_started"], stats_before["mpg"],
                         stats_before["ppg"], stats_before["rpg"], stats_before["apg"],
                         stats_before["spg"], stats_before["bpg"], stats_before["fg_pct"],
                         stats_before["three_pct"], stats_before["ft_pct"], stats_before["ts_pct"],
                         stats_before["efg_pct"], stats_before["usg_pct"],
                         stats_before["bpm"], stats_before["porpag"])
                    )

            # After season player stats
            adjoe_after  = team_adjoe.get((to_name, season), 100.0)
            prior_bpm    = stats_before["bpm"] if prev_seasons and prev_tid else None
            stats_after  = estimate_player_stats(adjoe_after, composite_rate, row["pos_clean"], bpm_before=prior_bpm)
            cur.execute(
                """INSERT INTO player_seasons
                   (player_id,team_id,season,games,games_started,mpg,ppg,rpg,apg,spg,bpg,
                    fg_pct,three_pct,ft_pct,ts_pct,efg_pct,usg_pct,bpm,porpag)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (player_id,team_id,season) DO NOTHING""",
                (player_id, to_tid, season,
                 stats_after["games"], stats_after["games_started"], stats_after["mpg"],
                 stats_after["ppg"], stats_after["rpg"], stats_after["apg"],
                 stats_after["spg"], stats_after["bpg"], stats_after["fg_pct"],
                 stats_after["three_pct"], stats_after["ft_pct"], stats_after["ts_pct"],
                 stats_after["efg_pct"], stats_after["usg_pct"],
                 stats_after["bpm"], stats_after["porpag"])
            )

        conn.commit()
        print(f"  {on3_inserted} On3 transfers inserted, {on3_skipped} skipped")
    else:
        print("No On3 data found — run etl/scrape_on3.py first")

    # ── Override estimated stats with real CBB Reference player stats ─────────
    cbb_stats_path = DATA_DIR / "cbb_player_stats.csv"
    if cbb_stats_path.exists():
        print("Applying real CBB Reference player stats...")
        cbb_df = pd.read_csv(cbb_stats_path)

        # Build lowercase name → player_id lookup
        cur.execute("SELECT player_id, LOWER(full_name) FROM players")
        name_to_pid = {name: pid for pid, name in cur.fetchall()}

        def parse_stat(val):
            try:
                f = float(val)
                return None if pd.isna(f) else f
            except (ValueError, TypeError):
                return None

        updated = skipped_real = 0
        for _, row in cbb_df.iterrows():
            player_name = str(row["Player"]).strip()
            school      = str(row["school"]).strip()
            season      = str(row["season"]).strip()

            pid = name_to_pid.get(player_name.lower())
            if not pid:
                skipped_real += 1
                continue

            tid = match_team(school, season)
            if not tid:
                skipped_real += 1
                continue

            bpm     = parse_stat(row.get("BPM"))
            obpm    = parse_stat(row.get("OBPM"))
            dbpm    = parse_stat(row.get("DBPM"))
            ts_pct  = parse_stat(row.get("TS%"))
            usg_pct = parse_stat(row.get("USG%"))

            # Sanity cap — real college BPM physically cannot exceed ±15
            if bpm is None or abs(bpm) > 15:
                skipped_real += 1
                continue
            if obpm is not None and abs(obpm) > 15:
                obpm = None
            if dbpm is not None and abs(dbpm) > 15:
                dbpm = None

            cur.execute(
                """UPDATE player_seasons
                   SET bpm    = %s,
                       obpm   = COALESCE(%s, obpm),
                       dbpm   = COALESCE(%s, dbpm),
                       ts_pct  = COALESCE(%s, ts_pct),
                       usg_pct = COALESCE(%s, usg_pct)
                   WHERE player_id = %s AND team_id = %s AND season = %s""",
                (bpm, obpm, dbpm, ts_pct, usg_pct, pid, tid, season)
            )
            updated += cur.rowcount

        conn.commit()
        print(f"  {updated} player-season rows updated with real stats ({skipped_real} unmatched)")
    else:
        print("No CBB Reference stats found — using estimates (run etl/scrape_cbb_reference.py to improve accuracy)")

    # ── Refresh materialized view ─────────────────────────────────────────────
    print("Refreshing tier_pair_expectations...")
    cur.execute("REFRESH MATERIALIZED VIEW tier_pair_expectations")
    conn.commit()

    # ── Summary ───────────────────────────────────────────────────────────────
    cur.execute("SELECT COUNT(*) FROM transfers")
    print(f"\nFinal counts:")
    print(f"  Transfers:      {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM player_seasons")
    print(f"  Player seasons: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM individual_transfer_scores")
    print(f"  Scored transfers: {cur.fetchone()[0]}")
    cur.execute("SELECT transfer_verdict, COUNT(*) FROM individual_transfer_scores GROUP BY 1")
    for verdict, count in cur.fetchall():
        print(f"    {verdict}: {count}")

    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
