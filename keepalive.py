"""
Supabase keepalive — runs a trivial read against the prod database.

Free-tier Supabase pauses a project after 7 days with no database activity.
Loading the Streamlit app is not enough on its own; the pause check keys off
real Postgres connections, so this issues an actual query.

Reads DB_* from the environment (GitHub Actions secrets in CI). Deliberately
does NOT import db.py: that module falls back to DB_HOST=localhost when the
env is unset, which would let this script connect to a local Postgres and
report success while prod stayed paused. Host and password are required here
and the script fails loudly if either is missing.
"""
import os
import sys
from datetime import datetime, timezone

import psycopg2

REQUIRED = ("DB_HOST", "DB_PASSWORD")


def build_config() -> dict:
    missing = [k for k in REQUIRED if not os.getenv(k)]
    if missing:
        sys.exit(f"keepalive: missing required env var(s): {', '.join(missing)}")

    host = os.environ["DB_HOST"]
    if host in ("localhost", "127.0.0.1"):
        sys.exit(f"keepalive: DB_HOST is {host} — refusing to run against a local database")

    return {
        "dbname":   os.getenv("DB_NAME", "postgres"),
        "user":     os.getenv("DB_USER", "postgres"),
        "host":     host,
        "port":     int(os.getenv("DB_PORT", 5432)),
        "password": os.environ["DB_PASSWORD"],
        "sslmode":  "require",
        "connect_timeout": 30,
    }


def main() -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with psycopg2.connect(**build_config()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM transfers")
                (count,) = cur.fetchone()
    except psycopg2.OperationalError as e:
        # A paused project surfaces here — surface it as a failed run so the
        # scheduled job goes red and GitHub emails about it.
        sys.exit(f"keepalive {stamp}: FAILED to reach database — {e}")

    print(f"keepalive {stamp}: ok — transfers table has {count} rows")


if __name__ == "__main__":
    main()
