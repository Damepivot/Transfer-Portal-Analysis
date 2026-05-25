"""
Generate synthetic but realistic NCAA transfer data to test the full model pipeline.
Produces ~400 players, ~600 player-seasons, ~250 transfers across 2021-25.

Run: python etl/seed_synthetic.py
Then: psql -d ncaa_transfers -f sql/03_views.sql
"""

import sys
import random
import psycopg2
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from db import DB_CONFIG

random.seed(42)

SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25"]

# (school_name, conference_abbr) — 40 programs across all tiers
SCHOOLS = [
    # High Major
    ("Duke",          "ACC"), ("Kentucky",       "SEC"), ("Kansas",        "Big 12"),
    ("Michigan",      "Big Ten"), ("UConn",       "Big East"), ("Gonzaga",   "WCC"),
    ("Alabama",       "SEC"), ("Purdue",          "Big Ten"), ("Baylor",     "Big 12"),
    ("Tennessee",     "SEC"), ("Houston",         "Big 12"), ("Creighton",  "Big East"),

    # High Mid Major
    ("San Diego State","MWC"), ("Dayton",         "A-10"), ("VCU",          "A-10"),
    ("Memphis",       "AAC"), ("Wichita State",   "AAC"), ("New Mexico",    "MWC"),
    ("Davidson",      "A-10"), ("Saint Mary's",   "WCC"),

    # Mid Major
    ("Drake",         "MVC"), ("Loyola Chicago",  "MVC"), ("Murray State",  "OVC"),
    ("Cleveland State","Horizon"), ("Toledo",     "MAC"), ("UTEP",          "CUSA"),
    ("Appalachian State","Sun Belt"), ("Furman",  "SoCon"), ("UC Irvine",   "Big West"),
    ("Georgia State", "Sun Belt"),

    # Low Major
    ("Belmont",       "ASUN"), ("Eastern Kentucky","ASUN"), ("Winthrop",    "Big South"),
    ("Holy Cross",    "Patriot"), ("Bryant",        "NEC"), ("NJIT",         "America East"),
    ("Jackson State", "SWAC"), ("Norfolk State",   "MEAC"), ("Lehigh",       "Patriot"),
    ("Manhattan",     "MAAC"),
]

POSITIONS = ["G", "G", "G", "G/F", "G/F", "F", "F", "F/C", "C"]

FIRST_NAMES = [
    "Malik", "Jalen", "Tyler", "Marcus", "Devon", "Isaiah", "Jordan", "Chris",
    "Aaron", "Kevin", "Darius", "Cameron", "Zach", "Nathan", "Brandon", "Troy",
    "Elijah", "Miles", "Andre", "Donovan", "Trey", "Cade", "Hunter", "Luke",
    "Caleb", "Noah", "Jaylen", "Kofi", "Trayce", "Darius", "Quentin", "Grant",
]
LAST_NAMES = [
    "Johnson", "Williams", "Brown", "Davis", "Thompson", "Jackson", "Harris",
    "Martin", "Clark", "Lewis", "Robinson", "Walker", "Hall", "Young", "Allen",
    "King", "Wright", "Scott", "Green", "Baker", "Adams", "Nelson", "Hill",
    "Mitchell", "Carter", "Phillips", "Turner", "Jones", "Taylor", "Moore",
]

TIER_BPM_RANGE = {
    "high_major":     (2.0,  8.0),
    "high_mid_major": (1.0,  6.0),
    "mid_major":      (-1.0, 4.5),
    "low_major":      (-3.0, 3.0),
}

CONF_TIER = {
    "ACC": "high_major", "SEC": "high_major", "Big 12": "high_major",
    "Big Ten": "high_major", "Big East": "high_major", "WCC": "high_mid_major",
    "AAC": "high_mid_major", "MWC": "high_mid_major", "A-10": "high_mid_major",
    "MVC": "mid_major", "MAC": "mid_major", "CUSA": "mid_major",
    "Sun Belt": "mid_major", "SoCon": "mid_major", "Big West": "mid_major",
    "Horizon": "mid_major", "OVC": "mid_major",
    "ASUN": "low_major", "Big South": "low_major", "NEC": "low_major",
    "Patriot": "low_major", "SWAC": "low_major", "MEAC": "low_major",
    "America East": "low_major", "MAAC": "low_major",
}

# Expected BPM delta when moving between tiers
# Positive = player typically improves, negative = tough jump
TIER_JUMP_DELTA = {
    ("low_major",     "mid_major"):      1.2,
    ("low_major",     "high_mid_major"): 0.4,
    ("low_major",     "high_major"):    -0.8,
    ("mid_major",     "high_mid_major"): 0.8,
    ("mid_major",     "high_major"):    -0.5,
    ("high_mid_major","high_major"):    -0.3,
    ("high_major",    "high_mid_major"): 1.5,
    ("high_major",    "mid_major"):      2.0,
    ("mid_major",     "low_major"):      2.5,
    ("high_mid_major","mid_major"):      1.8,
    ("high_major",    "high_major"):     0.1,
    ("high_mid_major","high_mid_major"): 0.0,
    ("mid_major",     "mid_major"):      0.2,
    ("low_major",     "low_major"):      0.0,
}


