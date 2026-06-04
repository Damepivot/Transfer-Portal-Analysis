"""
Scrape the On3 transfer portal rated player list (top ~50 per year).

How it works:
  On3 embeds all page data as JSON in a <script id="__NEXT_DATA__"> tag.
  No browser / JS execution needed — just parse the JSON from the raw HTML.

  The main portal page (on3.com/transfer-portal/industry/basketball/{year}/)
  contains two key fields in pageProps:
    topList     — the top-rated portal entrants (with composite scores)
    transferHistoryByPlayerKey — each player's college history (used to find from_school)

  NOTE: On3 changed their format in ~2023. The old format had a paginated
  playerData.list with all entrants. The new format caps topList at ~50
  rated players per year. Unrated players are NOT here — see scrape_on3_team_pages.py.

Two-pass strategy:
  1. Scrape topList for each year → ratings, to_school, from_school (from history)
  2. Backfill NULL composites for players already in the DB who have On3 slugs

Outputs: data/raw/on3_transfers_{year}.csv, data/raw/on3_transfers_combined.csv
Run: python etl/scrape_on3.py
"""

import re
import sys
import time
import json
import requests
import pandas as pd
import psycopg2
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import DB_CONFIG

OUTPUT_DIR = Path(__file__).parent.parent / "data" / "raw"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
YEARS = [2022, 2023, 2024, 2025]


def fetch_page(year: int, page: int = 1) -> dict:
    """Fetch one page of the On3 portal and return the __NEXT_DATA__ JSON dict."""
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


def extract_from_toplist(entry: dict, year: int, history_map: dict) -> dict | None:
    """
    Parse one topList entry into a flat record dict.

    from_school is derived from transferHistoryByPlayerKey — On3 stores a player's
    full college history there, sorted by startYear. The second-to-last school is
    the origin (the last is where they transferred to).
    """
    player = entry.get("player") or {}
    name   = player.get("fullName", "").strip()
    if not name:
        return None

    slug   = player.get("slug") or ""
    height = player.get("height") or ""
    weight = player.get("weight")
    pos    = (player.get("position") or {}).get("abbreviation") or \
             (player.get("position") or {}).get("name") or ""

    # Transfer composite — consensusRating is the blended On3 score (0–100)
    tr        = entry.get("transferRating") or {}
    composite = tr.get("consensusRating") or tr.get("rating")

    # Committed status and to_school — live in entry.status
    status    = entry.get("status") or {}
    committed = status.get("type") == "Committed"
    to_asset  = status.get("committedAsset") or {}
    to_school = to_asset.get("name", "").strip()

    # from_school — look up by player.key in history_map (keyed by player key as str)
    player_key  = str(player.get("key") or "")
    from_school = ""
    history     = history_map.get(player_key, [])
    if history:
        by_year = sorted(history, key=lambda h: h.get("startYear") or 0)
        # The entry before the most recent transfer is the origin school
        colleges = [h for h in by_year if (h.get("team") or {}).get("name")]
        if len(colleges) >= 2:
            from_school = (colleges[-2].get("team") or {}).get("name", "").strip()
        elif colleges:
            from_school = (colleges[-1].get("team") or {}).get("name", "").strip()

    return {
        "player_name":          name,
        "on3_slug":             slug,
        "position":             pos,
        "height":               height,
        "weight":               int(weight) if weight else None,
        "birth_year":           None,
        "from_school":          from_school,
        "to_school":            to_school,
        "recruiting_composite": round(float(composite), 2) if composite else None,
        "class_year":           None,
        "year":                 year,
        "committed":            committed,
    }


