"""Apply the Postgres schema and the generated status-domain CHECK.

Runs as the OWNING role (the migrator's peer-auth connection), never as
orchestrator_app, which deliberately has no CREATE on schema public.
Idempotent: schema.sql uses CREATE ... IF NOT EXISTS, and the CHECK is
guarded by a pg_constraint lookup (ADD CONSTRAINT has no IF NOT EXISTS).

Usage: python -m db.apply_schema   [DSN from SCHEMA_DSN, else DATABASE_URL]
"""

import os
from pathlib import Path

import psycopg

from src.core.tracking import status_domain_sql

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
STATUS_CONSTRAINT = "jobs_status_domain"


def apply_schema(dsn: str) -> dict:
    """Create tables/indexes if absent, then add the status CHECK if absent."""
    added = False
    with psycopg.connect(dsn) as conn:
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        exists = conn.execute(
            "SELECT 1 FROM pg_constraint "
            "WHERE conname = %s AND conrelid = 'jobs'::regclass",
            (STATUS_CONSTRAINT,),
        ).fetchone()
        if exists is None:
            conn.execute(
                f"ALTER TABLE jobs ADD CONSTRAINT {STATUS_CONSTRAINT} "
                f"{status_domain_sql()}"
            )
            added = True
        conn.commit()
    return {"schema": "applied", "check_added": added}


if __name__ == "__main__":
    target = os.environ.get("SCHEMA_DSN") or os.environ["DATABASE_URL"]
    print(apply_schema(target))
