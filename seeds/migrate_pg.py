"""One-off migration: data/jobs.db (SQLite) -> Postgres.

House style: seeds/repair_20260718.py — precondition-gated, dry-run-first,
single transaction, post-conditions verified before commit.

TIMESTAMP DOCTRINE (settles STATE_DOC §5 open item):
  Two formats exist in the source, verified by direct read on 2026-07-18:
    - UTC-aware '+00:00' strings (all fetched_at; tracking.py-era events)
      -> parsed and passed through.
    - Naive seconds-precision strings (8 drafting.py-era events + their
      status_updated_at mirrors) -> ASSUMED Europe/Istanbul, converted to UTC.
  The Istanbul assumption is consistency-checked, not proven: under both
  UTC- and Istanbul-interpretation, every event lands after its job's
  UTC-aware fetched_at, so the data cannot distinguish them. Istanbul is
  chosen because the sole operator's machine ran datetime.now() in that
  zone. Check query preserved in repo history (session 2026-07-18).

IDs migrate as-is: events, the reason audit, and project docs reference
jobs by number (#412, #616, #962). Sequences are setval'd to max(id).

Usage:
  python -m seeds.migrate_pg              # dry run (default): prints, writes nothing
  python -m seeds.migrate_pg --execute    # performs migration, one transaction
  PG_DSN env var overrides the default DSN.
"""

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

SQLITE_PATH = Path("data/jobs.db")
PG_DSN = os.environ.get("PG_DSN", "postgresql:///orchestrator")
ISTANBUL = ZoneInfo("Europe/Istanbul")

EXPECTED_JOBS = 283
EXPECTED_EVENTS = 10

JOB_COLS = [
    "id", "source", "external_id", "url", "title", "company", "category",
    "job_type", "location", "salary", "description", "publication_date",
    "fetched_at", "content_hash", "prefilter_pass", "ladder_match",
    "relevance_score", "score_reason", "status", "status_updated_at",
    "notes", "cover_letter",
]
EVENT_COLS = ["id", "job_id", "from_status", "to_status", "at", "note"]
TS_JOB_COLS = {"fetched_at", "status_updated_at"}
TS_EVENT_COLS = {"at"}


def normalize_ts(raw: str | None, *, log: list[str], ctx: str) -> datetime | None:
    """Aware strings pass through as UTC; naive strings get Istanbul attached
    then convert to UTC. Every naive conversion is logged for the dry run."""
    if raw is None:
        return None
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc)
    converted = dt.replace(tzinfo=ISTANBUL).astimezone(timezone.utc)
    log.append(f"  {ctx}: {raw} (naive, assumed Istanbul) -> {converted.isoformat()}")
    return converted


def preconditions(src: sqlite3.Connection, pg: psycopg.Connection, *, wipe: bool = False) -> None:
    n_jobs = src.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    n_events = src.execute("SELECT COUNT(*) FROM job_events").fetchone()[0]
    if (n_jobs, n_events) != (EXPECTED_JOBS, EXPECTED_EVENTS):
        sys.exit(f"ABORT: source has {n_jobs} jobs / {n_events} events; "
                 f"expected {EXPECTED_JOBS}/{EXPECTED_EVENTS}. "
                 "Update EXPECTED_* deliberately if the source legitimately grew.")
    from src.core import tracking
    violations = tracking.verify_invariants(src)
    if violations:
        for v in violations:
            print(f"  INVARIANT VIOLATION: {v}", file=sys.stderr)
        sys.exit("ABORT: source DB fails its own invariants; migrate nothing.")
    if wipe:
        with pg.cursor() as cur:
            for table in ("jobs", "job_events"):
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                print(f"  --wipe: will TRUNCATE {table} ({cur.fetchone()[0]} rows) in-transaction")
    else:
        with pg.cursor() as cur:
            for table in ("jobs", "job_events"):
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                n = cur.fetchone()[0]
                if n:
                    sys.exit(f"ABORT: target {table} already has {n} rows; "
                             "this script populates empty tables only.")


def postconditions(pg: psycopg.Connection) -> None:
    checks = [
        ("job count", f"SELECT COUNT(*) = {EXPECTED_JOBS} FROM jobs"),
        ("event count", f"SELECT COUNT(*) = {EXPECTED_EVENTS} FROM job_events"),
        ("non-new jobs evidenced",
         "SELECT NOT EXISTS (SELECT 1 FROM jobs j WHERE j.status <> 'new' "
         "AND NOT EXISTS (SELECT 1 FROM job_events e WHERE e.job_id = j.id))"),
        ("status matches latest event",
         "SELECT NOT EXISTS (SELECT 1 FROM jobs j JOIN LATERAL "
         "(SELECT to_status, at FROM job_events e WHERE e.job_id = j.id "
         "ORDER BY at DESC, id DESC LIMIT 1) le ON TRUE "
         "WHERE j.status <> le.to_status OR j.status_updated_at <> le.at)"),
    ]
    for name, sql in checks:
        with pg.cursor() as cur:
            cur.execute(sql)
            if not cur.fetchone()[0]:
                raise RuntimeError(f"POST-CONDITION FAILED: {name} (transaction rolls back)")
        print(f"  post-check OK: {name}")


def main() -> None:
    execute = "--execute" in sys.argv
    wipe = "--wipe" in sys.argv
    src = sqlite3.connect(SQLITE_PATH)
    src.row_factory = sqlite3.Row
    ts_log: list[str] = []

    jobs, events = [], []
    for row in src.execute(f"SELECT {', '.join(JOB_COLS)} FROM jobs ORDER BY id"):
        r = dict(row)
        for c in TS_JOB_COLS:
            r[c] = normalize_ts(r[c], log=ts_log, ctx=f"jobs.{c} #{r['id']}")
        jobs.append(r)
    for row in src.execute(f"SELECT {', '.join(EVENT_COLS)} FROM job_events ORDER BY id"):
        r = dict(row)
        for c in TS_EVENT_COLS:
            r[c] = normalize_ts(r[c], log=ts_log, ctx=f"events.{c} #{r['id']}")
        events.append(r)

    print(f"read: {len(jobs)} jobs, {len(events)} events from {SQLITE_PATH}")
    print(f"naive timestamps converted (Istanbul->UTC): {len(ts_log)}")
    for line in ts_log:
        print(line)

    with psycopg.connect(PG_DSN) as pg:
        preconditions(src, pg)
        print("preconditions OK")
        if not execute:
            print("DRY RUN — nothing written. Re-run with --execute.")
            return
        with pg.transaction():
            with pg.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO jobs ({', '.join(JOB_COLS)}) "
                    f"VALUES ({', '.join('%(' + c + ')s' for c in JOB_COLS)})", jobs)
                cur.executemany(
                    f"INSERT INTO job_events ({', '.join(EVENT_COLS)}) "
                    f"VALUES ({', '.join('%(' + c + ')s' for c in EVENT_COLS)})", events)
                for table in ("jobs", "job_events"):
                    cur.execute(
                        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                        f"(SELECT MAX(id) FROM {table}))")
            postconditions(pg)
        print("MIGRATED. Transaction committed.")


if __name__ == "__main__":
    main()
