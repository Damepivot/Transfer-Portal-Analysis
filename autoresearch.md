# Autoresearch: NCAA Transfer Portal Data Expansion

## Objective
Maximize the number of D1 men's basketball transfers loaded into the `ncaa_transfers` PostgreSQL database. Current baseline: 1,859 transfers (2020–2026). Target: 5,000+. Each experiment tries a different free/public data source or scraping approach to add more portal entries with from_school → to_school coverage.

## Metrics
- **Primary**: transfers_loaded (integer, higher is better)
- **Secondary**: scored_transfers (transfers with BPM in view), unique_players, skip_rate_pct

## How to Run
`./autoresearch.sh` — outputs `METRIC name=number` lines.

## Files in Scope
- `etl/load_real_data.py` — master ETL, loads all sources into DB
- `etl/scrape_on3.py` — On3 portal scraper
- `etl/scrape_barttorvik.py` — Barttorvik stats scraper
- `etl/scrape_cbb_reference.py` — CBB Reference BPM scraper
- `data/raw/` — raw CSV files (add new sources here)
- `autoresearch.sh` — benchmark script
- `autoresearch.md` — this file

## Off Limits
- `app.py`, `db.py`, `sql/`, `assets/` — dashboard and schema untouched
- `data/raw/cbb_player_stats.csv` — don't delete (accumulated scraper results)
- Do NOT drop the database or truncate without re-loading

## Constraints
- Only free/public data sources (no paid APIs)
- Must load into existing schema (transfers, players, player_seasons tables)
- Respect rate limits: 3s sleep on CBB Reference, 2s on other sites
- Transfers must have valid from_school AND to_school (both D1 or sub_d1 fallback)

## What's Been Tried

### Baseline (1,859 transfers)
Sources: On3 team page scraper (2,414 committed, 1,103 loaded) + Kaggle portal CSV (728 loaded) + 19 international. Main skip reasons: 672 same-school re-commits filtered by design; ~600 more from unmatched school names or non-D1 destinations.

### Experiments to Try (Priority Order)
1. **Barttorvik transfer data** — barttorvik.com has a dedicated transfer page with all portal entrants. URL: `https://barttorvik.com/trankings.php` or API endpoint. Should have ALL D1 portal entries, not just rated ones.
2. **ESPN transfer tracker** — ESPN has a full portal tracker. Try scraping `https://www.espn.com/mens-college-basketball/story/_/id/transfer-tracker`
3. **CBB Analytics GitHub datasets** — search GitHub for `ncaa basketball transfer portal dataset`
4. **247Sports portal** — `https://247sports.com/college/basketball/Season-2024/TransferPortal/`
5. **Fix On3 school name matching** — reduce the 600 school-name-mismatch skips
6. **Extend Kaggle to older seasons** — but fix name collision issue (use player_id based on school+name not just name)
