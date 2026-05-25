"""
Scrape per-player advanced stats from BartTorvik for seasons 2021-22 through 2024-25.
Outputs: data/barttorvik_players.csv

BartTorvik exposes player data as a JSON endpoint:
  https://barttorvik.com/playerstat.php?year=2023&type=player&csv=1

Usage:
    python etl/scrape_barttorvik.py
"""

import time
import requests
import pandas as pd
from pathlib import Path
from normalize import canonical_team, normalize_season

OUTPUT_PATH = Path(__file__).parent.parent / "data" / "barttorvik_players.csv"

SEASONS = {
    "2021-22": 2022,
    "2022-23": 2023,
    "2023-24": 2024,
    "2024-25": 2025,
}

# BartTorvik CSV column headers (as of 2024)
BARTTORVIK_COLS = [
    "player_name", "team", "conf", "gp", "min_pct",
    "o_rtg", "usg_pct", "efg_pct", "ts_pct",
    "orb_pct", "drb_pct", "ast_pct", "to_pct",
    "blk_pct", "stl_pct", "ftr",
    "ppg", "rpg", "apg",
    "adjo", "adjd",
    "year",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def fetch_season(season_label: str, year_int: int) -> pd.DataFrame:
    url = f"https://barttorvik.com/playerstat.php?year={year_int}&type=player&csv=1"
    print(f"  Fetching {season_label} ({url})")

    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    lines = resp.text.strip().splitlines()
    rows = [line.split(",") for line in lines]

    # BartTorvik CSV sometimes has a header row, sometimes not — detect it
    if rows and rows[0][0].lower() in ("player", "player_name", "name"):
        rows = rows[1:]

    records = []
    for row in rows:
        if len(row) < 10:
            continue
        try:
            record = {
                "player_name": row[0].strip().strip('"'),
                "team_raw":    row[1].strip().strip('"'),
                "team":        canonical_team(row[1].strip().strip('"')),
                "conf":        row[2].strip() if len(row) > 2 else "",
                "gp":          _safe_int(row[3]),
                "min_pct":     _safe_float(row[4]),
                "o_rtg":       _safe_float(row[5]),
                "usg_pct":     _safe_float(row[6]),
                "efg_pct":     _safe_float(row[7]),
                "ts_pct":      _safe_float(row[8]),
                "orb_pct":     _safe_float(row[9]),
                "drb_pct":     _safe_float(row[10]) if len(row) > 10 else None,
                "ast_pct":     _safe_float(row[11]) if len(row) > 11 else None,
                "to_pct":      _safe_float(row[12]) if len(row) > 12 else None,
                "blk_pct":     _safe_float(row[13]) if len(row) > 13 else None,
                "stl_pct":     _safe_float(row[14]) if len(row) > 14 else None,
                "ppg":         _safe_float(row[15]) if len(row) > 15 else None,
                "rpg":         _safe_float(row[16]) if len(row) > 16 else None,
                "apg":         _safe_float(row[17]) if len(row) > 17 else None,
                "season":      season_label,
            }
            records.append(record)
        except (IndexError, ValueError):
            continue

    df = pd.DataFrame(records)
    print(f"    -> {len(df)} players")
    return df


def _safe_float(val):
    try:
        return float(str(val).strip())
    except (ValueError, TypeError):
        return None


def _safe_int(val):
    try:
        return int(str(val).strip())
    except (ValueError, TypeError):
        return None


def main():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    all_frames = []

    for season_label, year_int in SEASONS.items():
        try:
            df = fetch_season(season_label, year_int)
            all_frames.append(df)
        except requests.HTTPError as e:
            print(f"  ERROR fetching {season_label}: {e}")
        time.sleep(2)  # be polite — 2 second delay between requests

    if not all_frames:
        print("No data fetched. Check your network connection.")
        return

    combined = pd.concat(all_frames, ignore_index=True)

    # Drop rows with no player name
    combined = combined[combined["player_name"].str.strip() != ""]

    # Sort for readability
    combined = combined.sort_values(["season", "team", "player_name"]).reset_index(drop=True)

    combined.to_csv(OUTPUT_PATH, index=False)
    print(f"\nSaved {len(combined)} rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
