# NCAA Transfer Market Analytics

Analyzes NCAA men's basketball transfer portal data (2021–2026, post-NIL era). Classifies programs by conference tier, quantifies player performance before/after transfers using a role- and tier-adjusted scoring model, and outputs data-driven recruitment profile recommendations by league tier.

> **581 scored transfers · 100% real CBB Reference data · zero estimation**

---

## Charts

### Transfer Success Rate by Tier Movement
![Tier heatmap](assets/01_tier_heatmap.png)

### High-Major Success Rate by Origin Tier
![Origin tier success](assets/02_origin_tier_success.png)

### Top 15 Transfers by Context Score
![Top transfers](assets/03_top_transfers.png)

### Recruiting Composite vs Transfer Success
![Recruit vs success](assets/04_recruit_vs_success.png)

### Success Rate by Position & Origin Tier (High-Major Destinations)
![Position success](assets/05_position_success.png)

### Verdict Distribution
![Verdict distribution](assets/06_verdict_distribution.png)

---

## Conference Tiers
| Tier | Conferences |
|------|-------------|
| `high_major` | ACC, Big Ten, Big 12, SEC, Big East, Pac-12 |
| `high_mid_major` | AAC, Mountain West, WCC, Atlantic 10 |
| `mid_major` | MVC, MAC, CUSA, Sun Belt, CAA, Horizon, Big West, SoCon |
| `low_major` | Big South, NEC, OVC, SWAC, MEAC, Patriot, America East, WAC, Ivy, ASUN |

---

## Scoring Model

### Context Score (primary metric)
```
context_score = bpm_after × tier_weight × role_weight
```

**Tier weight** — the higher the destination, the more credit for the same BPM:
| Destination | Weight |
|-------------|--------|
| high_major | 1.35 |
| high_mid_major | 1.15 |
| mid_major | 0.90 |
| low_major | 0.75 |

**Role weight** — producing BPM in a smaller role is harder:
| Usage change | Weight |
|-------------|--------|
| Lost 5+ pts USG% | 1.20 |
| Lost 2–5 pts USG% | 1.10 |
| Gained 2–5 pts USG% | 0.95 |
| Gained 5+ pts USG% | 0.85 |
| Minimal change | 1.00 |

**Why this matters:** A 6'10" low-major big going to a role at a Power 5 program may drop raw BPM but hold value — the context score surfaces that. A high-usage low-major guard who lands a bigger role at mid-major and posts production gets credit for both the tier competition and the usage context.

### Verdicts (based on absolute bpm_after)
Context score is a *ranking* metric — it rewards harder environments. Verdicts use a fixed BPM bar so the standard is consistent across tiers.

| BPM After | Verdict |
|-----------|---------|
| > 2.0 | High Value |
| > 0.5 | Solid Addition |
| > −0.5 | Neutral |
| ≤ −0.5 | Didn't Fit |

### Supporting stats
- `bpm_change` — raw BPM delta (after minus before), simple and transparent
- `usg_change` — usage rate change, shows role expansion/contraction
- `transfer_premium` — BPM vs peer baseline (players who made same tier jump with similar recruiting composite)

---

## Six Reports

### 1 — Individual Transfer Scores
Full player-level breakdown: context score, BPM change, role change, verdict.
View: `individual_transfer_scores`

### 2 — Team Transfer Portfolio
Per-team transfer class: origin tier mix, avg context score, avg BPM, wins/efficiency.
View: `team_transfer_report`

### 3 — League Recruitment Profiles
**Tier/league level** — not individual programs. What profile (position, BPM floor, usage, height, weight) succeeds at each tier when recruited from each origin tier.
View: `recruitment_profiles`

### 4 — League Transfer Trends
Season-by-season breakdown by tier pair and position: success rates, avg context score, avg physical profile of transfers.
View: `league_transfer_trends`

### 5 — Player Fit Finder (dashboard only)
Player inputs their profile (position, height, weight, birth year, BPM, USG%, current tier). System finds comparable transfers and shows which destination tiers they thrived in.
Tab: Player Fit Finder in `app.py`

### 6 — Coach Search (dashboard only)
Coaches input their program tier, conference, season, and role need. System returns Strong Matches and Matches from the historical transfer pool with CBB Reference profile links.
Tab: Coach Search in `app.py`

---

## Player Attributes
| Field | Source | Notes |
|-------|--------|-------|
| Position | Portal / On3 | G, G/F, F, F/C, C |
| Height | On3 | Stored as inches |
| Weight | On3 | Stored as lbs |
| Birth year | On3 | Used for age context |
| Recruiting composite | Portal / On3 | 0–100 scale |

Height and weight are included in all views for correlation analysis. Once re-scraped On3 data is loaded, they populate in `recruitment_profiles` and `league_transfer_trends` to show the physical profile of successful transfers by tier and position.

---

