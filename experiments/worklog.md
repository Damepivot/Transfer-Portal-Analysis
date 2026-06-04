# Autoresearch Worklog: Transfer Data Sources

## Session Info
- Started: 2026-06-03
- Goal: Expand D1 transfer count from 1,859 → 5,000+
- Branch: autoresearch/transfer-data-sources-20260603

## Baseline
- Transfers: 1,859
- Scored: 1,039
- Players: 1,654
- Sources: On3 (1,103) + Kaggle (728) + International (19)
- Key skip reasons: 672 same-school re-commits (by design), ~600 school name mismatches, non-D1 destinations

## Run Log

### Run 1 (Baseline) — transfers_loaded=1859 (KEEP)
- Timestamp: 2026-06-03
- What: Established baseline from existing data sources
- Result: 1,859 transfers, 1,039 scored
- Insight: On3 commits us to rated players only. Main growth vector is finding a source with ALL portal entrants.
- Next: Try Barttorvik transfer rankings page — covers all D1 portal entrants

## Key Insights
- On3 data gives ~735 committed/season but ~2,000+ actually transfer per year
- Skip rate is 45% due to: same-school re-commits (expected), unmatched school names (~30 fixable), non-D1 destinations
- Best ROI: data source with ALL portal entrants (Barttorvik, ESPN, 247)
- Name collision risk: don't extend Kaggle past 2022 without unique player ID strategy

## Next Ideas
1. Barttorvik `trankings.php` — likely has all portal entrants with origin/destination
2. Fix On3 school name map for ~30 schools that fail matching
3. ESPN transfer tracker scrape
4. 247Sports portal
5. GitHub open datasets

### Run 2: Fixed D1 school name mappings — transfers=1858, scored=1047 (KEEP)
- Timestamp: 2026-06-03
- What changed: Fixed 3 critical bugs: BYU mapped to "Brigham Young" (CBB file uses "BYU"), Detroit Mercy → "Detroit", Bethune-Cookman → "Bethune Cookman" (no hyphen)
- Result: 1,858 transfers, 1,047 scored (+8 scored vs baseline)
- Insight: School name bugs were routing D1 programs to sub_d1 tier incorrectly. BYU was the worst offender with ~10 transfers misclassified.
- Next: Research cbbstat API successor, Verbal Commits data, and remaining unmatched On3 schools
