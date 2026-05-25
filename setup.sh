#!/usr/bin/env bash
# One-shot setup for TransferPortal on a new machine.
# Usage: bash setup.sh

set -e

echo "=== TransferPortal Setup ==="

# 1. Python deps
echo "Installing Python dependencies..."
pip install -r requirements.txt

# 2. .env
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example — fill in your DB credentials before continuing."
    exit 0
fi

# 3. Database
echo "Creating PostgreSQL database (if it doesn't exist)..."
createdb ncaa_transfers 2>/dev/null || echo "  Database already exists, skipping."

# 4. Schema + seed conferences
echo "Applying schema and seeding conferences..."
psql -d ncaa_transfers -f sql/01_schema.sql
psql -d ncaa_transfers -f sql/02_seed_conferences.sql

# 5. Data placeholders
mkdir -p data/raw
touch data/raw/.gitkeep data/.gitkeep

echo ""
echo "=== Setup complete ==="
echo "Next steps:"
echo "  1. Add your data CSVs to data/raw/"
echo "  2. python etl/load_real_data.py"
echo "  3. psql -d ncaa_transfers -f sql/03_views.sql"
echo "  4. streamlit run app.py"