def rnd(lo, hi, decimals=2):
    return round(random.uniform(lo, hi), decimals)


def gen_stats(tier, bpm_base=None):
    lo, hi = TIER_BPM_RANGE[tier]
    bpm = round(bpm_base if bpm_base is not None else random.uniform(lo, hi), 2)
    ppg_base = {"high_major": 10.5, "high_mid_major": 12.0, "mid_major": 13.5, "low_major": 13.0}[tier]
    return {
        "games":   random.randint(22, 35),
        "games_started": random.randint(5, 32),
        "mpg":     rnd(14, 34),
        "ppg":     round(max(2.0, ppg_base + bpm * 0.9 + rnd(-3, 3)), 1),
        "rpg":     rnd(1.5, 8.5),
        "apg":     rnd(0.5, 6.0),
        "spg":     rnd(0.3, 1.8),
        "bpg":     rnd(0.1, 2.0),
        "fg_pct":  rnd(0.38, 0.55),
        "three_pct": rnd(0.28, 0.43),
        "ft_pct":  rnd(0.62, 0.85),
        "ts_pct":  rnd(0.49, 0.63),
        "efg_pct": rnd(0.44, 0.60),
        "usg_pct": rnd(14.0, 30.0),
        "bpm":     bpm,
        "porpag":  round(bpm * 0.7 + rnd(-0.5, 0.5), 2),
    }


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()

    # ── Pull conference IDs ───────────────────────────────────────────────────
    cur.execute("SELECT abbreviation, conference_id FROM conferences")
    conf_id_map = {abbr: cid for abbr, cid in cur.fetchall()}
    # fallback for any abbr not in DB — insert as mid_major
    def get_conf_id(abbr):
        if abbr in conf_id_map:
            return conf_id_map[abbr]
        cur.execute(
            "INSERT INTO conferences (name, abbreviation, tier) VALUES (%s,%s,'mid_major') RETURNING conference_id",
            (abbr, abbr)
        )
        cid = cur.fetchone()[0]
        conf_id_map[abbr] = cid
        return cid

    # ── Insert teams (one row per school per season) ──────────────────────────
    print("Inserting teams...")
    team_id_map = {}  # (school_name, season) -> team_id
    for school, conf_abbr in SCHOOLS:
        for season in SEASONS:
            cid = get_conf_id(conf_abbr)
            cur.execute(
                "INSERT INTO teams (name, conference_id, season) VALUES (%s,%s,%s) RETURNING team_id",
                (school, cid, season)
            )
            team_id_map[(school, season)] = cur.fetchone()[0]
    conn.commit()
    print(f"  {len(team_id_map)} team-season rows")

    # ── Generate players ──────────────────────────────────────────────────────
    print("Inserting players...")
    used_names = set()
    player_ids = []
    composites = {}  # player_id -> recruiting composite

    for _ in range(420):
        while True:
            name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
            if name not in used_names:
                used_names.add(name)
                break
        pos       = random.choice(POSITIONS)
        comp      = round(random.gauss(80, 8), 1)
        comp      = max(60.0, min(99.0, comp))
        class_yr  = random.choice(["FR", "SO", "JR", "SR", "Grad"])
        height    = random.randint(70, 84)
        cur.execute(
            """INSERT INTO players (full_name, position, height_in, class_year, recruiting_composite)
               VALUES (%s,%s,%s,%s,%s) RETURNING player_id""",
            (name, pos, height, class_yr, comp)
        )
        pid = cur.fetchone()[0]
        player_ids.append(pid)
        composites[pid] = comp
    conn.commit()
    print(f"  {len(player_ids)} players")

    # ── Assign players to teams + generate player_seasons ────────────────────
    print("Inserting player seasons...")
    # Each player starts on one team, may transfer once
    player_home = {}  # pid -> (school, season_index, base_bpm)
    season_rows = []

    for pid in player_ids:
        school, conf_abbr = random.choice(SCHOOLS)
        tier      = CONF_TIER.get(conf_abbr, "mid_major")
        lo, hi    = TIER_BPM_RANGE[tier]
        base_bpm  = random.uniform(lo, hi)
        start_idx = random.randint(0, 2)  # start in one of first 3 seasons
        player_home[pid] = (school, start_idx, base_bpm, tier)

    # Insert base season stats for each player
    for pid, (school, start_idx, base_bpm, tier) in player_home.items():
        for i in range(start_idx, min(start_idx + 2, len(SEASONS))):
            season = SEASONS[i]
            tid = team_id_map.get((school, season))
            if not tid:
                continue
            noise = rnd(-0.8, 0.8)
            stats = gen_stats(tier, base_bpm + noise)
            season_rows.append((pid, tid, season, stats))

    for pid, tid, season, s in season_rows:
        cur.execute(
            """INSERT INTO player_seasons
               (player_id,team_id,season,games,games_started,mpg,ppg,rpg,apg,spg,bpg,
                fg_pct,three_pct,ft_pct,ts_pct,efg_pct,usg_pct,bpm,porpag)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (player_id,team_id,season) DO NOTHING""",
            (pid, tid, season, s["games"], s["games_started"], s["mpg"], s["ppg"],
             s["rpg"], s["apg"], s["spg"], s["bpg"], s["fg_pct"], s["three_pct"],
             s["ft_pct"], s["ts_pct"], s["efg_pct"], s["usg_pct"], s["bpm"], s["porpag"])
        )
    conn.commit()
    print(f"  {len(season_rows)} player-season rows")

    # ── Generate transfers ────────────────────────────────────────────────────
    print("Inserting transfers...")
    transfer_pool = [(pid, data) for pid, data in player_home.items()
                     if data[1] < 3]  # must have at least one season after transfer

    random.shuffle(transfer_pool)
    transfer_pool = transfer_pool[:260]

    transfer_count = 0
    for pid, (from_school, start_idx, base_bpm, from_tier) in transfer_pool:
        # Pick a different destination school
        dest_options = [(s, c) for s, c in SCHOOLS if s != from_school]
        to_school, to_conf = random.choice(dest_options)
        to_tier = CONF_TIER.get(to_conf, "mid_major")

        # Transfer season = one after their first recorded season
        transfer_season_idx = start_idx + 1
        if transfer_season_idx >= len(SEASONS):
            continue
        transfer_season = SEASONS[transfer_season_idx]

        from_tid = team_id_map.get((from_school, transfer_season))
        to_tid   = team_id_map.get((to_school, transfer_season))
        if not from_tid or not to_tid:
            continue

        cur.execute(
            """INSERT INTO transfers (player_id, from_team_id, to_team_id, season, transfer_type)
               VALUES (%s,%s,%s,%s,'portal') ON CONFLICT DO NOTHING""",
            (pid, from_tid, to_tid, transfer_season)
        )

        # Insert post-transfer player_season with realistic delta
        delta = TIER_JUMP_DELTA.get((from_tier, to_tier), 0.0)
        noise = rnd(-1.5, 1.5)
        new_bpm = base_bpm + delta + noise
        stats_after = gen_stats(to_tier, new_bpm)

        cur.execute(
            """INSERT INTO player_seasons
               (player_id,team_id,season,games,games_started,mpg,ppg,rpg,apg,spg,bpg,
                fg_pct,three_pct,ft_pct,ts_pct,efg_pct,usg_pct,bpm,porpag)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (player_id,team_id,season) DO NOTHING""",
            (pid, to_tid, transfer_season, stats_after["games"], stats_after["games_started"],
             stats_after["mpg"], stats_after["ppg"], stats_after["rpg"], stats_after["apg"],
             stats_after["spg"], stats_after["bpg"], stats_after["fg_pct"], stats_after["three_pct"],
             stats_after["ft_pct"], stats_after["ts_pct"], stats_after["efg_pct"],
             stats_after["usg_pct"], stats_after["bpm"], stats_after["porpag"])
        )
        transfer_count += 1

    conn.commit()
    print(f"  {transfer_count} transfers")

    # ── Insert team_seasons (wins/losses + adj efficiency) ───────────────────
    print("Inserting team seasons...")
    cur.execute("SELECT team_id, conference_id, season FROM teams")
    all_teams = cur.fetchall()
    cur.execute("SELECT conference_id, tier FROM conferences")
    tier_map = {cid: tier for cid, tier in cur.fetchall()}

    for tid, cid, season in all_teams:
        tier = tier_map.get(cid, "mid_major")
        base_wins = {"high_major": 18, "high_mid_major": 20, "mid_major": 16, "low_major": 13}[tier]
        wins   = max(3, min(35, int(random.gauss(base_wins, 5))))
        losses = random.randint(3, max(3, 34 - wins))
        adj_eff = round(random.gauss(
            {"high_major": 8, "high_mid_major": 4, "mid_major": 0, "low_major": -5}[tier], 6
        ), 2)
        cur.execute(
            """INSERT INTO team_seasons (team_id, season, wins, losses, adj_efficiency)
               VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (tid, season, wins, losses, adj_eff)
        )
    conn.commit()
    print(f"  {len(all_teams)} team-season rows")

    cur.close()
    conn.close()
    print("\nDone. Run: psql -d ncaa_transfers -f sql/03_views.sql")


if __name__ == "__main__":
    main()
