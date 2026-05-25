"""
Scrape real player advanced stats (BPM, TS%, USG%) from College Basketball Reference.
Only fetches schools that appear in our transfer dataset — avoids scraping all 350 programs.
Outputs: data/raw/cbb_player_stats.csv

Run: python etl/scrape_cbb_reference.py
Then re-run: python etl/load_real_data.py  (it will use this file to override estimated stats)
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
}


def get_slug(school_name: str) -> str | None:
    if school_name in SCHOOL_SLUG_MAP:
        return SCHOOL_SLUG_MAP[school_name]
    # Auto-generate slug: lowercase, spaces → hyphens, remove punctuation
    slug = re.sub(r"[^a-z0-9\-]", "", school_name.lower().replace(" ", "-").replace(".", "").replace("'", ""))
    return slug


def scrape_school_season(school: str, season: str) -> pd.DataFrame | str | None:
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
            # Rate-limited — back off and signal caller
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
                    # Drop separator rows (where Player == 'Player')
                    df = df[df["Player"] != "Player"].copy()
                    df = df[df["Player"].notna()].copy()
                    df["school"]  = school
                    df["season"]  = season
                    return df[["Player", "Pos", "G", "TS%", "USG%", "BPM", "OBPM", "DBPM", "school", "season"]]
    except Exception:
        pass
    return None


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()

    # Only scrape school-seasons that appear in our actual transfer data
    cur.execute("""
        SELECT DISTINCT t.name AS school, tr.season
        FROM transfers tr
        JOIN teams t ON (tr.from_team_id = t.team_id OR tr.to_team_id = t.team_id)
        WHERE tr.season IN ('2021-22','2022-23','2023-24','2024-25')
        ORDER BY tr.season, t.name
    """)
    school_seasons = cur.fetchall()
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
