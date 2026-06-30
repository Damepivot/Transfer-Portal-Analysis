"""
Master ETL script — loads all portal data into PostgreSQL from scratch.

Sources loaded (in order):
  1. CBB team stats     cbb21.csv – cbb26.csv → teams + team_seasons
  2. Kaggle portal      collegeBasketBallTransferMen.csv (2019-2022) → players + transfers
  3. On3 full portal    on3_transfers_full.csv (2022-2025, all entrants) → players + transfers
  4. International      data/international_transfers.csv → players + transfers (intl tier)
  5. CBB player stats   cbb_player_stats.csv → player_seasons UPSERT (real BPM only)

School name normalization:
  On3 stores names like "Iowa Hawkeyes" or "Louisiana State"; CBB Reference uses "Iowa",
  "Louisiana Lafayette". SCHOOL_NAME_MAP handles both mascot-stripped and abbreviated forms.
  match_team() does an exact lookup first, then falls back to case-insensitive and partial match.
  Sub-D1 schools (JUCO, D2, D3) that fail match_team get routed to get_or_create_sub_d1_team().
  International origins always go through get_or_create_intl_team().

No estimation:
  All BPM / TS% / USG% come exclusively from CBB Reference (scrape_cbb_reference.py).
  Player seasons created before the stat UPSERT are empty placeholders; they only get real
  numbers once the CBB stat pass finds a matching player name + school + season row.

Run: python3 etl/load_real_data.py
"""

