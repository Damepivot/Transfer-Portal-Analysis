"""
Scrape player height and weight from CBB Reference school roster pages.

For every transfer player in the DB missing height_in or weight_lbs, fetch the
roster page for the school they played at and match by player name.

URL: sports-reference.com/cbb/schools/{slug}/men/{year}.html
  - Contains table id="roster" with Player, Height, Weight columns
  - Height format: "6-4" → 76 inches  Weight: integer lbs

Matching strategy:
  1. Exact lowercase full-name match
  2. Last-name + first-initial match (catches "D.J." vs "DJ" differences)

Rate limiting: 3s between requests, resume support via checkpoint CSV.

Run: python3 etl/scrape_heights.py
Then: python3 etl/load_real_data.py  (or apply directly — see bottom of this file)
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

CHECKPOINT = Path(__file__).parent.parent / "data" / "raw" / "heights_scraped.csv"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

# Reuse slug map from scrape_cbb_reference — inline the key ones here
SCHOOL_SLUG_MAP: dict[str, str] = {}

def _load_slug_map():
    """Read SCHOOL_SLUG_MAP from scrape_cbb_reference.py at runtime."""
    src = Path(__file__).parent / "scrape_cbb_reference.py"
    content = src.read_text()
    start = content.find("SCHOOL_SLUG_MAP: dict[str, str] = {")
    end   = content.find("\n}\n", start) + 3
    block = content[start:end]
    exec_env: dict = {}
    exec(block, exec_env)
    return exec_env.get("SCHOOL_SLUG_MAP", {})


def school_to_slug(school: str) -> str | None:
    slug = SCHOOL_SLUG_MAP.get(school)
    if slug:
        return slug
    # Auto-generate: lowercase, spaces → hyphens, drop punctuation
    auto = re.sub(r"[^\w\s-]", "", school.lower()).strip().replace(" ", "-").replace("--", "-")
    return auto or None


SEASON_TO_YEAR = {
    "2020-21": 2021, "2021-22": 2022,
    "2022-23": 2023, "2023-24": 2024,
    "2024-25": 2025, "2025-26": 2026,
}


def parse_height(val: str) -> int | None:
    """Convert '6-4' or '6-04' to total inches."""
    try:
        parts = str(val).strip().split("-")
        if len(parts) == 2:
            return int(parts[0]) * 12 + int(parts[1])
    except (ValueError, TypeError):
        pass
    return None


def fetch_roster(school: str, season: str) -> pd.DataFrame | None:
    """Fetch the roster table for a given school and season from CBB Reference."""
    slug = school_to_slug(school)
    if not slug:
        return None
    year = SEASON_TO_YEAR.get(season)
    if not year:
        return None

    url = f"https://www.sports-reference.com/cbb/schools/{slug}/men/{year}.html"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 429:
            print("    Rate limited — sleeping 60s")
            time.sleep(60)
            resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "lxml")

        # Try direct first, then inside HTML comments (CBB Reference hides some tables)
        table = soup.find("table", {"id": "roster"})
        if not table:
            for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
                if "roster" in str(comment) and "Height" in str(comment):
                    inner = BeautifulSoup(str(comment), "lxml")
                    table = inner.find("table", {"id": "roster"})
                    if table:
                        break

        if not table:
            return None

        df = pd.read_html(StringIO(str(table)))[0]
        df = df[df["Player"].notna() & (df["Player"] != "Player")].copy()
        return df[["Player", "Height", "Weight"]].copy()

    except Exception:
        return None


def match_player(roster_df: pd.DataFrame, player_name: str) -> tuple[int | None, int | None]:
    """
    Try to match player_name against the roster DataFrame.
    Returns (height_in, weight_lbs) or (None, None).
    """
    target = player_name.lower().strip()
    target_parts = target.split()
    target_last  = target_parts[-1] if target_parts else ""
    target_first_init = target_parts[0][0] if target_parts else ""

    for _, row in roster_df.iterrows():
        name = str(row["Player"]).lower().strip()
        # 1. Exact match
        if name == target:
            return parse_height(row["Height"]), _parse_weight(row["Weight"])
        # 2. Last name + first initial
        parts = name.split()
        if parts and parts[-1] == target_last and parts[0][0:1] == target_first_init:
            return parse_height(row["Height"]), _parse_weight(row["Weight"])

    return None, None


def _parse_weight(val) -> int | None:
    try:
        return int(float(str(val).strip()))
    except (ValueError, TypeError):
        return None


def get_missing_players(conn) -> list[dict]:
    """Return transfer players missing height or weight, grouped by school+season."""
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT
            p.player_id,
            p.full_name,
            t.name AS school,
            ps.season
        FROM players p
        JOIN transfers tr ON tr.player_id = p.player_id
        JOIN player_seasons ps ON ps.player_id = p.player_id
        JOIN teams t ON ps.team_id = t.team_id
        JOIN conferences c ON t.conference_id = c.conference_id
        WHERE (p.height_in IS NULL OR p.weight_lbs IS NULL)
          AND c.tier NOT IN ('sub_d1', 'international')
          AND ps.season BETWEEN '2020-21' AND '2024-25'
          AND t.season IS NOT NULL
        ORDER BY ps.season, t.name, p.full_name
    """)
    return [
        {"player_id": r[0], "full_name": r[1], "school": r[2], "season": r[3]}
        for r in cur.fetchall()
    ]