## Data Sources
| Data | Source | Method |
|------|--------|--------|
| Transfer records 2016–2022 | Kaggle (`collegeBasketBallTransferMen.csv`) | Downloaded via Kaggle CLI |
| Transfer records 2023–2025 | On3 transfer portal | `etl/scrape_on3.py` |
| Team stats (wins, ADJOE) | College Basketball Reference CSVs | `data/raw/cbb21.csv` – `cbb26.csv` |
| Real player BPM / TS% / USG% | CBB Reference school pages | `etl/scrape_cbb_reference.py` |
| Height / weight / birth year | On3 (re-scrape) | `etl/scrape_on3.py` (updated) |
| Conference tiers | Manual seed CSV | `data/conferences_seed.csv` |

---

## Project Structure
```
TransferPortal/
├── sql/
│   ├── 01_schema.sql              ← all 6 tables + enum types
│   ├── 02_seed_conferences.sql    ← loads 31 conferences with tier
│   └── 03_views.sql               ← 5 analytical views (incl. materialized)
├── etl/
│   ├── seed_synthetic.py          ← synthetic data for model validation
│   ├── scrape_on3.py              ← scrapes On3 portal (now includes height/weight/birth year)
│   ├── scrape_cbb_reference.py    ← scrapes real BPM/TS%/USG% per school-season
│   └── load_real_data.py          ← master ETL: team stats → transfers → real stat overrides
├── data/
│   ├── conferences_seed.csv       ← static tier lookup (31 conferences)
│   └── raw/
│       ├── cbb21.csv – cbb26.csv  ← team stats per season
│       ├── collegeBasketBallTransferMen.csv  ← Kaggle portal data
│       ├── on3_transfers_combined.csv        ← On3 scraper output
│       └── cbb_player_stats.csv             ← CBB Reference real player stats
├── app.py                         ← 6-tab Streamlit dashboard
├── db.py                          ← shared DB connection config (reads .env)
├── requirements.txt
└── setup.sh                       ← one-shot environment setup
```

---

## Database Schema
| Table | Description |
|-------|-------------|
| `conferences` | Tier classification for all 31 conferences |
| `teams` | School + conference + season (handles realignment year-by-year) |
| `players` | Name, position, height (in), weight (lbs), birth year, class year, recruiting composite |
| `transfers` | From/to team, season, transfer type |
| `player_seasons` | Per-season stats: PPG, BPM, USG%, TS%, PORPAG, etc. |
| `team_seasons` | Team wins/losses + adjusted efficiency |

---

## SQL Views
| View | Type | Description |
|------|------|-------------|
| `tier_pair_expectations` | Materialized | Avg BPM baseline by tier-pair + recruiting tier |
| `individual_transfer_scores` | View | Full player scoring: context_score, bpm_change, usg_change, verdict |
| `team_transfer_report` | View | Per-team transfer class outcomes |
| `recruitment_profiles` | View | League-level: profile of successful transfers by dest/origin tier + position |
| `league_transfer_trends` | View | Season-by-season success rates, physical profile, by tier pair + position |

---

## Run Order
```bash
# 1. Copy env template and fill in your Postgres credentials
cp .env.example .env

# 2. Install dependencies and create the database
bash setup.sh

# 3. Scrape On3 portal data — includes height, weight, birth year
python3 etl/scrape_on3.py

# 4. Scrape real player stats from CBB Reference (~35 min, resumable)
python3 etl/scrape_cbb_reference.py

# 5. Load all data into PostgreSQL
python3 etl/load_real_data.py

# 6. Launch dashboard
streamlit run app.py
```

---

## Dashboard — app.py
Six-tab Streamlit app running at `http://localhost:8501`

| Tab | Content |
|-----|---------|
| Transfer Overview | Season KPIs, tier-to-tier flow heatmap, volume by season |
| Individual Scores | Context score bar chart, verdict breakdown, full table with BPM Δ / USG Δ |
| Team Portfolio | Transfer class composition, origin tier mix, premium vs wins |
| Recruit Profiles | League-level stat floors + physical profile by tier and position |
| Player Fit Finder | Input your profile → see comparable transfers + best destination tiers |
| Coach Search | Filter by tier, conference, position, BPM/USG floor → ranked Strong Matches + Matches with CBB Reference links |

Sidebar filters: season, position, destination tier.

---

## Current Status
- [x] PostgreSQL schema with height, weight, birth year, recruiting composite
- [x] 31 conferences seeded across 4 tiers
- [x] CBB team stats loaded (cbb21–cbb26, 6 seasons)
- [x] Kaggle transfer portal data loaded (2021–2025)
- [x] On3 transfer data scraped and loaded (2023–2025)
- [x] Real BPM / TS% / USG% from CBB Reference (14,591 player-season rows, zero estimation)
- [x] Context score model live — verdicts use absolute bpm_after, context_score used for ranking only
- [x] 5 SQL views including materialized peer baseline (`tier_pair_expectations`)
- [x] 581 fully scored transfers — all backed by real CBB Reference data
- [x] Data cleaning: small-sample BPM nulled (< 8 games), duplicate transfers resolved, view Cartesian products fixed
- [x] 6-tab Streamlit dashboard: Overview, Individual Scores, Team Portfolio, Recruit Profiles, Player Fit Finder, Coach Search
- [x] Coach Search: Strong Match / Match tiers, USG%-based role projection, CBB Reference links
