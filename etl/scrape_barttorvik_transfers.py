"""
Scrape ALL D1 men's basketball portal entries from Barttorvik using Playwright.

Why Playwright: Barttorvik uses Cloudflare JS challenge — requests/curl is blocked.
The player data is embedded as a JSON array in an inline <script> tag after render.

Data structure per entry: [player_name, from_school, to_school, committed_flag]
  - to_school is null for uncommitted portal entrants (excluded from output)
  - Covers ALL portal entrants at ALL D1 schools, not just rated players

URL: barttorvik.com/playerstat.php?year={year}&xvalue=trans&minmin=0&erk=500
  year=2022 → 2021-22 season (they transferred INTO 2021-22)
  year=2023 → 2022-23 season
  ...
  year=2026 → 2025-26 season

Output: data/raw/barttorvik_transfers.csv
  Columns: player_name, from_school, to_school, season

Run: python3 etl/scrape_barttorvik_transfers.py
"""

import json
import time
import sys
import pandas as pd
from pathlib import Path
from playwright.sync_api import sync_playwright

OUTPUT_PATH = Path(__file__).parent.parent / "data" / "raw" / "barttorvik_transfers.csv"

YEAR_TO_SEASON = {
    # Barttorvik year=YYYY shows portal entries FROM the YYYY-1/YYYY season.
    # Players play at their new school the FOLLOWING season (year → year+1 format).
    # Exception: year=2026 entries are current-season players already playing 2025-26.
    2022: "2022-23",
    2023: "2023-24",
    2024: "2024-25",
    2025: "2025-26",
    2026: "2025-26",  # current season — players already playing
    # As of 2026-06, barttorvik's own site redirects year=2026 requests to
    # year=2027 (the site rolled its "current" trans-page pointer forward once
    # the 2025-26 season ended). year=2027 is now the live 2026-27 incoming
    # portal class — verified by spot-checking real, recognizable commits
    # (e.g. Denzel Aberdeen Kentucky → Florida). These players haven't played
    # at their new school yet, so they score with skill_index_before only.
    2027: "2026-27",
}

# Date ranges for each season (used in the URL filter)
YEAR_DATE_RANGE = {
    2022: ("20211101", "20220501"),
    2023: ("20221101", "20230501"),
    2024: ("20231101", "20240501"),
    2025: ("20241101", "20250501"),
    2026: ("20251101", "20260501"),
    2027: ("20251101", "20270501"),
}


def fetch_portal_year(page, year_int: int) -> list[list]:
    """
    Navigate to barttorvik playerstat for transfer portal and extract the
    embedded JS data array of [player_name, from_school, to_school, committed].
    Returns list of rows; to_school may be None for uncommitted players.
    """
    start, end = YEAR_DATE_RANGE[year_int]
    url = (
        f"https://barttorvik.com/playerstat.php"
        f"?year={year_int}&xvalue=trans&minmin=0&erk=500"
        f"&start={start}&end={end}"
    )
    print(f"  Fetching year={year_int} ({YEAR_TO_SEASON[year_int]}) ...")
    page.goto(url, wait_until="networkidle", timeout=60000)
    time.sleep(2)

    raw_json = page.evaluate("""() => {
        const scripts = Array.from(document.querySelectorAll('script:not([src])'));
        const bigScript = scripts.find(s => s.innerText.length > 100000);
        if (!bigScript) return null;
        const text = bigScript.innerText;
        const playerStart = text.indexOf('[[');
        if (playerStart === -1) return null;
        let depth = 0, end = playerStart;
        for (let i = playerStart; i < text.length; i++) {
            if (text[i] === '[') depth++;
            else if (text[i] === ']') {
                depth--;
                if (depth === 0) { end = i + 1; break; }
            }
        }
        return text.substring(playerStart, end);
    }""")

    if not raw_json:
        print(f"    WARNING: No data found for year={year_int}")
        return []

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        print(f"    WARNING: JSON parse error for year={year_int}: {e}")
        return []

    print(f"    {len(data)} total entries, {sum(1 for r in data if len(r) >= 3 and r[2] is not None)} with destination")
    return data


def main():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Check for existing data (resume support)
    existing_seasons = set()
    if OUTPUT_PATH.exists():
        existing = pd.read_csv(OUTPUT_PATH)
        existing_seasons = set(existing["season"].unique())
        print(f"Resuming — already have: {sorted(existing_seasons)}")
        all_frames = [existing]
    else:
        all_frames = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        for year_int, season in YEAR_TO_SEASON.items():
            if season in existing_seasons:
                print(f"  Skipping {season} (already in CSV)")
                continue

            rows = fetch_portal_year(page, year_int)
            if not rows:
                time.sleep(3)
                continue

            records = []
            for row in rows:
                if not isinstance(row, list) or len(row) < 3:
                    continue
                player_name = str(row[0]).strip() if row[0] else None
                from_school  = str(row[1]).strip() if row[1] else None
                to_school    = str(row[2]).strip() if row[2] else None

                if not player_name or not from_school or not to_school:
                    continue
                if to_school.lower() in ("none", "null", ""):
                    continue
                if from_school == to_school:
                    continue  # same-school re-commit — skip

                records.append({
                    "player_name": player_name,
                    "from_school": from_school,
                    "to_school":   to_school,
                    "season":      season,
                })

            if records:
                frame = pd.DataFrame(records)
                frame = frame.drop_duplicates(subset=["player_name", "from_school", "to_school", "season"])
                all_frames.append(frame)
                print(f"    {len(frame)} committed transfers for {season}")

            # Save checkpoint after each year
            if all_frames:
                combined = pd.concat(all_frames, ignore_index=True)
                combined = combined.drop_duplicates(subset=["player_name", "from_school", "to_school", "season"])
                combined.to_csv(OUTPUT_PATH, index=False)
                print(f"    Checkpoint saved: {len(combined)} total rows")

            time.sleep(3)

        browser.close()

    if all_frames:
        combined = pd.concat(all_frames, ignore_index=True)
        combined = combined.drop_duplicates(subset=["player_name", "from_school", "to_school", "season"])
        combined = combined.sort_values(["season", "from_school", "player_name"]).reset_index(drop=True)
        combined.to_csv(OUTPUT_PATH, index=False)
        print(f"\nDone. Saved {len(combined)} transfer entries to {OUTPUT_PATH}")
        print(f"By season:\n{combined.groupby('season').size().to_string()}")
    else:
        print("No data fetched.")


if __name__ == "__main__":
    main()
