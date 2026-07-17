"""
Build the public dashboard database from the REAL job search database.

WHAT CHANGED (2026-07-17) AND WHY:
This script used to synthesize fictional companies and leave job_events
empty, on the stated rule: "the deployed dashboard must never see
data/jobs.db." That rule is REPEALED, deliberately, by Poi on 2026-07-17.

Reason for repeal: the fictional site could not be shown to anyone. A demo
of invented companies demonstrates the pipeline's shape and nothing about
the pipeline's use. The real board — 283 rows, 3 applications, 3 audit
events — is the portfolio piece. The fake one was furniture.

WHAT IS PUBLIC NOW, EXPLICITLY:
  - Real company names, titles, URLs, locations, salaries.
  - Real relevance scores and Haiku-written score_reason text about
    real, named employers.
  - Real application history: which companies, what outcome, when.

WHAT STAYS OUT, AND WHY IT CANNOT BE SOLVED IN THE UI:
  - Cover letters. Withheld 2026-07-17. They are generated from profile.md
    and are that file restated in prose; publishing them publishes it.
    Excluded at copy time because that is the only place exclusion is
    possible — see below.
  - profile.md itself is NOT copied. It is not in this schema and never
    enters the repo.
  - There is no "private side" of this dashboard. The deploy is static,
    read-only, unauthenticated, and committed to a public git repo.
    Anything in this DB is public whether or not a widget renders it, and
    is permanent in git history once pushed. Hiding a column in the page
    hides nothing. The only lever is: in this DB, or not in it.

SCHEMA SOURCE (changed 2026-07-17):
This script used to call init_db(), which builds from storage.SCHEMA. That
was believed to make the demo "byte-identical in schema to production by
construction." It does not, and did not: storage.SCHEMA has no cover_letter
column, while the live jobs.db does. The column was added to the live DB
without updating SCHEMA, and init_db's CREATE TABLE IF NOT EXISTS meant the
drift never fired against an existing DB — only against a fresh one.

So the schema is now read from the SOURCE DB's own sqlite_master. The mirror
is built from what production reports, not from a transcript of what
production was once declared to be. If they drift again, this still works.

Run from repo root:  python -m seeds.build_demo
Output:              data/demo.db  (committed; see .gitignore negation)
Source:             data/jobs.db  (gitignored, never committed)
"""

import sqlite3
from pathlib import Path

from src.core.storage import get_connection

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "data" / "jobs.db"
DEMO_PATH = ROOT / "data" / "demo.db"

# Columns are discovered from the source DB at runtime (see _columns), not
# listed here. A hardcoded list is a second transcript that can drift from
# production exactly the way storage.SCHEMA did.
#
# 'id' is copied deliberately: job_events.job_id points at job ids. If the
# demo re-numbered rows, event 2 would silently attach to whatever landed at
# 962 — a false claim, rendered confidently.


def _clone_schema(src: sqlite3.Connection, dst: sqlite3.Connection) -> None:
    """Recreate the source DB's schema exactly, as the source reports it."""
    stmts = src.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL "
        "AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    for (stmt,) in stmts:
        dst.execute(stmt)


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Column names from the DB, never from recall."""
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _copy_table(src: sqlite3.Connection, dst: sqlite3.Connection,
                table: str, cols: list[str]) -> int:
    rows = src.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
    if not rows:
        return 0
    placeholders = ", ".join(["?"] * len(cols))
    dst.executemany(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
        rows,
    )
    return len(rows)


def main() -> None:
    if not SOURCE_PATH.exists():
        raise SystemExit(f"source DB not found: {SOURCE_PATH}")

    if DEMO_PATH.exists():
        DEMO_PATH.unlink()                          # idempotent: fresh each run
        print(f"removed existing {DEMO_PATH.name}")

    with get_connection(SOURCE_PATH) as src, get_connection(DEMO_PATH) as dst:
        _clone_schema(src, dst)
        # Columns come from the source DB itself. If a column is added to
        # production tomorrow, this copies it without being edited.
        #
        # EXCEPT cover_letter (excluded 2026-07-17, Poi's call): the letters
        # are generated from profile.md, the one file that never enters this
        # repo. A committed DB is public whether or not a widget renders the
        # column, and permanent in git history once pushed. Excluding it here
        # is the only place the exclusion can happen. The site still shows
        # that drafting occurred — status, score, and event — without the text.
        WITHHELD = {"cover_letter"}
        job_cols = [c for c in _columns(src, "jobs") if c not in WITHHELD]
        event_cols = _columns(src, "job_events")
        print(f"  jobs columns   : {len(job_cols)} -> {', '.join(job_cols)}")
        print(f"  withheld       : {', '.join(sorted(WITHHELD))}")
        n_jobs = _copy_table(src, dst, "jobs", job_cols)
        n_events = _copy_table(src, dst, "job_events", event_cols)

    # Verify from the live answer, not from the plan we just wrote.
    with get_connection(DEMO_PATH) as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM job_events").fetchone()[0]
        by_status = conn.execute(
            "SELECT status, COUNT(*) FROM jobs GROUP BY status"
        ).fetchall()
        letters = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE cover_letter IS NOT NULL"
        ).fetchone()[0]        # Every 'applied' row must have a backing event. This is the claim
        # the public site makes; it should not be able to make it falsely.
        unbacked = conn.execute("""
            SELECT COUNT(*) FROM jobs j
            WHERE j.status = 'applied'
              AND NOT EXISTS (
                  SELECT 1 FROM job_events e
                  WHERE e.job_id = j.id AND e.to_status = 'applied'
              )
        """).fetchone()[0]

    print(f"built {DEMO_PATH}")
    print(f"  copied jobs   : {n_jobs}")
    print(f"  copied events : {n_events}")
    print(f"  total jobs    : {total}")
    print(f"  job_events    : {events}")
    print(f"  cover letters : {letters}  (must be 0 — withheld)")
    print(f"  by status     : {dict(by_status)}")
    print(f"  unbacked applied : {unbacked}  (must be 0)")

    if letters:
        raise SystemExit(
            f"REFUSING: {letters} cover letter(s) reached the public DB. "
            "These are profile.md restated in prose and must not be committed."
        )

    if unbacked:
        raise SystemExit(
            f"REFUSING: {unbacked} 'applied' row(s) have no backing event. "
            "The public site would claim an application it cannot evidence."
        )


if __name__ == "__main__":
    main()
