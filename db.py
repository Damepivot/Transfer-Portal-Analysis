"""
Shared database connection config.
All ETL scripts and the dashboard import DB_CONFIG from here.

Priority order:
  1. Streamlit secrets (st.secrets) — used when deployed on Streamlit Community Cloud
  2. .env file — used locally and for ETL scripts
Credentials are never hardcoded.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

def _get_config() -> dict:
    # Try Streamlit secrets first — available when running on Streamlit Cloud
    try:
        import streamlit as st
        s = st.secrets
        return {
            "dbname":   s["DB_NAME"],
            "user":     s["DB_USER"],
            "host":     s["DB_HOST"],
            "port":     int(s.get("DB_PORT", 5432)),
            "password": s["DB_PASSWORD"],
            "sslmode":  "require",   # Supabase requires SSL
        }
    except Exception:
        pass

    # Fall back to .env (local dev and ETL scripts)
    return {
        "dbname":   os.getenv("DB_NAME", "ncaa_transfers"),
        "user":     os.getenv("DB_USER", ""),
        "host":     os.getenv("DB_HOST", "localhost"),
        "port":     int(os.getenv("DB_PORT", 5432)),
        "password": os.getenv("DB_PASSWORD", ""),
    }

DB_CONFIG = _get_config()
