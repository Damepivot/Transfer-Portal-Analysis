"""
Scrape real player advanced stats from College Basketball Reference.

Why CBB Reference?
  BPM (Box Plus/Minus) is the core metric of the scoring model.
  It measures a player's impact in points above average per 100 possessions,
  adjusted for pace and opponent strength. It's the closest D1 basketball has
  to a single-number "how good is this player" measure that's available for free.

  CBB Reference is the only public source for BPM at the college level.

How scraping works:
  Each school-season page (e.g. sports-reference.com/cbb/schools/duke/men/2024.html)
  has an advanced stats table (id="players_advanced") embedded in an HTML comment
  to avoid search-engine indexing. We extract the comment, parse the table with
  BeautifulSoup + pd.read_html, and keep BPM, OBPM, DBPM, TS%, USG%, G columns.

Scope — only schools involved in our transfers:
  Scraping all 350 D1 programs would take hours and most data would be unused.
  We query the DB for (school, season) pairs where the school sent or received
  a transfer, then also fetch the prior season for each from_school so that
  bpm_before is real data, not estimated.

Resume support:
  Progress is saved to cbb_player_stats.csv every 25 schools. Re-running the
  scraper after interruption will skip already-done school-seasons.

Rate limiting:
  3-second sleep between requests. CBB Reference will 429 if hit too fast.
  Three consecutive 429s trigger a save-and-stop to avoid bans.

Outputs: data/raw/cbb_player_stats.csv
Run: python etl/scrape_cbb_reference.py
Then: python etl/load_real_data.py
"""

import re
import sys
import time
import psycopg2
import requests
import pandas as pd
from io import StringIO
from pathlib import Path
from bs4 import BeautifulSoup, Comment
sys.path.insert(0, str(Path(__file__).parent.parent))
from db import DB_CONFIG

OUTPUT_PATH = Path(__file__).parent.parent / "data" / "raw" / "cbb_player_stats.csv"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

# CBB Reference uses year = end of season (2024 = 2023-24 season)
SEASON_TO_YEAR = {
    "2016-17": 2017, "2017-18": 2018, "2018-19": 2019, "2019-20": 2020,
    "2020-21": 2021, "2021-22": 2022,
    "2022-23": 2023, "2023-24": 2024, "2024-25": 2025,
}

