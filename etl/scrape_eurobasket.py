"""
Scrape pre-D1 European league stats for international players in the database.

Sources tried in order per player:
  1. EuroBasket.com player search by last name + club
  2. Basketball-Reference.com EuroLeague section (for top-tier players)

Output: data/raw/eurobasket_stats.csv
  Columns: player_name, team_name, league_name, country, season, games,
           ppg, rpg, apg, mpg, fg_pct, three_pct, source_url

Load step: python3 etl/load_real_data.py  (reads this CSV into player_pre_d1_stats table)

Run: python3 etl/scrape_eurobasket.py
"""

import re
import sys
import time
import requests
import pandas as pd
from pathlib import Path
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import DB_CONFIG
import psycopg2

OUTPUT_PATH = Path(__file__).parent.parent / "data" / "raw" / "eurobasket_stats.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _safe_float(val):
    try:
        return float(str(val).strip().replace(",", ".").replace("%", ""))
    except (ValueError, TypeError):
        return None


def search_eurobasket(player_name: str, club_name: str, country: str) -> dict | None:
    """
    Search EuroBasket.com for a player by name. Returns stats dict or None if not found.
    """
    last_name = player_name.split()[-1]
    url = f"https://www.eurobasket.com/players/playerStatsSearch.aspx?name={last_name.replace(' ', '+')}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "lxml")
        # Find player rows in results table
        rows = soup.select("table.players tr")
        if not rows:
            rows = soup.select("table tr")

        player_first = player_name.split()[0].lower()
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
            row_name = cells[0].get_text(strip=True).lower()
            row_club = cells[1].get_text(strip=True).lower() if len(cells) > 1 else ""

            if player_first in row_name and (
                club_name.lower() in row_club
                or country.lower() in row_name
                or country.lower() in row_club
            ):
                # Try to parse stats from this row
                link = cells[0].find("a")
                if link and link.get("href"):
                    player_url = "https://www.eurobasket.com" + link["href"]
                    return fetch_player_page(player_name, player_url)
    except Exception:
        pass
    return None


def fetch_player_page(player_name: str, url: str) -> dict | None:
    """Fetch a EuroBasket player page and extract their most recent season stats."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "lxml")
        # Find stats table
        tables = soup.find_all("table")
        for table in tables:
            headers_row = table.find("tr")
            if not headers_row:
                continue
            cols = [th.get_text(strip=True).lower() for th in headers_row.find_all(["th", "td"])]
            if "gp" in cols or "g" in cols or "pts" in cols or "ppg" in cols:
                rows = table.find_all("tr")[1:]
                if rows:
                    # Take most recent season (last row)
                    last_row = rows[-1]
                    cells = [td.get_text(strip=True) for td in last_row.find_all("td")]
                    if len(cells) >= 4:
                        return {
                            "player_name": player_name,
                            "source_url": url,
                            "raw_cols": cols,
                            "raw_vals": cells,
                        }
    except Exception:
        pass
    return None


def get_international_players() -> list[dict]:
    """Load international players from the DB who need pre-D1 stats."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()
    cur.execute("""
        SELECT p.full_name, p.origin_country, p.origin_league,
               t_from.name AS from_club, tr.season
        FROM transfers tr
        JOIN players p ON tr.player_id = p.player_id
        JOIN teams t_from ON tr.from_team_id = t_from.team_id
        JOIN conferences c_from ON t_from.conference_id = c_from.conference_id
        WHERE c_from.tier = 'international'
        ORDER BY p.full_name
    """)
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "player_name": r[0],
            "country":     r[1] or "",
            "league":      r[2] or "",
            "from_club":   r[3],
            "season":      r[4],
        }
        for r in rows
    ]


def main():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Resume support
    existing_names = set()
    if OUTPUT_PATH.exists():
        existing = pd.read_csv(OUTPUT_PATH)
        existing_names = set(existing["player_name"].str.lower())
        print(f"Resuming — already have {len(existing_names)} players")
        all_records = existing.to_dict("records")
    else:
        all_records = []

    players = get_international_players()
    print(f"International players to process: {len(players)}")

    for player in players:
        name   = player["player_name"]
        if name.lower() in existing_names:
            print(f"  Skipping {name} (already scraped)")
            continue

        country = player["country"]
        club    = player["from_club"]
        league  = player["league"]
        season  = player["season"]

        print(f"  Scraping {name} ({club}, {country}) ...")
        result = search_eurobasket(name, club, country)

        if result:
            raw_cols = result.get("raw_cols", [])
            raw_vals = result.get("raw_vals", [])

            def get_col(names):
                for n in names:
                    for i, c in enumerate(raw_cols):
                        if n in c and i < len(raw_vals):
                            return _safe_float(raw_vals[i])
                return None

            record = {
                "player_name": name,
                "team_name":   club,
                "league_name": league,
                "country":     country,
                "season":      season,
                "games":       get_col(["gp", "g", "games"]),
                "ppg":         get_col(["pts", "ppg", "points"]),
                "rpg":         get_col(["reb", "rpg", "rebound"]),
                "apg":         get_col(["ast", "apg", "assist"]),
                "mpg":         get_col(["min", "mpg", "minutes"]),
                "fg_pct":      get_col(["fg%", "fg pct", "2fg%"]),
                "three_pct":   get_col(["3p%", "3fg", "three"]),
                "source_url":  result.get("source_url", ""),
            }
            all_records.append(record)
            existing_names.add(name.lower())
            print(f"    Found stats: {record.get('ppg')} PPG, {record.get('rpg')} RPG")
        else:
            # Still record the player as attempted so we don't retry
            all_records.append({
                "player_name": name, "team_name": club, "league_name": league,
                "country": country, "season": season,
                "games": None, "ppg": None, "rpg": None, "apg": None,
                "mpg": None, "fg_pct": None, "three_pct": None, "source_url": "",
            })
            print(f"    No stats found — skipping")
            existing_names.add(name.lower())

        # Save checkpoint
        pd.DataFrame(all_records).to_csv(OUTPUT_PATH, index=False)
        time.sleep(3)

    df = pd.DataFrame(all_records)
    df.to_csv(OUTPUT_PATH, index=False)
    found = df["ppg"].notna().sum()
    print(f"\nDone. {found}/{len(df)} players with stats → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
