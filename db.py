"""
Shared database connection config.
All ETL scripts and the dashboard import DB_CONFIG from here.
Credentials come from .env (gitignored) — never hardcoded.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DB_CONFIG = {
    "dbname":   os.getenv("DB_NAME", "ncaa_transfers"),
    "user":     os.getenv("DB_USER", ""),
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5432)),
    "password": os.getenv("DB_PASSWORD", ""),
}
