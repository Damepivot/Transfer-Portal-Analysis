"""
Scrape ALL outgoing transfers from On3 school-level transfer pages.

Problem this solves:
  The main On3 portal page (topList) only shows the ~50 highest-rated players per year.
  Each school's transfer page shows every player who entered the portal from that school,
  rated or unrated. This gives us the ~1,600 committed transfers that never appear on
  the main list.

URL pattern:
  https://www.on3.com/college/{slug}/basketball/{year}/transfers/
  e.g. https://www.on3.com/college/iowa-hawkeyes/basketball/2025/transfers/

Data location in __NEXT_DATA__ JSON:
  pageProps.playerList.list      — all portal entrants from this school
  pageProps.transferHistoryByPlayerKey — where each player went (committedAsset.name)

Strategy:
  1. Scrape the main portal page for each year to collect slug → school_name pairs.
  2. For each slug × year, fetch the team transfer page (paginating if needed).
  3. from_school = the school we're scraping (slug's canonical name).
     to_school   = status.committedAsset.name for committed players.
  4. Save progress after each school to support resuming.
  5. Merge with existing on3_transfers_combined.csv (deduplicate on player+year).

Output: data/raw/on3_transfers_full.csv — same columns as on3_transfers_combined.csv.

Run: python etl/scrape_on3_team_pages.py
"""

import re
import sys
import json
import time
import requests
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

OUTPUT_DIR = Path(__file__).parent.parent / "data" / "raw"
PROGRESS_PATH = OUTPUT_DIR / "on3_team_pages_progress.csv"  # partial results saved here
OUTPUT_PATH   = OUTPUT_DIR / "on3_transfers_full.csv"

YEARS = [2022, 2023, 2024, 2025]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

# Output columns — must match on3_transfers_combined.csv for load_real_data.py
OUTPUT_COLS = [
    "player_name", "on3_slug", "position", "height", "weight",
    "birth_year", "from_school", "to_school", "recruiting_composite",
    "class_year", "year", "committed",
]


# ── Slug collection ────────────────────────────────────────────────────────────

def fetch_portal_page(year: int) -> dict:
    """Fetch the main portal page __NEXT_DATA__ for slug + school name extraction."""
    url = f"https://www.on3.com/transfer-portal/industry/basketball/{year}/"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.DOTALL)
    return json.loads(m.group(1)) if m else {}


def collect_slugs_from_portal(years: list[int]) -> dict[str, str]:
    """
    Return slug → canonical school name by mining 4 years of portal main pages.
    Sources used in each page:
      - topList[].status.committedAsset  (has .name and .slug)
      - transferHistoryByPlayerKey values[].team  (has .name and .slug)
      - teamRankings[].orgs[]            (school slug + name in ranking context)
    """
    slug_map: dict[str, str] = {}  # slug → name

    for year in years:
        print(f"  Collecting slugs from {year} portal page...")
        try:
            data = fetch_portal_page(year)
        except Exception as e:
            print(f"    Error fetching {year}: {e}")
            continue

        pp = data.get("props", {}).get("pageProps", {})

        # committedAsset on each topList entry
        for entry in pp.get("topList", []):
            asset = (entry.get("status") or {}).get("committedAsset") or {}
            _add(slug_map, asset)

        # history teams for each player
        for history_list in pp.get("transferHistoryByPlayerKey", {}).values():
            for h in (history_list if isinstance(history_list, list) else []):
                _add(slug_map, (h.get("team") or {}))

        # teamRankings
        for tr in pp.get("teamRankings", []):
            for org in tr.get("orgs", []):
                _add(slug_map, org)

        time.sleep(1.0)

    print(f"  Collected {len(slug_map)} unique school slugs")
    return slug_map


def _add(slug_map: dict, obj: dict):
    """Add a slug→name pair from an object with 'slug' and 'name' keys."""
    slug = (obj.get("slug") or "").strip()
    name = (obj.get("name") or "").strip()
    if slug and name and slug not in slug_map:
        slug_map[slug] = name


# ── Team page scraping ────────────────────────────────────────────────────────

def fetch_team_page(slug: str, year: int, page: int = 1) -> dict:
    """Fetch one page of a school's transfer page __NEXT_DATA__."""
    url = f"https://www.on3.com/college/{slug}/basketball/{year}/transfers/"
    params = {"page": page} if page > 1 else {}
    resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
    if resp.status_code == 404:
        return {}  # school didn't exist / wasn't D1 that year
    resp.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.DOTALL)
    return json.loads(m.group(1)) if m else {}