# School name → CBB Reference URL slug
# Derived from https://www.sports-reference.com/cbb/schools/
SCHOOL_SLUG_MAP = {
    "Duke":                "duke",
    "Kentucky":            "kentucky",
    "Kansas":              "kansas",
    "Michigan":            "michigan",
    "Connecticut":         "connecticut",
    "Gonzaga":             "gonzaga",
    "Alabama":             "alabama",
    "Purdue":              "purdue",
    "Baylor":              "baylor",
    "Tennessee":           "tennessee",
    "Houston":             "houston",
    "Creighton":           "creighton",
    "San Diego St.":       "san-diego-state",
    "Dayton":              "dayton",
    "Virginia Commonwealth": "virginia-commonwealth",
    "Memphis":             "memphis",
    "Wichita St.":         "wichita-state",
    "New Mexico":          "new-mexico",
    "Davidson":            "davidson",
    "Saint Mary's":        "saint-marys-ca",
    "Drake":               "drake",
    "Loyola Chicago":      "loyola-il",
    "Murray St.":          "murray-state",
    "Cleveland St.":       "cleveland-state",
    "Toledo":              "toledo",
    "Texas El Paso":       "utep",
    "Appalachian St.":     "appalachian-state",
    "Furman":              "furman",
    "UC Irvine":           "uc-irvine",
    "Georgia St.":         "georgia-state",
    "Arizona":             "arizona",
    "Arizona St.":         "arizona-state",
    "Arkansas":            "arkansas",
    "Auburn":              "auburn",
    "Boston College":      "boston-college",
    "Butler":              "butler",
    "California":          "california",
    "Cincinnati":          "cincinnati",
    "Clemson":             "clemson",
    "Colorado":            "colorado",
    "Colorado St.":        "colorado-state",
    "DePaul":              "depaul",
    "Florida":             "florida",
    "Florida St.":         "florida-state",
    "Georgetown":          "georgetown",
    "Georgia":             "georgia",
    "Georgia Tech":        "georgia-tech",
    "Illinois":            "illinois",
    "Indiana":             "indiana",
    "Iowa":                "iowa",
    "Iowa St.":            "iowa-state",
    "Kansas St.":          "kansas-state",
    "Louisville":          "louisville",
    "LSU":                 "louisiana-state",
    "Marquette":           "marquette",
    "Maryland":            "maryland",
    "Michigan St.":        "michigan-state",
    "Minnesota":           "minnesota",
    "Mississippi":         "mississippi",
    "Mississippi St.":     "mississippi-state",
    "Missouri":            "missouri",
    "NC State":            "north-carolina-state",
    "Nebraska":            "nebraska",
    "North Carolina":      "north-carolina",
    "Northwestern":        "northwestern",
    "Notre Dame":          "notre-dame",
    "Ohio St.":            "ohio-state",
    "Oklahoma":            "oklahoma",
    "Oklahoma St.":        "oklahoma-state",
    "Oregon":              "oregon",
    "Oregon St.":          "oregon-state",
    "Penn St.":            "penn-state",
    "Pittsburgh":          "pittsburgh",
    "Providence":          "providence",
    "Rutgers":             "rutgers",
    "Seton Hall":          "seton-hall",
    "South Carolina":      "south-carolina",
    "Stanford":            "stanford",
    "St. John's":          "st-johns-ny",
    "Syracuse":            "syracuse",
    "TCU":                 "texas-christian",
    "Texas":               "texas",
    "Texas A&M":           "texas-am",
    "Texas Tech":          "texas-tech",
    "UCLA":                "ucla",
    "USC":                 "southern-california",
    "Utah":                "utah",
    "Utah St.":            "utah-state",
    "Vanderbilt":          "vanderbilt",
    "Virginia":            "virginia",
    "Virginia Tech":       "virginia-tech",
    "Wake Forest":         "wake-forest",
    "Washington":          "washington",
    "Washington St.":      "washington-state",
    "West Virginia":       "west-virginia",
    "Wisconsin":           "wisconsin",
    "Xavier":              "xavier",
    "Villanova":           "villanova",
    "BYU":                 "brigham-young",
    "UCF":                 "central-florida",
    "Miami FL":            "miami-fl",
    "SMU":                 "southern-methodist",
    "Tulsa":               "tulsa",
    "South Florida":       "south-florida",
    "Tulane":              "tulane",
    "East Carolina":       "east-carolina",
    "Florida Atlantic":    "florida-atlantic",
    "North Texas":         "north-texas",
    "Rice":                "rice",
    "UTSA":                "texas-san-antonio",
    "Charlotte":           "charlotte",
    "Nevada":              "nevada",
    "Boise St.":           "boise-state",
    "Fresno St.":          "fresno-state",
    "UNLV":                "nevada-las-vegas",
    "Wyoming":             "wyoming",
    "Air Force":           "air-force",
    "Hawaii":              "hawaii",
    "San Francisco":       "san-francisco",
    "Santa Clara":         "santa-clara",
    "Pacific":             "pacific",
    "Loyola Marymount":    "loyola-marymount",
    "Pepperdine":          "pepperdine",
    "Portland":            "portland",
    "VCU":                 "virginia-commonwealth",
    "Saint Louis":         "saint-louis",
    "Richmond":            "richmond",
    "Rhode Island":        "rhode-island",
    "George Mason":        "george-mason",
    "Fordham":             "fordham",
    "La Salle":            "la-salle",
    "Duquesne":            "duquesne",
    "Massachusetts":       "massachusetts",
    "Missouri St.":        "missouri-state",
    "Illinois St.":        "illinois-state",
    "Indiana St.":         "indiana-state",
    "Bradley":             "bradley",
    "Evansville":          "evansville",
    "Ohio":                "ohio",
    "Ball St.":            "ball-state",
    "Kent St.":            "kent-state",
    "Miami OH":            "miami-oh",
    "Akron":               "akron",
    "Buffalo":             "buffalo",
    "Western Michigan":    "western-michigan",
    "Northern Illinois":   "northern-illinois",
    "Bowling Green":       "bowling-green-state",
    "LA Tech":             "louisiana-tech",
    "Marshall":            "marshall",
    "Old Dominion":        "old-dominion",
    "UAB":                 "alabama-birmingham",
    "FIU":                 "florida-international",
    "Middle Tennessee":    "middle-tennessee-state",
    "Western Kentucky":    "western-kentucky",
    "Belmont":             "belmont",
    "Jacksonville St.":    "jacksonville-state",
    "Liberty":             "liberty",
    "Kennesaw St.":        "kennesaw-state",
    "Little Rock":         "arkansas-little-rock",
    "Sam Houston St.":     "sam-houston-state",
    "Winthrop":            "winthrop",
    "Gardner-Webb":        "gardner-webb",
    "SIU Edwardsville":    "siu-edwardsville",
    "Detroit":             "detroit-mercy",
    "Green Bay":           "wisconsin-green-bay",
    "Youngstown St.":      "youngstown-state",
    "Wright St.":          "wright-state",
    "Oakland":             "oakland",
    "Milwaukee":           "wisconsin-milwaukee",
    "Northeastern":        "northeastern",
    "Hofstra":             "hofstra",
    "Delaware":            "delaware",
    "Drexel":              "drexel",
    "Elon":                "elon",
    "Towson":              "towson",
    "William & Mary":      "william-mary",
    "James Madison":       "james-madison",
    "Stony Brook":         "stony-brook",
    "Vermont":             "vermont",
    "Albany":              "albany-ny",
    "Maine":               "maine",
    "New Hampshire":       "new-hampshire",
    "Hartford":            "hartford",
    "Binghamton":          "binghamton",
    "UMBC":                "maryland-baltimore-county",
    "Jackson St.":         "jackson-state",
    "Alabama St.":         "alabama-state",
    "Grambling St.":       "grambling",
    "Southern":            "southern",
    "Alcorn St.":          "alcorn-state",
    "Prairie View":        "prairie-view",
    "Texas Southern":      "texas-southern",
    "Norfolk St.":         "norfolk-state",
    "Morgan St.":          "morgan-state",
    "Coppin St.":          "coppin-state",
    "Delaware St.":        "delaware-state",
    "Howard":              "howard",
    "Bethune-Cookman":     "bethune-cookman",
    "Florida A&M":         "florida-am",
    "Holy Cross":          "holy-cross",
    "Lehigh":              "lehigh",
    "Bucknell":            "bucknell",
    "Navy":                "navy",
    "Army":                "army",
    "Lafayette":           "lafayette",
    "Colgate":             "colgate",
    "American":            "american",
    "Manhattan":           "manhattan",
    "Iona":                "iona",
    "Siena":               "siena",
    "Rider":               "rider",
    "Fairfield":           "fairfield",
    "Niagara":             "niagara",
    "Marist":              "marist",
    "Canisius":            "canisius",
    "Quinnipiac":          "quinnipiac",
    # Schools where auto-slug fails: "St." → CBB uses "state", not "st"
    "North Carolina St.":  "north-carolina-state",
    "San Jose St.":        "san-jose-state",
    "McNeese St.":         "mcneese-state",
    "Nicholls St.":        "nicholls-state",
    "Arkansas St.":        "arkansas-state",
    "Montana St.":         "montana-state",
    "South Dakota St.":    "south-dakota-state",
    "North Dakota St.":    "north-dakota-state",
    "Weber St.":           "weber-state",
    "Sacramento St.":      "sacramento-state",
    "Portland St.":        "portland-state",
    "Southeast Missouri St.": "southeast-missouri-state",
    "Tennessee St.":       "tennessee-state",
    "Mississippi Valley St.": "mississippi-valley-state",
    "Idaho St.":           "idaho-state",
    "Tennessee Martin":    "tennessee-martin",
    "East Tennessee St.":  "east-tennessee-state",
    "Morehead St.":        "morehead-state",
    "Long Beach St.":      "long-beach-state",
    "Cal St. Bakersfield": "cal-state-bakersfield",
    "Cal St. Fullerton":   "cal-state-fullerton",
    "Cal St. Northridge":  "cal-state-northridge",
    # Schools with non-standard slugs on CBB Reference
    "Louisiana Lafayette": "louisiana",
    "Louisiana Monroe":    "louisiana-monroe",
    "Louisiana Tech":      "louisiana-tech",
    "Detroit Mercy":       "detroit-mercy",
    "Detroit":             "detroit-mercy",
    "Bethune Cookman":     "bethune-cookman",
    "Gardner Webb":        "gardner-webb",
    "George Washington":   "george-washington",
    "Grand Canyon":        "grand-canyon",
    "High Point":          "high-point",
    "Houston Baptist":     "houston-baptist",
    "Houston Christian":   "houston-christian",
    "Incarnate Word":      "incarnate-word",
    "Jacksonville":        "jacksonville",
    "Le Moyne":            "le-moyne",
    "Lipscomb":            "lipscomb",
    "Longwood":            "longwood",
    "Mercer":              "mercer",
    "Merrimack":           "merrimack",
    "Monmouth":            "monmouth",
    "Montana":             "montana",
    "New Orleans":         "new-orleans",
    "North Alabama":       "north-alabama",
    "North Carolina A&T":  "north-carolina-at",
    "North Carolina Central": "north-carolina-central",
    "North Dakota":        "north-dakota",
    "North Florida":       "north-florida",
    "Northern Arizona":    "northern-arizona",
    "Northern Colorado":   "northern-colorado",
    "Northern Iowa":       "northern-iowa",
    "Northern Kentucky":   "northern-kentucky",
    "Oral Roberts":        "oral-roberts",
    "Penn":                "pennsylvania",
    "Prairie View A&M":    "prairie-view",
    "Presbyterian":        "presbyterian",
    "Princeton":           "princeton",
    "Radford":             "radford",
    "Robert Morris":       "robert-morris",
    "Sacred Heart":        "sacred-heart",
    "Saint Joseph's":      "saint-josephs-pa",
    "Saint Peter's":       "saint-peters",
    "Samford":             "samford",
    "San Diego":           "san-diego",
    "Seattle":             "seattle",
    "South Alabama":       "south-alabama",
    "South Dakota":        "south-dakota",
    "Southeastern Louisiana": "southeastern-louisiana",
    "Southern Miss":       "southern-miss",
    "Southern Utah":       "southern-utah",
    "St. Bonaventure":     "st-bonaventure",
    "Stetson":             "stetson",
    "Tarleton St.":        "tarleton-state",
    "Temple":              "temple",
    "Tennessee Tech":      "tennessee-tech",
    "Texas St.":           "texas-state",
    "Troy":                "troy",
    "UC Davis":            "uc-davis",
    "UC Riverside":        "uc-riverside",
    "UC San Diego":        "uc-san-diego",
    "UC Santa Barbara":    "uc-santa-barbara",
    "UNC Asheville":       "unc-asheville",
    "UNC Greensboro":      "unc-greensboro",
    "UNC Wilmington":      "unc-wilmington",
    "Utah Valley":         "utah-valley",
    "Valparaiso":          "valparaiso",
    "Wagner":              "wagner",
    "Western Carolina":    "western-carolina",
    "Western Illinois":    "western-illinois",
    "Wofford":             "wofford",
    "Yale":                "yale",
    "Columbia":            "columbia",
    "Cornell":             "cornell",
    "Dartmouth":           "dartmouth",
    "Brown":               "brown",
    "Harvard":             "harvard",
    "Denver":              "denver",
    "Cal Poly":            "cal-poly",
    "Cal Baptist":         "california-baptist",
    "Abilene Christian":   "abilene-christian",
    "Austin Peay":         "austin-peay",
    "Bryant":              "bryant",
    "Campbell":            "campbell",
    "Central Arkansas":    "central-arkansas",
    "Central Michigan":    "central-michigan",
    "Charleston Southern": "charleston-southern",
    "Chattanooga":         "chattanooga",
    "Coastal Carolina":    "coastal-carolina",
    "College of Charleston": "college-of-charleston",
    "Eastern Illinois":    "eastern-illinois",
    "Eastern Kentucky":    "eastern-kentucky",
    "Eastern Michigan":    "eastern-michigan",
    "Eastern Washington":  "eastern-washington",
    "Fairleigh Dickinson": "fairleigh-dickinson",
    "Florida Gulf Coast":  "florida-gulf-coast",
    "Fort Wayne":          "purdue-fort-wayne",
    "Georgia Southern":    "georgia-southern",
    "Hampton":             "hampton",
    "Idaho":               "idaho",
    "IUPUI":               "iupui",
    "LIU Brooklyn":        "long-island-university",
    "New Mexico St.":      "new-mexico-state",
    "Nebraska Omaha":      "nebraska-omaha",
    "Queens":              "queens-nc",
    "St. Thomas":          "st-thomas-mn",
    "Stonehill":           "stonehill",
}


