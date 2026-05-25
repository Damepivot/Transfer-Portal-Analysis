#!/bin/bash
# Run once to create the database and load the full schema
# Requires PostgreSQL installed: brew install postgresql@16 && brew services start postgresql@16

DB=ncaa_transfers

createdb $DB
psql -d $DB -f sql/01_schema.sql
psql -d $DB -f sql/02_seed_conferences.sql
psql -d $DB -f sql/03_views.sql

echo "Database '$DB' ready."
