#!/usr/bin/env bash
set -euo pipefail

cd /Users/damepivot/Projects/TransferPortal

# Reload ETL to get fresh counts
echo "Reloading ETL..."
python3 etl/load_real_data.py > /tmp/etl_output.txt 2>&1

# Parse counts from ETL output
TRANSFERS=$(psql -U damepivot -d ncaa_transfers -t -c "SELECT COUNT(*) FROM transfers;" | tr -d ' ')
SCORED=$(psql -U damepivot -d ncaa_transfers -t -c "SELECT COUNT(*) FROM individual_transfer_scores;" | tr -d ' ')
PLAYERS=$(psql -U damepivot -d ncaa_transfers -t -c "SELECT COUNT(DISTINCT player_id) FROM transfers;" | tr -d ' ')

echo "METRIC transfers_loaded=${TRANSFERS}"
echo "METRIC scored_transfers=${SCORED}"
echo "METRIC unique_players=${PLAYERS}"
