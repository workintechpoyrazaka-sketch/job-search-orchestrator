"""SQLite storage layer: the orchestrator's system of record."""

import os

import psycopg
from psycopg.rows import dict_row


COLUMNS = [
    "source", "external_id", "url", "title", "company",
    "category", "job_type", "location", "salary", "description",
    "publication_date", "fetched_at", "content_hash",
]


def get_connection(dsn: str | None = None) -> psycopg.Connection:
    """Open a Postgres connection with dict-style row access (row["col"]).

    dsn defaults to DATABASE_URL (fail-closed, matching the gate and auth.py);
    probes pass an explicit scratch DSN to override. No mkdir/PRAGMA: Postgres
    creates nothing on connect and always enforces foreign keys.
    """
    return psycopg.connect(dsn or os.environ["DATABASE_URL"], row_factory=dict_row)


def ensure_schema(dsn: str | None = None) -> None:
    """Verify the schema exists. Does NOT create it.

    Schema creation belongs to the migrator, which connects as the owning
    role. orchestrator_app deliberately holds no CREATE on schema public
    (least privilege, mirroring the column-scoped grants), so this fails
    closed with a named error rather than a permission traceback.
    """
    conn = get_connection(dsn)
    try:
        row = conn.execute(
            "SELECT to_regclass('public.jobs') AS jobs, "
            "       to_regclass('public.job_events') AS job_events"
        ).fetchone()
        missing = [name for name, oid in row.items() if oid is None]
        if missing:
            raise RuntimeError(
                f"schema not initialized (missing: {', '.join(missing)}) -- "
                f"apply db/schema.sql via the migrator (runs as the owner role)"
            )
    finally:
        conn.close()


def insert_new_jobs(conn: psycopg.Connection, jobs: list[dict]) -> dict:
    """Insert jobs, skipping duplicates on (source, external_id).

    NOT an upsert. On conflict the existing row is left UNTOUCHED, so
    changed adapter values never reach rows already in the DB. Consequence:
    the `Worldwide` tokens the Himalayas adapter fabricated from empty
    locationRestrictions (fixed 2026-07; silence-for-silence) are frozen
    in rows collected before the fix, and re-collection cannot rewrite
    them -- that requires an explicit migration. A count is deliberately
    not stated here: an earlier version said "278", which a later purge
    made stale, and surviving fabricated tokens are now indistinguishable
    from legitimate worldwide-filtered results. Docstrings should not
    carry numbers the DB can contradict.
    Does NOT commit: the caller owns the connection lifecycle and the
    transaction boundary, so this stays composable inside a larger
    transaction (psycopg forbids commit() inside conn.transaction()).
    Returns a {"new": int, "duplicate": int} summary.
    """
    placeholders = ", ".join(["%s"] * len(COLUMNS))
    col_names = ", ".join(COLUMNS)
    # ON CONFLICT DO NOTHING relies on the UNIQUE(source, external_id)
    # constraint to drop re-seen postings without an extra SELECT. Still not
    # an upsert: the existing row is left untouched (see the docstring).
    sql = (
        f"INSERT INTO jobs ({col_names}) VALUES ({placeholders}) "
        f"ON CONFLICT (source, external_id) DO NOTHING"
    )

    new_count = dup_count = 0
    for job in jobs:
        cur = conn.execute(sql, [job.get(c) for c in COLUMNS])
        if cur.rowcount == 1:
            new_count += 1
        else:
            dup_count += 1
    return {"new": new_count, "duplicate": dup_count}