import sys
import psycopg2
import pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from db import DB_CONFIG

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
    2022: "2022-23",
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

    sub_d1_conf_id       = conf_by_name.get("Sub-D1")       # JUCO/D2/D3/NAIA domestic origins
    intl_conf_id         = conf_by_name.get("International") # foreign professional league origins
    sub_d1_team_cache:   dict[str, int] = {}
    intl_team_cache:     dict[str, int] = {}

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


    # Normalize CBB file naming inconsistencies (e.g. cbb26 uses different abbreviations)
    CBB_CANONICAL = {
        "N.C. State":  "North Carolina St.",
        "La Tech":     "Louisiana Tech",
    }
    for (name, season), tid in list(team_id_map.items()):
        canonical = CBB_CANONICAL.get(name)
        if canonical:
            team_id_map[(canonical, season)] = tid

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
        # CBB CSV uses these specific spellings
        "NC State":               "North Carolina St.",
        "North Carolina State":   "North Carolina St.",
        "USF":                    "South Florida",
        "Louisiana-Monroe":       "Louisiana Monroe",
        "Louisiana":              "Louisiana Lafayette",
        "Gardner-Webb":           "Gardner Webb",
        "San Jose State":         "San Jose St.",
        "UT Martin":              "Tennessee Martin",
        "Nicholls State":         "Nicholls St.",
        # On3 team page names include mascot suffix — strip to school name
        "Ole Miss Rebels":        "Mississippi",
        "Miami Hurricanes":       "Miami FL",
        "Miami (FL) Hurricanes":  "Miami FL",
        "Cleveland State University Vikings": "Cleveland St.",
        "Chicago State Cougars":  "Chicago St.",
        "Bethune-Cookman Wildcats": "Bethune Cookman",
        "Boise State Broncos":    "Boise St.",
        "Kennesaw State Owls":    "Kennesaw St.",
        "NC State Wolfpack":      "North Carolina St.",
        "USF Bulls":              "South Florida",
        "UTEP Miners":            "Texas El Paso",
        "UTSA Roadrunners":       "UTSA",
        "VCU Rams":               "VCU",
        "SMU Mustangs":           "SMU",
        "TCU Horned Frogs":       "TCU",
        "BYU Cougars":            "BYU",
        "Brigham Young":          "BYU",
        "Detroit Mercy":          "Detroit",
        "Detroit Mercy Titans":   "Detroit",
        "LSU Tigers":             "LSU",
        "UCF Knights":            "UCF",
        "UConn Huskies":          "Connecticut",
        "UAB Blazers":            "UAB",
        "UNC Tar Heels":          "North Carolina",
        "Pitt Panthers":          "Pittsburgh",
        "Gonzaga University Bulldogs": "Gonzaga",
        "Saint Mary's College of California Gaels": "Saint Mary's CA",
        "Loyola Chicago Ramblers": "Loyola Chicago",
        "Belmont University Bruins": "Belmont",
        "Binghamton University Bearcats": "Binghamton",
        "Bryant University Bulldogs": "Bryant",
        "Detroit Mercy Titans":   "Detroit",
        "Gardner-Webb Bulldogs":  "Gardner Webb",
        "Fort Lewis College Skyhawks": "Fort Lewis College",  # sub_d1 — handled downstream
        "Robert Morris Colonials": "Robert Morris",
        "Jacksonville Dolphins":  "Jacksonville",
        "Eastern Michigan Eagles": "Eastern Michigan",
        "Western Kentucky Hilltoppers": "Western Kentucky",
        "Texas Tech Red Raiders": "Texas Tech",
        "Minnesota Golden Gophers": "Minnesota",
        "Providence Friars":      "Providence",
        "Oakland Golden Grizzlies": "Oakland",
        "Arizona Wildcats":       "Arizona",
        "Arizona State Sun Devils": "Arizona St.",
        "Arkansas Razorbacks":    "Arkansas",
        "Auburn Tigers":          "Auburn",
        "California Golden Bears": "California",
        "Clemson Tigers":         "Clemson",
        "Connecticut Huskies":    "Connecticut",
        "Creighton Bluejays":     "Creighton",
        "DePaul Blue Demons":     "DePaul",
        "Georgia Bulldogs":       "Georgia",
        "Georgetown Hoyas":       "Georgetown",
        "Indiana Hoosiers":       "Indiana",
        "Iowa Hawkeyes":          "Iowa",
        "Kansas Jayhawks":        "Kansas",
        "Kentucky Wildcats":      "Kentucky",
        "Louisville Cardinals":   "Louisville",
        "Marquette Golden Eagles": "Marquette",
        "Maryland Terrapins":     "Maryland",
        "Michigan Wolverines":    "Michigan",
        "Michigan State Spartans": "Michigan St.",
        "Minnesota Golden Gophers": "Minnesota",
        "Missouri Tigers":        "Missouri",
        "Nebraska Cornhuskers":   "Nebraska",
        "North Carolina Tar Heels": "North Carolina",
        "Notre Dame Fighting Irish": "Notre Dame",
        "Ohio State Buckeyes":    "Ohio St.",
        "Oklahoma Sooners":       "Oklahoma",
        "Oklahoma State Cowboys": "Oklahoma St.",
        "Oregon Ducks":           "Oregon",
        "Oregon State Beavers":   "Oregon St.",
        "Penn State Nittany Lions": "Penn St.",
        "Purdue Boilermakers":    "Purdue",
        "Rutgers Scarlet Knights": "Rutgers",
        "Stanford Cardinal":      "Stanford",
        "Syracuse Orange":        "Syracuse",
        "Tennessee Volunteers":   "Tennessee",
        "Texas Longhorns":        "Texas",
        "Texas A&M Aggies":       "Texas A&M",
        "UCLA Bruins":            "UCLA",
        "USC Trojans":            "USC",
        "Utah Utes":              "Utah",
        "Vanderbilt Commodores":  "Vanderbilt",
        "Virginia Cavaliers":     "Virginia",
        "Virginia Tech Hokies":   "Virginia Tech",
        "Washington Huskies":     "Washington",
        "Washington State Cougars": "Washington St.",
        "West Virginia Mountaineers": "West Virginia",
        "Wisconsin Badgers":      "Wisconsin",
        "Xavier Musketeers":      "Xavier",
        "Seton Hall Pirates":     "Seton Hall",
        "St. John's Red Storm":   "St. John's",
        "Florida Gators":         "Florida",
        "Florida State Seminoles": "Florida St.",
        "Georgia Tech Yellow Jackets": "Georgia Tech",
        "Iowa State Cyclones":    "Iowa St.",
        "Kansas State Wildcats":  "Kansas St.",
        "Mississippi State Bulldogs": "Mississippi St.",
        "Texas A&M Aggies":       "Texas A&M",
        "Wake Forest Demon Deacons": "Wake Forest",
        "Duke Blue Devils":       "Duke",
        "Villanova Wildcats":     "Villanova",
        # Mascot-suffixed from_school values from On3 team page scraper
        "LSU Tigers":             "LSU",
        "SMU Mustangs":           "SMU",
        "VCU Rams":               "VCU",
        "TCU Horned Frogs":       "TCU",
        "UNLV Rebels":            "UNLV",
        "Utah State Aggies":      "Utah St.",
        "Iowa State Cyclones":    "Iowa St.",
        "Iowa Hawkeyes":          "Iowa",
        "Ohio State Buckeyes":    "Ohio St.",
        "Penn State Nittany Lions": "Penn St.",
        "Ole Miss Rebels":        "Mississippi",
        "Miami Hurricanes":       "Miami FL",
        "Cleveland State University Vikings": "Cleveland St.",
        "Chicago State Cougars":  "Chicago St.",
        "Bethune-Cookman Wildcats": "Bethune Cookman",
        "Boise State Broncos":    "Boise St.",
        "NC State Wolfpack":      "North Carolina St.",
        "Michigan Wolverines":    "Michigan",
        "Louisville Cardinals":   "Louisville",
        "Texas Tech Red Raiders": "Texas Tech",
        "Arizona Wildcats":       "Arizona",
        "Arizona State Sun Devils": "Arizona St.",
        "Arkansas Razorbacks":    "Arkansas",
        "Auburn Tigers":          "Auburn",
        "California Golden Bears": "California",
        "Clemson Tigers":         "Clemson",
        "Connecticut Huskies":    "Connecticut",
        "Creighton Bluejays":     "Creighton",
        "DePaul Blue Demons":     "DePaul",
        "Georgia Bulldogs":       "Georgia",
        "Georgetown Hoyas":       "Georgetown",
        "Indiana Hoosiers":       "Indiana",
        "Kansas Jayhawks":        "Kansas",
        "Kentucky Wildcats":      "Kentucky",
        "Marquette Golden Eagles": "Marquette",
        "Maryland Terrapins":     "Maryland",
        "Michigan State Spartans": "Michigan St.",
        "Minnesota Golden Gophers": "Minnesota",
        "Missouri Tigers":        "Missouri",
        "Nebraska Cornhuskers":   "Nebraska",
        "North Carolina Tar Heels": "North Carolina",
        "Notre Dame Fighting Irish": "Notre Dame",
        "Oklahoma Sooners":       "Oklahoma",
        "Oklahoma State Cowboys": "Oklahoma St.",
        "Oregon Ducks":           "Oregon",
        "Oregon State Beavers":   "Oregon St.",
        "Purdue Boilermakers":    "Purdue",
        "Rutgers Scarlet Knights": "Rutgers",
        "Stanford Cardinal":      "Stanford",
        "Syracuse Orange":        "Syracuse",
        "Tennessee Volunteers":   "Tennessee",
        "Texas Longhorns":        "Texas",
        "Texas A&M Aggies":       "Texas A&M",
        "UCLA Bruins":            "UCLA",
        "USC Trojans":            "USC",
        "Utah Utes":              "Utah",
        "Vanderbilt Commodores":  "Vanderbilt",
        "Virginia Cavaliers":     "Virginia",
        "Virginia Tech Hokies":   "Virginia Tech",
        "Washington Huskies":     "Washington",
        "Washington State Cougars": "Washington St.",
        "West Virginia Mountaineers": "West Virginia",
        "Wisconsin Badgers":      "Wisconsin",
        "Xavier Musketeers":      "Xavier",
        "Seton Hall Pirates":     "Seton Hall",
        "St. John's Red Storm":   "St. John's",
        "Florida Gators":         "Florida",
        "Florida State Seminoles": "Florida St.",
        "Georgia Tech Yellow Jackets": "Georgia Tech",
        "Kansas State Wildcats":  "Kansas St.",
        "Mississippi State Bulldogs": "Mississippi St.",
        "Wake Forest Demon Deacons": "Wake Forest",
        "Duke Blue Devils":       "Duke",
        "Villanova Wildcats":     "Villanova",
        "Gonzaga University Bulldogs": "Gonzaga",
        "Gonzaga Bulldogs":       "Gonzaga",
        "Saint Mary's College of California Gaels": "Saint Mary's CA",
        "Saint Mary's Gaels":     "Saint Mary's CA",
        "Loyola Chicago Ramblers": "Loyola Chicago",
        "Belmont University Bruins": "Belmont",
        "Binghamton University Bearcats": "Binghamton",
        "Bryant University Bulldogs": "Bryant",
        "Detroit Mercy Titans":   "Detroit",
        "Gardner-Webb Bulldogs":  "Gardner Webb",
        "Robert Morris Colonials": "Robert Morris",
        "Jacksonville Dolphins":  "Jacksonville",
        "Eastern Michigan Eagles": "Eastern Michigan",
        "Western Kentucky Hilltoppers": "Western Kentucky",
        "UAB Blazers":            "UAB",
        "UCF Knights":            "UCF",
        "UConn Huskies":          "Connecticut",
        "UNC Tar Heels":          "North Carolina",
        "Pitt Panthers":          "Pittsburgh",
        "USF Bulls":              "South Florida",
        "UTEP Miners":            "Texas El Paso",
        "UTSA Roadrunners":       "UTSA",
        "BYU Cougars":            "BYU",
        "Brigham Young":          "BYU",
        "Detroit Mercy":          "Detroit",
        "Detroit Mercy Titans":   "Detroit",
        "Kennesaw State Owls":    "Kennesaw St.",
        "NC State":               "North Carolina St.",
        "Boise State":            "Boise St.",
        "Iowa State":             "Iowa St.",
        "Kennesaw State":         "Kennesaw St.",
        "Pennsylvania":           "Penn",
        "USF":                    "South Florida",
        "San Jose State":         "San Jose St.",
        "Cleveland State University": "Cleveland St.",
        "Tarleton State":         "Tarleton St.",
        "UT Martin":              "Tennessee Martin",
        "Nicholls State":         "Nicholls St.",
        "Gardner-Webb":           "Gardner Webb",
        "Long Island":            "Long Island University",
        # Remaining gaps found in on3_transfers_full.csv
        "USF":                    "South Florida",
        "San Jose State":         "San Jose St.",
        "Cleveland State University": "Cleveland St.",
        "Tarleton State":         "Tarleton St.",
        "Pennsylvania":           "Penn",
        "UT Martin":              "Tennessee Martin",
        "Nicholls State":         "Nicholls St.",
        "Gardner-Webb":           "Gardner Webb",
        "Long Island":            "Long Island University",
        # Non-D1 destinations — route to sub_d1 (handled via get_or_create_sub_d1_team fallback)
        # "Professional", "NBA G League", "Johnson C. Smith University",
        # "Midwestern State", "Winston-Salem State", "Garden City Community College"
        # These are intentionally left out — match_team will return None and
        # get_or_create_sub_d1_team handles them for from_school; for to_school
        # we skip non-D1 destinations since we can't get CBB stats there.
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

    def get_or_create_sub_d1_team(raw: str) -> int | None:
        """Return (or create) a season-agnostic sub_d1 team for JUCO/D2/D3 schools."""
        if not raw or str(raw).strip().lower() in ("nan", "none", ""):
            return None
        name = str(raw).strip()
        if name in sub_d1_team_cache:
            return sub_d1_team_cache[name]
        cur.execute(
            "INSERT INTO teams (name, conference_id, season) VALUES (%s,%s,NULL)"
            " ON CONFLICT DO NOTHING RETURNING team_id",
            (name, sub_d1_conf_id)
        )
        row = cur.fetchone()
        if not row:
            cur.execute("SELECT team_id FROM teams WHERE name=%s AND season IS NULL LIMIT 1", (name,))
            row = cur.fetchone()
        if row:
            sub_d1_team_cache[name] = row[0]
            return row[0]
        return None

    def get_or_create_intl_team(raw: str) -> int | None:
        """Return (or create) a season-agnostic international team for foreign league origins."""
        if not raw or str(raw).strip().lower() in ("nan", "none", ""):
            return None
        name = str(raw).strip()
        if name in intl_team_cache:
            return intl_team_cache[name]
        cur.execute(
            "INSERT INTO teams (name, conference_id, season) VALUES (%s,%s,NULL)"
            " ON CONFLICT DO NOTHING RETURNING team_id",
            (name, intl_conf_id)
        )
        row = cur.fetchone()
        if not row:
            cur.execute("SELECT team_id FROM teams WHERE name=%s AND season IS NULL LIMIT 1", (name,))
            row = cur.fetchone()
        if row:
            intl_team_cache[name] = row[0]
            return row[0]
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
        to_tid     = match_team(to_name, season)
        # Try D1 match first; fall back to sub_d1 for JUCO/D2/D3 origins
        from_tid   = match_team(from_name, season) or get_or_create_sub_d1_team(from_name)

        if not player_id or not from_tid or not to_tid or from_tid == to_tid:
            skipped += 1
            continue

        cur.execute(
            """INSERT INTO transfers (player_id, from_team_id, to_team_id, season, transfer_type)
               VALUES (%s,%s,%s,%s,'portal') ON CONFLICT DO NOTHING""",
            (player_id, from_tid, to_tid, season)
        )
        inserted_transfers += cur.rowcount

        # Structural placeholders — BPM filled exclusively by CBB Reference UPSERT, no estimation
        # Only create ps_before placeholder for D1 from_schools (sub_d1 won't have CBB data)
        prev_seasons = [s for s in CBB_FILE_TO_SEASON.values() if s < season]
        if prev_seasons:
            prev_season = max(prev_seasons)
            prev_tid    = match_team(from_name, prev_season)
            if prev_tid:
                cur.execute(
                    "INSERT INTO player_seasons (player_id,team_id,season) VALUES (%s,%s,%s) ON CONFLICT (player_id,team_id,season) DO NOTHING",
                    (player_id, prev_tid, prev_season)
                )
        cur.execute(
            "INSERT INTO player_seasons (player_id,team_id,season) VALUES (%s,%s,%s) ON CONFLICT (player_id,team_id,season) DO NOTHING",
            (player_id, to_tid, season)
        )

    conn.commit()
    print(f"  {inserted_transfers} transfers inserted, {skipped} skipped (unmatched to_school)")

    # ── Load On3 transfers (2022-2025) ────────────────────────────────────────
    # Prefer the full team-page CSV (all portal entrants) over the top-50-only combined CSV.
    full_path = DATA_DIR / "on3_transfers_full.csv"
    on3_path  = full_path if full_path.exists() else DATA_DIR / "on3_transfers_combined.csv"
    if on3_path.exists():
        print(f"Loading On3 transfers from {on3_path.name}...")
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

            composite  = row["recruiting_composite"] if pd.notna(row.get("recruiting_composite")) else None
            height_in  = parse_height_in(row.get("height")) if pd.notna(row.get("height", None) or float("nan")) else None
            weight_lbs = int(row["weight"]) if pd.notna(row.get("weight")) and row.get("weight") else None
            birth_year = int(row["birth_year"]) if pd.notna(row.get("birth_year")) and row.get("birth_year") else None

            # Insert or update player — COALESCE ensures existing non-null values are never overwritten
            cur.execute(
                """INSERT INTO players (full_name, position, class_year, recruiting_composite, height_in, weight_lbs, birth_year)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (full_name) DO UPDATE SET
                       recruiting_composite = COALESCE(players.recruiting_composite, EXCLUDED.recruiting_composite),
                       height_in  = COALESCE(players.height_in,  EXCLUDED.height_in),
                       weight_lbs = COALESCE(players.weight_lbs, EXCLUDED.weight_lbs),
                       birth_year = COALESCE(players.birth_year, EXCLUDED.birth_year)
                   RETURNING player_id""",
                (name, row["pos_clean"], str(row.get("class_year", ""))[:10] or None, composite, height_in, weight_lbs, birth_year)
            )
            result = cur.fetchone()
            if result:
                player_id_map[name] = result[0]
            elif name not in player_id_map:
                cur.execute("SELECT player_id FROM players WHERE full_name=%s", (name,))
                r = cur.fetchone()
                if r:
                    player_id_map[name] = r[0]

            player_id = player_id_map.get(name)
            to_tid    = match_team(to_name, season)
            # Try D1 match first; fall back to sub_d1 for JUCO/D2/D3 origins
            from_tid  = match_team(from_name, season) or get_or_create_sub_d1_team(from_name)

            if not player_id or not from_tid or not to_tid or from_tid == to_tid:
                on3_skipped += 1
                continue

            cur.execute(
                """INSERT INTO transfers (player_id, from_team_id, to_team_id, season, transfer_type)
                   VALUES (%s,%s,%s,%s,'portal') ON CONFLICT DO NOTHING""",
                (player_id, from_tid, to_tid, season)
            )
            on3_inserted += cur.rowcount

            # Structural placeholders — only for D1 from_schools (sub_d1 has no CBB data)
            prev_seasons = [s for s in CBB_FILE_TO_SEASON.values() if s < season]
            if prev_seasons:
                prev_season = max(prev_seasons)
                prev_tid    = match_team(from_name, prev_season)
                if prev_tid:
                    cur.execute(
                        "INSERT INTO player_seasons (player_id,team_id,season) VALUES (%s,%s,%s) ON CONFLICT (player_id,team_id,season) DO NOTHING",
                        (player_id, prev_tid, prev_season)
                    )
            cur.execute(
                "INSERT INTO player_seasons (player_id,team_id,season) VALUES (%s,%s,%s) ON CONFLICT (player_id,team_id,season) DO NOTHING",
                (player_id, to_tid, season)
            )

        conn.commit()
        print(f"  {on3_inserted} On3 transfers inserted, {on3_skipped} skipped")
    else:
        print("No On3 data found — run etl/scrape_on3.py first")

    # ── Load Barttorvik transfers (ALL D1 portal entrants 2021-2026) ─────────────
    # Source: etl/scrape_barttorvik_transfers.py → data/raw/barttorvik_transfers.csv
    # Covers every portal entrant across all 364 D1 schools, not just rated players.
    # No player stats/height/weight here — only name + from/to school + season.
    # Add Barttorvik-specific abbreviations to the name map before matching
    SCHOOL_NAME_MAP.update({
        "A&M-CC":           "Texas A&M Corpus Christi",
        "SIU-E":            "SIU Edwardsville",
        "FDU":              "Fairleigh Dickinson",
        "FGCU":             "Florida Gulf Coast",
        "UNC-W":            "UNC Wilmington",
        "UNC-A":            "UNC Asheville",
        "E. Tennessee St.": "East Tennessee St.",
        "Miss. Valley St.": "Mississippi Valley St.",
        "Sac. State":       "Sacramento St.",
        "S.F. Austin":      "SF Austin",
        "Charleston":       "Charleston",
        "N.C. State":       "North Carolina St.",
        "Purdue Fort Wayne":"Purdue Fort Wayne",
        "Texas A&M-CC":     "Texas A&M Corpus Christi",
        "LIU Brooklyn":     "LIU",
        "UMKC":             "Missouri Kansas City",
    })
    # Rebuild all_team_names after map update (ensure normalize looks at fresh list)
    all_team_names = list({name for name, _ in team_id_map.keys()})

    bart_path = DATA_DIR / "barttorvik_transfers.csv"
    if bart_path.exists():
        print("Loading Barttorvik all-portal transfers...")
        bart_df = pd.read_csv(bart_path)
        bart_df = bart_df[bart_df["season"].isin(CBB_FILE_TO_SEASON.values())].copy()
        print(f"  {len(bart_df)} Barttorvik entries to process")

        bart_inserted = bart_skipped = 0
        for _, row in bart_df.iterrows():
            season    = str(row["season"]).strip()
            name      = str(row["player_name"]).strip().title()
            from_name = str(row["from_school"]).strip()
            to_name   = str(row["to_school"]).strip()

            if not name or not from_name or not to_name:
                bart_skipped += 1
                continue

            # Insert player (no physical data from Barttorvik — COALESCE preserves existing)
            cur.execute(
                """INSERT INTO players (full_name)
                   VALUES (%s)
                   ON CONFLICT (full_name) DO NOTHING""",
                (name,)
            )
            pid = player_id_map.get(name)
            if not pid:
                cur.execute("SELECT player_id FROM players WHERE full_name=%s", (name,))
                r = cur.fetchone()
                if r:
                    player_id_map[name] = r[0]
                    pid = r[0]
            if not pid:
                bart_skipped += 1
                continue

            to_tid   = match_team(to_name, season)
            from_tid = match_team(from_name, season) or get_or_create_sub_d1_team(from_name)

            if not to_tid or not from_tid or from_tid == to_tid:
                bart_skipped += 1
                continue

            cur.execute(
                """INSERT INTO transfers (player_id, from_team_id, to_team_id, season, transfer_type)
                   VALUES (%s,%s,%s,%s,'portal') ON CONFLICT DO NOTHING""",
                (pid, from_tid, to_tid, season)
            )
            bart_inserted += cur.rowcount

            prev_seasons = [s for s in CBB_FILE_TO_SEASON.values() if s < season]
            if prev_seasons:
                prev_season = max(prev_seasons)
                prev_tid    = match_team(from_name, prev_season)
                if prev_tid:
                    cur.execute(
                        "INSERT INTO player_seasons (player_id,team_id,season) VALUES (%s,%s,%s) ON CONFLICT (player_id,team_id,season) DO NOTHING",
                        (pid, prev_tid, prev_season)
                    )
            cur.execute(
                "INSERT INTO player_seasons (player_id,team_id,season) VALUES (%s,%s,%s) ON CONFLICT (player_id,team_id,season) DO NOTHING",
                (pid, to_tid, season)
            )

        conn.commit()
        print(f"  {bart_inserted} Barttorvik transfers inserted, {bart_skipped} skipped")
    else:
        print("No Barttorvik transfers CSV — run etl/scrape_barttorvik_transfers.py first")

    # ── Load international transfers (foreign leagues → D1) ───────────────────
    # Scored like sub_d1: bpm_after only (no pre-D1 stats exist).
    # Composite rating is the primary scouting signal — no European league tiers.
    intl_path = Path(__file__).parent.parent / "data" / "international_transfers.csv"
    if intl_path.exists():
        print("Loading international transfers...")
        intl_df = pd.read_csv(intl_path)
        intl_df = intl_df[intl_df["year"].isin(ON3_YEAR_TO_SEASON.keys())].copy()
        intl_df["season"] = intl_df["year"].map(ON3_YEAR_TO_SEASON)

        intl_inserted = intl_skipped = 0
        for _, row in intl_df.iterrows():
            season    = row["season"]
            name      = str(row["player_name"]).strip().title()
            from_name = str(row["from_school"]).strip()
            to_name   = str(row["to_school"]).strip() if pd.notna(row.get("to_school")) else ""

            if not from_name or not to_name or to_name.lower() in ("nan", ""):
                intl_skipped += 1
                continue

            pos_raw        = str(row.get("position", "")).strip()
            pos_clean      = ON3_POSITION_MAP.get(pos_raw.upper(), "G")
            composite      = row["recruiting_composite"] if pd.notna(row.get("recruiting_composite")) else None
            height_in      = parse_height_in(row.get("height")) if pd.notna(row.get("height", None) or float("nan")) else None
            weight_lbs     = int(row["weight"]) if pd.notna(row.get("weight")) and row.get("weight") else None
            birth_year     = int(row["birth_year"]) if pd.notna(row.get("birth_year")) and row.get("birth_year") else None
            origin_country = str(row.get("origin_country", row.get("from_country", ""))).strip() or None
            origin_league  = str(row.get("origin_league", "")).strip() or None

            cur.execute(
                """INSERT INTO players (full_name, position, class_year, recruiting_composite,
                       height_in, weight_lbs, birth_year, origin_country, origin_league)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (full_name) DO UPDATE SET
                       recruiting_composite = COALESCE(players.recruiting_composite, EXCLUDED.recruiting_composite),
                       height_in     = COALESCE(players.height_in,     EXCLUDED.height_in),
                       weight_lbs    = COALESCE(players.weight_lbs,    EXCLUDED.weight_lbs),
                       birth_year    = COALESCE(players.birth_year,    EXCLUDED.birth_year),
                       origin_country= COALESCE(players.origin_country,EXCLUDED.origin_country),
                       origin_league = COALESCE(players.origin_league, EXCLUDED.origin_league)
                   RETURNING player_id""",
                (name, pos_clean, str(row.get("class_year", ""))[:10] or None, composite,
                 height_in, weight_lbs, birth_year, origin_country, origin_league)
            )
            result = cur.fetchone()
            if result:
                player_id_map[name] = result[0]
            elif name not in player_id_map:
                cur.execute("SELECT player_id FROM players WHERE full_name=%s", (name,))
                r = cur.fetchone()
                if r:
                    player_id_map[name] = r[0]

            player_id = player_id_map.get(name)
            to_tid    = match_team(to_name, season)
            from_tid  = get_or_create_intl_team(from_name)  # always international origin

            if not player_id or not from_tid or not to_tid:
                intl_skipped += 1
                continue

            cur.execute(
                """INSERT INTO transfers (player_id, from_team_id, to_team_id, season, transfer_type)
                   VALUES (%s,%s,%s,%s,'portal') ON CONFLICT DO NOTHING""",
                (player_id, from_tid, to_tid, season)
            )
            intl_inserted += cur.rowcount

            # Only create the post-transfer placeholder — no pre-D1 CBB stats for international players
            cur.execute(
                "INSERT INTO player_seasons (player_id,team_id,season) VALUES (%s,%s,%s) ON CONFLICT (player_id,team_id,season) DO NOTHING",
                (player_id, to_tid, season)
            )

        conn.commit()
        print(f"  {intl_inserted} international transfers inserted, {intl_skipped} skipped")
    else:
        print("No international_transfers.csv found — skipping")

    # ── Load EuroBasket pre-D1 stats into player_pre_d1_stats ────────────────
    euro_path = DATA_DIR / "eurobasket_stats.csv"
    if euro_path.exists():
        print("Loading EuroBasket pre-D1 stats...")
        euro_df = pd.read_csv(euro_path)
        euro_df = euro_df[euro_df["ppg"].notna()].copy()  # only rows with actual stats
        cur.execute("SELECT player_id, LOWER(full_name) FROM players")
        euro_name_map = {n: pid for pid, n in cur.fetchall()}
        euro_upserted = 0
        for _, row in euro_df.iterrows():
            pid = euro_name_map.get(str(row["player_name"]).strip().lower())
            if not pid:
                continue
            cur.execute(
                """INSERT INTO player_pre_d1_stats
                   (player_id, team_name, league_name, country, season,
                    games, ppg, rpg, apg, mpg, fg_pct, three_pct, source_url)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (player_id, team_name, season) DO UPDATE SET
                       ppg    = COALESCE(EXCLUDED.ppg,    player_pre_d1_stats.ppg),
                       rpg    = COALESCE(EXCLUDED.rpg,    player_pre_d1_stats.rpg),
                       apg    = COALESCE(EXCLUDED.apg,    player_pre_d1_stats.apg),
                       mpg    = COALESCE(EXCLUDED.mpg,    player_pre_d1_stats.mpg),
                       fg_pct = COALESCE(EXCLUDED.fg_pct, player_pre_d1_stats.fg_pct)""",
                (pid, str(row.get("team_name",""))[:100], str(row.get("league_name",""))[:100],
                 str(row.get("country",""))[:50], str(row.get("season",""))[:9],
                 int(row["games"]) if pd.notna(row.get("games")) else None,
                 row.get("ppg"), row.get("rpg"), row.get("apg"), row.get("mpg"),
                 row.get("fg_pct"), row.get("three_pct"), str(row.get("source_url",""))[:255])
            )
            euro_upserted += cur.rowcount
        conn.commit()
        print(f"  {euro_upserted} EuroBasket stat rows upserted")
    else:
        print("No eurobasket_stats.csv — run etl/scrape_eurobasket.py to get pre-D1 intl stats")

    # ── Apply real CBB Reference player stats ─────────────────────────────────
    # This is the UPSERT pass — the only source of truth for BPM, TS%, USG%.
    # We only update player_seasons rows for transfer players (skip everyone else).
    # Players with < 12 games get skipped — small-sample BPM is unreliable.
    # Any |BPM| > 15 is almost certainly a data error; null it out rather than propagate.
    cbb_stats_path = DATA_DIR / "cbb_player_stats.csv"
    if cbb_stats_path.exists():
        print("Applying real CBB Reference player stats...")
        cbb_df = pd.read_csv(cbb_stats_path)
        cbb_df = cbb_df[cbb_df["Player"] != "Team Totals"].copy()

        cur.execute("SELECT player_id, LOWER(full_name) FROM players")
        name_to_pid = {name: pid for pid, name in cur.fetchall()}

        # Only write stats for players who appear in the transfers table
        cur.execute("SELECT DISTINCT player_id FROM transfers")
        transfer_pids = {r[0] for r in cur.fetchall()}

        def get_or_create_team(school, season):
            """Find or insert a team row — used when CBB Reference has a school not yet in teams."""
            tid = match_team(school, season)
            if tid:
                return tid
            cur.execute(
                "SELECT team_id FROM teams WHERE LOWER(name) = LOWER(%s) AND season = %s LIMIT 1",
                (school, season)
            )
            row = cur.fetchone()
            if row:
                team_id_map[(school, season)] = row[0]
                return row[0]
            cur.execute(
                "INSERT INTO teams (name, season) VALUES (%s, %s) RETURNING team_id",
                (school, season)
            )
            tid = cur.fetchone()[0]
            team_id_map[(school, season)] = tid
            all_team_names.append(school)
            return tid

        def parse_stat(val):
            """Convert a CBB Reference stat cell to float, returning None for blanks."""
            try:
                f = float(val)
                return None if pd.isna(f) else f
            except (ValueError, TypeError):
                return None

        # Build last-name → list of (player_id, full_name) for fuzzy fallback
        # If exact full-name match fails, match on (last_name, school) — catches
        # apostrophe/hyphen/period differences like "D'Shawn" vs "Dshawn"
        lastname_to_pids: dict[str, list[tuple[int, str]]] = {}
        for full_name_lower, pid in name_to_pid.items():
            parts = full_name_lower.strip().split()
            if parts:
                ln = parts[-1]
                lastname_to_pids.setdefault(ln, []).append((pid, full_name_lower))

        upserted = skipped_real = 0
        for _, row in cbb_df.iterrows():
            player_name = str(row["Player"]).strip()
            school      = str(row["school"]).strip()
            season      = str(row["season"]).strip()

            pid = name_to_pid.get(player_name.lower())
            if not pid:
                # Last-name fallback: if a transfer player has the same last name
                # and is at the same school this season, trust the match
                last = player_name.lower().split()[-1] if player_name else ""
                candidates = [
                    p for p, fn in lastname_to_pids.get(last, [])
                    if p in transfer_pids
                ]
                if len(candidates) == 1:
                    pid = candidates[0]
                elif len(candidates) > 1:
                    # Multiple transfer players with same last name — require school match
                    # via checking existing player_seasons for (pid, school, season)
                    for cand_pid in candidates:
                        to_tid_check = match_team(school, season)
                        if to_tid_check:
                            cur.execute(
                                "SELECT 1 FROM player_seasons WHERE player_id=%s AND team_id=%s AND season=%s LIMIT 1",
                                (cand_pid, to_tid_check, season)
                            )
                            if cur.fetchone():
                                pid = cand_pid
                                break
            if not pid or pid not in transfer_pids:
                skipped_real += 1
                continue

            bpm     = parse_stat(row.get("BPM"))
            obpm    = parse_stat(row.get("OBPM"))
            dbpm    = parse_stat(row.get("DBPM"))
            ts_pct  = parse_stat(row.get("TS%"))
            usg_pct = parse_stat(row.get("USG%"))
            ws_per_40 = parse_stat(row.get("WS/40"))
            ws        = parse_stat(row.get("WS"))
            games     = int(row["G"]) if pd.notna(row.get("G")) else None

            if bpm is None or abs(bpm) > 15 or (games is not None and games < 12):
                skipped_real += 1
                continue
            if obpm is not None and abs(obpm) > 15:
                obpm = None
            if dbpm is not None and abs(dbpm) > 15:
                dbpm = None
            if ws_per_40 is not None and (ws_per_40 < -0.1 or ws_per_40 > 0.5):
                ws_per_40 = None
            if ws is not None and (ws < -1 or ws > 15):
                ws = None  # clip extreme outliers

            tid = get_or_create_team(school, season)

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
                       ws        = COALESCE(EXCLUDED.ws,        player_seasons.ws)""",
                (pid, tid, season, bpm, obpm, dbpm, ts_pct, usg_pct, games, ws_per_40, ws)
            )
            upserted += cur.rowcount

            # Phase 2: backfill missing transfer records from CBB Reference "Prev. School" column
            # This catches JUCO→D1 and D2→D1 players who never appeared in any portal CSV
            prev_school_raw = str(row.get("prev_school", "")).strip()
            if prev_school_raw and prev_school_raw not in ("", "nan", "—"):
                cur.execute(
                    "SELECT 1 FROM transfers WHERE player_id=%s AND season=%s LIMIT 1",
                    (pid, season)
                )
                if not cur.fetchone():
                    from_tid_back = match_team(prev_school_raw, season) or get_or_create_sub_d1_team(prev_school_raw)
                    to_tid_back   = get_or_create_team(school, season)
                    if from_tid_back and to_tid_back and from_tid_back != to_tid_back:
                        cur.execute(
                            """INSERT INTO transfers (player_id, from_team_id, to_team_id, season, transfer_type)
                               VALUES (%s,%s,%s,%s,'cbb_backfill') ON CONFLICT DO NOTHING""",
                            (pid, from_tid_back, to_tid_back, season)
                        )

        conn.commit()
        print(f"  {upserted} player-season rows upserted with real stats ({skipped_real} skipped — not a transfer player or no CBB data)")
    else:
        print("No CBB Reference stats found — run etl/scrape_cbb_reference.py first")

    # ── Refresh materialized views ────────────────────────────────────────────
    print("Refreshing materialized views...")
    cur.execute("REFRESH MATERIALIZED VIEW tier_pair_expectations")
    cur.execute("REFRESH MATERIALIZED VIEW tier_pair_fallback")
    cur.execute("REFRESH MATERIALIZED VIEW individual_transfer_scores")
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