def extract_team_transfers(slug: str, school_name: str, year: int) -> list[dict]:
    """
    Fetch all pages for a school × year and return a list of transfer records.
    Each record includes from_school = school_name (the page we scraped).
    """
    records = []
    seen    = set()
    page    = 1

    while True:
        try:
            data = fetch_team_page(slug, year, page)
        except Exception as e:
            print(f"    [{slug} {year} p{page}] error: {e}")
            break

        if not data:
            break

        pp          = data.get("props", {}).get("pageProps", {})
        player_list = pp.get("playerList") or {}
        entries     = player_list.get("list", [])

        if not entries:
            break

        for entry in entries:
            pk = entry.get("psoKey")
            if pk in seen:
                continue
            seen.add(pk)

            player    = entry.get("player") or {}
            name      = player.get("fullName", "").strip()
            if not name:
                continue

            # Physical / profile fields
            height    = player.get("height") or ""
            weight    = player.get("weight")
            pos_obj   = player.get("position") or {}
            pos       = pos_obj.get("abbreviation") or pos_obj.get("name") or ""

            # Rating (only top ~50/year are rated; most will be None)
            tr        = entry.get("transferRating") or {}
            composite = tr.get("consensusRating") or tr.get("rating")

            # Destination school
            status    = entry.get("status") or {}
            committed = status.get("type") in ("Committed", "Enrolled")
            to_asset  = status.get("committedAsset") or {}
            to_school = to_asset.get("name", "").strip()

            records.append({
                "player_name":          name,
                "on3_slug":             player.get("slug") or "",
                "position":             pos,
                "height":               height,
                "weight":               int(weight) if weight else None,
                "birth_year":           None,
                "from_school":          school_name,       # implicit: the page we scraped
                "to_school":            to_school,
                "recruiting_composite": round(float(composite), 2) if composite else None,
                "class_year":           None,
                "year":                 year,
                "committed":            committed,
            })

        # Check pagination
        total_pages = player_list.get("pageCount", 1)
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.5)

    return records


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load any previously saved progress so we can resume
    done_pairs: set[tuple[str, int]] = set()
    progress_rows: list[dict] = []
    if PROGRESS_PATH.exists():
        prog_df = pd.read_csv(PROGRESS_PATH)
        # _scraped_slug and _scraped_year track which (slug, year) combos are done
        if "_scraped_slug" in prog_df.columns and "_scraped_year" in prog_df.columns:
            for _, r in prog_df.iterrows():
                done_pairs.add((r["_scraped_slug"], int(r["_scraped_year"])))
            progress_rows = prog_df.to_dict("records")
        print(f"Resuming — {len(done_pairs)} (slug, year) combos already scraped")

    # Step 1: collect slug → school name from portal pages
    print("\nStep 1: Collecting school slugs from portal pages...")
    slug_map = collect_slugs_from_portal(YEARS)

    if not slug_map:
        print("No slugs collected — check network access.")
        return

    # Step 2: for each slug × year, scrape the team transfer page
    total_slugs = len(slug_map)
    total_pairs = total_slugs * len(YEARS)
    remaining   = [(s, n, y) for s, n in slug_map.items() for y in YEARS
                   if (s, y) not in done_pairs]

    print(f"\nStep 2: Scraping {len(remaining)} team×year pages "
          f"(of {total_pairs} total, {len(done_pairs)} already done)...")

    for i, (slug, school_name, year) in enumerate(remaining, 1):
        print(f"  [{i}/{len(remaining)}] {school_name} ({slug}) — {year}", end=" ... ", flush=True)

        records = extract_team_transfers(slug, school_name, year)

        # Tag each record with which (slug, year) produced it for resume support
        for r in records:
            r["_scraped_slug"] = slug
            r["_scraped_year"] = year

        progress_rows.extend(records)
        done_pairs.add((slug, year))

        print(f"{len(records)} transfers")

        # Save progress every 10 schools
        if i % 10 == 0:
            pd.DataFrame(progress_rows).to_csv(PROGRESS_PATH, index=False)
            print(f"    Progress saved ({len(progress_rows)} rows total)")

        # Polite rate limit — stay well under On3's server limits
        time.sleep(0.8)

    # Final progress save
    pd.DataFrame(progress_rows).to_csv(PROGRESS_PATH, index=False)

    # Step 3: build clean output — drop internal tracking columns, deduplicate
    print("\nStep 3: Building final output CSV...")
    if not progress_rows:
        print("No data collected.")
        return

    df = pd.DataFrame(progress_rows)
    df = df.drop(columns=["_scraped_slug", "_scraped_year"], errors="ignore")

    # Keep output columns only
    for col in OUTPUT_COLS:
        if col not in df.columns:
            df[col] = None
    df = df[OUTPUT_COLS]

    # Deduplicate: same player + year (keep row with highest composite if duplicate)
    df["_name_lower"] = df["player_name"].str.strip().str.lower()
    df = (df
          .sort_values("recruiting_composite", ascending=False)
          .drop_duplicates(subset=["_name_lower", "year"])
          .drop(columns=["_name_lower"])
          .reset_index(drop=True))

    df.to_csv(OUTPUT_PATH, index=False)

    committed_count = df["committed"].sum()
    rated_count     = df["recruiting_composite"].notna().sum()
    print(f"\nDone: {len(df)} unique player-year records")
    print(f"  Committed: {committed_count}")
    print(f"  With composite rating: {rated_count}")
    print(f"  Saved → {OUTPUT_PATH.name}")

    # Step 4: compare with existing on3_transfers_combined.csv
    combined_path = OUTPUT_DIR / "on3_transfers_combined.csv"
    if combined_path.exists():
        old_df = pd.read_csv(combined_path)
        print(f"\nExisting on3_transfers_combined.csv: {len(old_df)} rows")
        print(f"New on3_transfers_full.csv:           {len(df)} rows")
        print(f"Net new transfers: +{len(df) - len(old_df)}")
        print("\nNext step: re-run load_real_data.py after pointing it at the full CSV")


if __name__ == "__main__":
    main()