def scrape_year(year: int) -> pd.DataFrame:
    """
    Scrape all rated portal entrants for one year.

    The portal shows how many total players entered (playersEnteredCount), but
    only the top ~50 rated are available in topList. We paginate anyway in case
    On3 ever re-enables full pagination — the duplicate-key guard (seen_keys)
    stops the loop when pages repeat.
    """
    print(f"\nScraping {year}...")
    data = fetch_page(year, 1)
    pp   = data.get("props", {}).get("pageProps", {})

    top_list    = pp.get("topList", [])
    history_map = pp.get("transferHistoryByPlayerKey", {})
    total       = (pp.get("relatedModel") or {}).get("playersEnteredCount", len(top_list))
    print(f"  Portal shows {total} total entrants; fetching rated players across pages")

    records   = []
    seen_keys = set()

    def process_entries(entries):
        for entry in entries:
            pk = entry.get("psoKey")
            if pk in seen_keys:
                continue
            seen_keys.add(pk)
            rec = extract_from_toplist(entry, year, history_map)
            if rec:
                records.append(rec)

    process_entries(top_list)

    # Paginate — On3 returns the same top-50 rated on each page if structure is new;
    # keep fetching until we get a duplicate first entry or hit page 20.
    for page in range(2, 21):
        time.sleep(0.8)
        try:
            d2   = fetch_page(year, page)
            pp2  = d2.get("props", {}).get("pageProps", {})
            tl2  = pp2.get("topList", [])
            hm2  = pp2.get("transferHistoryByPlayerKey", {})
            if not tl2:
                break
            first_pk = tl2[0].get("psoKey")
            if first_pk in seen_keys and page > 2:
                # All pages returning same results — new On3 format caps at 50 rated
                break
            history_map.update(hm2)
            process_entries(tl2)
            if page % 5 == 0:
                print(f"  page {page} — {len(records)} records so far")
        except Exception as e:
            print(f"  page {page} error: {e}")
            break

    df = pd.DataFrame(records)
    if df.empty:
        return df

    # Keep only columns matching the old format for load_real_data.py compatibility
    keep = ["player_name", "on3_slug", "position", "height", "weight",
            "birth_year", "from_school", "to_school", "recruiting_composite",
            "class_year", "year", "committed"]
    df = df[[c for c in keep if c in df.columns]]
    print(f"  Done: {len(df)} records, {df['committed'].sum()} committed, "
          f"{df['recruiting_composite'].notna().sum()} with composite")
    return df


def backfill_composites_from_csv(combined_csv: Path) -> int:
    """Push On3 composite ratings from the CSV into the DB for any player with NULL composite."""
    if not combined_csv.exists():
        return 0
    df = pd.read_csv(combined_csv)
    df = df[df["recruiting_composite"].notna()].copy()
    df["name_clean"] = df["player_name"].str.strip().str.title().str.lower()
    best = df.sort_values("recruiting_composite", ascending=False).drop_duplicates("name_clean")

    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()
    cur.execute("SELECT player_id, LOWER(TRIM(full_name)) FROM players WHERE recruiting_composite IS NULL")
    null_map = {row[1]: row[0] for row in cur.fetchall()}

    updated = 0
    for _, row in best.iterrows():
        pid = null_map.get(row["name_clean"])
        if pid:
            cur.execute(
                "UPDATE players SET recruiting_composite = %s WHERE player_id = %s",
                (round(float(row["recruiting_composite"]), 2), pid)
            )
            updated += 1

    conn.commit()
    cur.close(); conn.close()
    return updated


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    all_frames = []

    for year in YEARS:
        df = scrape_year(year)
        if not df.empty:
            out_path = OUTPUT_DIR / f"on3_transfers_{year}.csv"
            df.to_csv(out_path, index=False)
            print(f"  Saved → {out_path.name}")
            all_frames.append(df)

    if not all_frames:
        print("No data scraped.")
        return

    combined = pd.concat(all_frames, ignore_index=True)
    combined_path = OUTPUT_DIR / "on3_transfers_combined.csv"
    combined.to_csv(combined_path, index=False)
    print(f"\nCombined: {len(combined)} rows → {combined_path.name}")

    print("\nBackfilling composite values into DB...")
    n = backfill_composites_from_csv(combined_path)
    print(f"  Updated {n} players with composite from fresh On3 data")


if __name__ == "__main__":
    main()
