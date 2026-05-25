"""
Scrape On3 transfer portal for 2023, 2024, 2025 seasons.
Data is embedded as __NEXT_DATA__ JSON in each page — no JS execution needed.
Outputs: data/raw/on3_transfers_{year}.csv

Run: python etl/scrape_on3.py
"""

import re
import time
import json
import requests
import pandas as pd
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent / "data" / "raw"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
YEARS     = [2023, 2024, 2025]
PAGE_SIZE = 50


def fetch_page(year: int, page: int) -> dict:
    url = f"https://www.on3.com/transfer-portal/industry/basketball/{year}/"
    params = {"page": page} if page > 1 else {}
    resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    match = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.DOTALL
    )
    if not match:
        return {}
    return json.loads(match.group(1))


def extract_player(p: dict, year: int) -> dict | None:
    name     = p.get("name", "").strip()
    position = p.get("positionAbbreviation", "")
    height   = p.get("height", "")
    weight   = p.get("weight")

    # Birth year from ISO date string e.g. "2003-05-15"
    birth_date = p.get("birthDate") or p.get("birthday") or ""
    birth_year = None
    if birth_date:
        try:
            birth_year = int(str(birth_date)[:4])
        except (ValueError, TypeError):
            pass

    # From school
    last_team = p.get("lastTeam") or {}
    from_school = last_team.get("name") or last_team.get("fullName") or ""
    from_school = from_school.replace(" Wildcats","").replace(" Bulldogs","").strip()
    from_school = last_team.get("name", from_school)

    # To school — only if committed
    commit = p.get("commitStatus") or {}
    to_org  = commit.get("committedOrganization") or {}
    to_school = to_org.get("name") or to_org.get("fullName") or ""

    if not from_school:
        return None

    # Recruiting composite (On3 scale 0-100)
    transfer_rating = p.get("transferRating") or {}
    composite = transfer_rating.get("consensusRating") or transfer_rating.get("rating")

    # Class / eligibility
    eligibility = (p.get("eligibility") or {}).get("label") or \
                  (commit.get("classRank") or "")

    if p.get("withdrawnTransfer"):
        to_school = ""

    return {
        "player_name":          name,
        "position":             position,
        "height":               height,
        "weight":               int(weight) if weight else None,
        "birth_year":           birth_year,
        "from_school":          from_school.strip(),
        "to_school":            to_school.strip(),
        "recruiting_composite": round(composite, 2) if composite else None,
        "class_year":           str(eligibility)[:10] if eligibility else None,
        "year":                 year,
        "committed":            commit.get("type") == "Committed",
    }


def scrape_year(year: int) -> pd.DataFrame:
    print(f"\nScraping {year}...")
    # Get first page to find total count
    data       = fetch_page(year, 1)
    player_data = data["props"]["pageProps"]["playerData"]
    total      = player_data["pagination"]["count"]
    pages      = -(-total // PAGE_SIZE)   # ceiling division
    print(f"  {total} players across {pages} pages")

    records = []
    for p in player_data["list"]:
        rec = extract_player(p, year)
        if rec:
            records.append(rec)

    for page in range(2, pages + 1):
        time.sleep(0.8)   # be polite
        try:
            data = fetch_page(year, page)
            players = data["props"]["pageProps"]["playerData"]["list"]
            for p in players:
                rec = extract_player(p, year)
                if rec:
                    records.append(rec)
            if page % 5 == 0:
                print(f"  page {page}/{pages} — {len(records)} records so far")
        except Exception as e:
            print(f"  page {page} error: {e}")
            continue

    df = pd.DataFrame(records)
    print(f"  Done: {len(df)} records, {df['committed'].sum()} committed")
    return df


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    all_frames = []

    for year in YEARS:
        df = scrape_year(year)
        out_path = OUTPUT_DIR / f"on3_transfers_{year}.csv"
        df.to_csv(out_path, index=False)
        print(f"  Saved to {out_path.name}")
        all_frames.append(df)

    combined = pd.concat(all_frames, ignore_index=True)
    combined_path = OUTPUT_DIR / "on3_transfers_combined.csv"
    combined.to_csv(combined_path, index=False)
    print(f"\nCombined: {len(combined)} rows → {combined_path.name}")
    print(combined[["year","from_school","to_school","committed"]].value_counts("year"))


if __name__ == "__main__":
    main()