def get_slug(school_name: str) -> str | None:
    """Return the CBB Reference URL slug for a school.

    SCHOOL_SLUG_MAP covers known mismatches (St. vs state, abbreviations).
    The auto-generator handles the majority of schools that follow the
    standard lowercase-hyphen pattern.
    """
    if school_name in SCHOOL_SLUG_MAP:
        return SCHOOL_SLUG_MAP[school_name]
    # Auto-generate slug: lowercase, spaces → hyphens, remove punctuation
    slug = re.sub(r"[^a-z0-9\-]", "", school_name.lower().replace(" ", "-").replace(".", "").replace("'", ""))
    return slug


def scrape_school_season(school: str, season: str) -> pd.DataFrame | str | None:
    """
    Fetch the advanced stats table for one school-season from CBB Reference.

    Returns:
      pd.DataFrame  — stats table (Player, G, BPM, OBPM, DBPM, TS%, USG%)
      "rate_limited" — 429 received; caller should back off
      None          — page not found, parsing failed, or season not in SEASON_TO_YEAR

    The advanced table is embedded in an HTML comment on the page — CBB Reference
    hides it to prevent search indexing. We find the comment containing
    "players_advanced" and parse the table from inside it.
    """
    year = SEASON_TO_YEAR.get(season)
    if not year:
        return None
    slug = get_slug(school)
    if not slug:
        return None

    url = f"https://www.sports-reference.com/cbb/schools/{slug}/men/{year}.html"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 429:
            time.sleep(60)
            return "rate_limited"
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "lxml")
        comments = soup.find_all(string=lambda t: isinstance(t, Comment))

        for comment in comments:
            if "players_advanced" in str(comment):
                inner = BeautifulSoup(str(comment), "lxml")
                table = inner.find("table", {"id": "players_advanced"})
                if table:
                    df = pd.read_html(StringIO(str(table)))[0]
                    # Drop separator rows (repeated header rows CBB Reference inserts every 20 rows)
                    df = df[df["Player"] != "Player"].copy()
                    df = df[df["Player"].notna()].copy()
                    df["school"]  = school
                    df["season"]  = season
                    keep = ["Player", "Pos", "G", "TS%", "USG%", "BPM", "OBPM", "DBPM", "WS", "WS/40", "school", "season"]
                    for col in ("Prev. School", "Prior School"):
                        if col in df.columns:
                            df["prev_school"] = df[col].astype(str).replace("nan", "")
                            keep.append("prev_school")
                            break
                    return df[[c for c in keep if c in df.columns]]
    except Exception:
        pass
    return None


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()

    # Build the scrape list: every (school, season) where the school sent or received
    # a transfer. Also add the prior season for each from_school so bpm_before is real.
    PRIOR = {
        "2021-22": "2020-21",
        "2022-23": "2021-22",
        "2023-24": "2022-23",
        "2024-25": "2023-24",
    }
    cur.execute("""
        SELECT DISTINCT t.name AS school, tr.season
        FROM transfers tr
        JOIN teams t ON (tr.from_team_id = t.team_id OR tr.to_team_id = t.team_id)
        WHERE tr.season IN ('2020-21','2021-22','2022-23','2023-24','2024-25')
        UNION
        SELECT DISTINCT t.name AS school, tr.season
        FROM transfers tr
        JOIN teams t ON tr.from_team_id = t.team_id
        WHERE tr.season IN ('2021-22','2022-23','2023-24','2024-25')
        ORDER BY 2, 1
    """)
    raw_pairs = cur.fetchall()
    # Add prior-season rows for each from_school
    prior_pairs = set()
    cur.execute("""
        SELECT DISTINCT t.name, tr.season
        FROM transfers tr
        JOIN teams t ON tr.from_team_id = t.team_id
        WHERE tr.season IN ('2021-22','2022-23','2023-24','2024-25')
    """)
    for school, season in cur.fetchall():
        if season in PRIOR:
            prior_pairs.add((school, PRIOR[season]))
    school_seasons = list(set(raw_pairs) | prior_pairs)
    school_seasons.sort(key=lambda x: (x[1], x[0]))
    cur.close()
    conn.close()

    # Load existing CSV so we never overwrite already-scraped data
    already_done = set()
    existing_frames = []
    if OUTPUT_PATH.exists():
        existing = pd.read_csv(OUTPUT_PATH)
        existing_frames.append(existing)
        already_done = set(zip(existing["school"], existing["season"]))
        print(f"Resuming — {len(already_done)} school-seasons already in CSV, skipping those.")

    todo = [(s, ss) for s, ss in school_seasons if (s, ss) not in already_done]
    print(f"Need to scrape {len(todo)} school-seasons (of {len(school_seasons)} total).")
    print(f"At 3s/req estimated time: {len(todo) * 3 // 60} min")

    new_frames = []
    done = skipped = rate_limited = 0

    for i, (school, season) in enumerate(todo):
        result = scrape_school_season(school, season)
        if isinstance(result, str) and result == "rate_limited":
            rate_limited += 1
            skipped += 1
            if rate_limited >= 3:
                print(f"\nRate-limited 3x in a row — saving progress and stopping.")
                break
        elif isinstance(result, pd.DataFrame) and not result.empty:
            new_frames.append(result)
            done += 1
            rate_limited = 0
        else:
            skipped += 1
            rate_limited = 0

        if (i + 1) % 25 == 0:
            # Save progress incrementally every 25 schools
            if new_frames:
                checkpoint = pd.concat(existing_frames + new_frames, ignore_index=True)
                checkpoint.to_csv(OUTPUT_PATH, index=False)
            print(f"  [{i+1}/{len(todo)}] +{done} scraped, {skipped} skipped — saved checkpoint")

        time.sleep(3.0)   # polite rate limit

    all_frames = existing_frames + new_frames
    if all_frames:
        combined = pd.concat(all_frames, ignore_index=True).drop_duplicates(subset=["Player","school","season"])
        combined.to_csv(OUTPUT_PATH, index=False)
        print(f"\nSaved {len(combined)} total player-season rows to {OUTPUT_PATH.name}")
        print(f"New this run: {done} scraped, {skipped} skipped")
    else:
        print("No data.")


if __name__ == "__main__":
    main()