def main():
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)

    # Resume: load already-processed school-seasons
    done_school_seasons: set[tuple] = set()
    updates: list[dict] = []
    if CHECKPOINT.exists():
        ckpt = pd.read_csv(CHECKPOINT)
        done_school_seasons = set(zip(ckpt["school"], ckpt["season"]))
        updates = ckpt.to_dict("records")
        print(f"Resuming — {len(done_school_seasons)} school-seasons already done, "
              f"{sum(1 for u in updates if u.get('height_in'))} players with data")

    conn = psycopg2.connect(**DB_CONFIG)
    missing = get_missing_players(conn)
    print(f"Players missing height/weight: {len(missing)}")

    # Group by school+season to minimise HTTP requests
    from itertools import groupby
    from operator import itemgetter

    school_seasons: dict[tuple, list[dict]] = {}
    for p in missing:
        key = (p["school"], p["season"])
        school_seasons.setdefault(key, []).append(p)

    total_pairs = len(school_seasons)
    skip_n = len(done_school_seasons)
    todo = [(k, v) for k, v in school_seasons.items() if k not in done_school_seasons]
    print(f"School-season pairs to scrape: {len(todo)} (skipping {skip_n} already done)")

    found_total = 0
    for i, ((school, season), players) in enumerate(todo, 1):
        print(f"  [{i}/{len(todo)}] {school} {season} ({len(players)} players) ...")
        roster = fetch_roster(school, season)

        if roster is None:
            print(f"    No roster found")
            for p in players:
                updates.append({**p, "height_in": None, "weight_lbs": None})
        else:
            found_here = 0
            for p in players:
                h, w = match_player(roster, p["full_name"])
                updates.append({**p, "height_in": h, "weight_lbs": w})
                if h or w:
                    found_here += 1
            found_total += found_here
            print(f"    {found_here}/{len(players)} players matched")

        done_school_seasons.add((school, season))

        # Checkpoint every 10 schools
        if i % 10 == 0 or i == len(todo):
            pd.DataFrame(updates).to_csv(CHECKPOINT, index=False)
            print(f"    Checkpoint saved ({found_total} with data so far)")

        time.sleep(3)

    # Final save
    pd.DataFrame(updates).to_csv(CHECKPOINT, index=False)

    # Apply to DB
    cur = conn.cursor()
    applied = 0
    for u in updates:
        if u.get("height_in") or u.get("weight_lbs"):
            cur.execute(
                """UPDATE players SET
                       height_in  = COALESCE(height_in,  %s),
                       weight_lbs = COALESCE(weight_lbs, %s)
                   WHERE player_id = %s""",
                (u["height_in"], u["weight_lbs"], u["player_id"])
            )
            applied += cur.rowcount

    conn.commit()
    conn.close()
    print(f"\nDone. Applied height/weight to {applied} players.")
    print(f"Data saved to {CHECKPOINT}")


if __name__ == "__main__":
    SCHOOL_SLUG_MAP.update(_load_slug_map())
    main()
