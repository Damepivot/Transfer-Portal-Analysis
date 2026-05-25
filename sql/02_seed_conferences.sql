-- Load conference reference data from CSV
-- Run from psql: \i sql/02_seed_conferences.sql
-- Or via: psql -d ncaa_transfers -f sql/02_seed_conferences.sql

COPY conferences (name, abbreviation, tier)
FROM '/Users/damepivot/ncaa-transfer-analysis/data/conferences_seed.csv'
DELIMITER ','
CSV HEADER;
