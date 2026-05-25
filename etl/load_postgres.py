"""
Load cleaned CSVs into PostgreSQL.
Run AFTER: 01_schema.sql, 02_seed_conferences.sql, 03_views.sql are applied.
Run AFTER: scrape_barttorvik.py and load_kaggle_transfers.py have produced their outputs.

Usage:
    python etl/load_postgres.py

Requires:
    pip install psycopg2-binary pandas
"""

import sys
import psycopg2
import pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from db import DB_CONFIG

DATA_DIR = Path(__file__).parent.parent / "data"


def get_conn():
    try:
        return psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"Could not connect to PostgreSQL: {e}")
        print("Make sure PostgreSQL is running: brew services start postgresql@16")
        sys.exit(1)


def load_conferences(conn):
    """conferences are loaded via SQL COPY in 02_seed_conferences.sql — skip here."""
    pass


def load_teams(conn, transfers_df: pd.DataFrame, barttorvik_df: pd.DataFrame):
    """Insert all unique team names, linked to their conference."""
    print("Loading teams...")
    cur = conn.cursor()

    # Pull the conference lookup
    cur.execute("SELECT name, abbreviation, conference_id FROM conferences")
    conf_rows = cur.fetchall()
    conf_by_abbr = {abbr: cid for _, abbr, cid in conf_rows}
    conf_by_name = {name: cid for name, _, cid in conf_rows}

    # Collect all team+season+conf combinations from BartTorvik
    team_seasons = set()
    for _, row in barttorvik_df.iterrows():
        team_seasons.add((row["team"], row.get("conf", ""), str(row["season"])))

    inserted = 0
    for team_name, conf_abbr, season in sorted(team_seasons):
        if not team_name:
            continue
        conf_id = conf_by_abbr.get(conf_abbr) or conf_by_name.get(conf_abbr)
        cur.execute(
            """
            INSERT INTO teams (name, conference_id, season)
            VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (team_name, conf_id, season),
        )
        inserted += cur.rowcount

    conn.commit()
    cur.close()
    print(f"  Inserted {inserted} team-season rows")


def load_players(conn, transfers_df: pd.DataFrame):
    print("Loading players...")
    cur = conn.cursor()

    players = (
        transfers_df[["player_name", "position", "class_year", "recruiting_composite"]]
        .drop_duplicates(subset=["player_name"])
        .dropna(subset=["player_name"])
    )

    inserted = 0
    for _, row in players.iterrows():
        cur.execute(
            """
            INSERT INTO players (full_name, position, class_year, recruiting_composite)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                row["player_name"],
                row.get("position"),
                row.get("class_year"),
                row.get("recruiting_composite"),
            ),
        )
        inserted += cur.rowcount

    conn.commit()
    cur.close()
    print(f"  Inserted {inserted} players")


def load_transfers(conn, transfers_df: pd.DataFrame):
    print("Loading transfers...")
    cur = conn.cursor()

    # Build player lookup
    cur.execute("SELECT player_id, full_name FROM players")
    player_map = {name: pid for pid, name in cur.fetchall()}

    # Build team lookup: name+season → team_id
    cur.execute("SELECT team_id, name, season FROM teams")
    team_map = {(name, season): tid for tid, name, season in cur.fetchall()}

    inserted = skipped = 0
    for _, row in transfers_df.iterrows():
        player_id  = player_map.get(row["player_name"])
        from_tid   = team_map.get((row["from_school"], row["season"]))
        to_tid     = team_map.get((row["to_school"], row["season"]))

        if not all([player_id, from_tid, to_tid]):
            skipped += 1
            continue

        cur.execute(
            """
            INSERT INTO transfers (player_id, from_team_id, to_team_id, season, transfer_type)
            VALUES (%s, %s, %s, %s, 'portal')
            ON CONFLICT DO NOTHING
            """,
            (player_id, from_tid, to_tid, row["season"]),
        )
        inserted += cur.rowcount

    conn.commit()
    cur.close()
    print(f"  Inserted {inserted} transfers, skipped {skipped} (unmatched teams/players)")


def load_player_seasons(conn, barttorvik_df: pd.DataFrame):
    print("Loading player seasons...")
    cur = conn.cursor()

    cur.execute("SELECT player_id, full_name FROM players")
    player_map = {name: pid for pid, name in cur.fetchall()}

    cur.execute("SELECT team_id, name, season FROM teams")
    team_map = {(name, season): tid for tid, name, season in cur.fetchall()}

    inserted = skipped = 0
    for _, row in barttorvik_df.iterrows():
        player_id = player_map.get(row["player_name"])
        team_id   = team_map.get((row["team"], row["season"]))

        if not player_id or not team_id:
            skipped += 1
            continue

        cur.execute(
            """
            INSERT INTO player_seasons
                (player_id, team_id, season, games, usg_pct, efg_pct, ts_pct, ppg, rpg, apg)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (player_id, team_id, season) DO NOTHING
            """,
            (
                player_id, team_id, row["season"],
                row.get("gp"),
                row.get("usg_pct"),
                row.get("efg_pct"),
                row.get("ts_pct"),
                row.get("ppg"),
                row.get("rpg"),
                row.get("apg"),
            ),
        )
        inserted += cur.rowcount

    conn.commit()
    cur.close()
    print(f"  Inserted {inserted} player seasons, skipped {skipped}")


def main():
    transfers_path   = DATA_DIR / "transfers_clean.csv"
    barttorvik_path  = DATA_DIR / "barttorvik_players.csv"

    missing = [p for p in [transfers_path, barttorvik_path] if not p.exists()]
    if missing:
        print("Missing data files:")
        for p in missing:
            print(f"  {p}")
        print("\nRun these first:")
        print("  python etl/scrape_barttorvik.py")
        print("  python etl/load_kaggle_transfers.py")
        sys.exit(1)

    transfers_df  = pd.read_csv(transfers_path)
    barttorvik_df = pd.read_csv(barttorvik_path)

    print(f"Transfers:  {len(transfers_df)} rows")
    print(f"BartTorvik: {len(barttorvik_df)} rows")

    conn = get_conn()
    try:
        load_teams(conn, transfers_df, barttorvik_df)
        load_players(conn, transfers_df)
        load_transfers(conn, transfers_df)
        load_player_seasons(conn, barttorvik_df)
    finally:
        conn.close()

    print("\nAll done. Next: refresh the materialized view in psql:")
    print("  REFRESH MATERIALIZED VIEW tier_pair_expectations;")


if __name__ == "__main__":
    main()
