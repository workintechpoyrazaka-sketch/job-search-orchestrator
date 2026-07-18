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

WHAT STAYS OUT, AND WHY IT CANNOT BE SOLVED IN THE UI:
  - Cover letters. Withheld 2026-07-17. They are generated from profile.md
    and are that file restated in prose; publishing them publishes it.
  - profile.md itself is NOT copied. It is not in this schema and never
    enters the repo.
  - There is no "private side" of this dashboard. The deploy is static,
    read-only, unauthenticated, and committed to a public git repo.
    Anything in this DB is public whether or not a widget renders it, and
    is permanent in git history once pushed. Hiding a column in the page
    hides nothing. The only lever is: in this DB, or not in it.

SCHEMA SOURCE (changed 2026-07-17):
Schema is read from the SOURCE DB's own sqlite_master, not from
storage.SCHEMA. storage.SCHEMA has no cover_letter column while the live
DB does; CREATE TABLE IF NOT EXISTS hid the drift. The mirror is built
from what production reports, not from a transcript of what production
was once declared to be.

AMENDED 2026-07-18 (cold-review findings):
  1. FAIL-CLOSED COLUMNS. The 07-17 design auto-copied any new column and
     withheld by denylist — leak-by-default wearing a feature's clothes
     (demonstrated live: score_reason reached the public DB while the
     working policy said withheld). Now every column of `jobs` must be
     classified PUBLIC or WITHHELD below; an unclassified column kills
     the build by name. Adding a column to production now forces a
     publication decision instead of defaulting to one.
  2. ATOMIC PUBLISH. Gates used to run after demo.db was fully written;
     a failed gate left the poisoned file on disk, un-gitignored and
     stageable. Now the build writes to a temp path, gates run against
     the temp, and only a passing build is renamed onto demo.db.
  3. FULL INVARIANT GATE. The unbacked-applied check verified one clause
     of tracking.py's stated invariant for one status. The build now runs
     tracking.verify_invariants (all clauses) and keeps the stricter
     applied-specific check.

Run from repo root:  python -m seeds.build_demo
Output:              data/demo.db  (committed; see .gitignore negation)
Source:              data/jobs.db  (gitignored, never committed)
"""

import os
import sqlite3
from pathlib import Path

from src.core.storage import get_connection
from src.core.tracking import verify_invariants

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "data" / "jobs.db"
DEMO_PATH = ROOT / "data" / "demo.db"
BUILD_PATH = ROOT / "data" / "demo.db.building"   # gates pass -> renamed onto DEMO_PATH

# --- column classification: every jobs column, explicitly ---------------------
# PUBLIC is an allowlist; WITHHELD is documentation of the refusals; anything
# in NEITHER set fails the build by name. There is no default.
#
# 'id' is public deliberately: job_events.job_id points at job ids. If the
# demo re-numbered rows, an event would silently attach to whatever landed
# at its job_id — a false claim, rendered confidently.
PUBLIC = {
    "id", "source", "external_id", "url", "title", "company", "category",
    "job_type", "location", "salary", "description", "publication_date",
    "fetched_at", "content_hash", "prefilter_pass", "ladder_match",
    "relevance_score", "status", "status_updated_at",
    # score_reason: PUBLIC as of 2026-07-18, decided after reading all 34
    # and verifying checkable claims against stored posting text
    # (probes/reason_audit.py): 32/34 grounded; rows 412 and 616 carry
    # unsupported eligibility claims, published deliberately as evidence
    # of the failure mode the truncation probe named (post-hoc narrative,
    # not mechanism). Both errors favor the candidate, not the company.
    "score_reason",
}
WITHHELD = {
    # profile.md restated in prose; the one file that never enters the repo.
    "cover_letter",
    # hand-written private commentary (e.g. the Uken eligibility note);
    # the dashboard never rendered it, so withholding costs the site nothing.
    "notes",
}

# job_events: the transitions ARE the public evidence; the notes are running
# commentary, including on in-progress hiring processes (withheld 2026-07-18
# while an application is live -- content is professional, but disclosing the
# state of an open negotiation is a choice, and this makes it deliberate).
# Reclassify and rebuild to publish later.
EVENT_PUBLIC = {"id", "job_id", "from_status", "to_status", "at"}
EVENT_WITHHELD = {"note"}


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


def _classify(table: str, all_cols: list[str],
              public: set[str], withheld: set[str]) -> list[str]:
    """Return the public column list, or die naming every unclassified column."""
    unclassified = [c for c in all_cols if c not in public and c not in withheld]
    if unclassified:
        raise SystemExit(
            f"REFUSING: unclassified column(s) in {table}: {unclassified}. "
            "Every column must be classified in seeds/build_demo.py. "
            "No default. Classify, then rebuild."
        )
    ghosts = (public | withheld) - set(all_cols)
    if ghosts:
        raise SystemExit(
            f"REFUSING: column(s) classified for {table} not in the live "
            f"schema: {sorted(ghosts)}. The classification has drifted from "
            "production; fix the lists, then rebuild."
        )
    return [c for c in all_cols if c in public]


def _run_gates(build_path: Path) -> None:
    """Every gate, against the built artifact. Any raise aborts the publish."""
    with get_connection(build_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM job_events").fetchone()[0]
        by_status = dict(conn.execute(
            "SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())

        for table, withheld in (("jobs", WITHHELD), ("job_events", EVENT_WITHHELD)):
            cols = set(_columns(conn, table))
            for col in sorted(withheld & cols):
                n = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {col} IS NOT NULL"
                ).fetchone()[0]
                if n:
                    raise SystemExit(
                        f"REFUSING: {n} row(s) carry withheld column "
                        f"{table}.{col} in the built DB. Copy must not have "
                        "excluded it; abort."
                    )

        unbacked = conn.execute("""
            SELECT COUNT(*) FROM jobs j
            WHERE j.status = 'applied'
              AND NOT EXISTS (
                  SELECT 1 FROM job_events e
                  WHERE e.job_id = j.id AND e.to_status = 'applied'
              )
        """).fetchone()[0]
        if unbacked:
            raise SystemExit(
                f"REFUSING: {unbacked} 'applied' row(s) have no backing "
                "event. The public site would claim an application it "
                "cannot evidence."
            )

        problems = verify_invariants(conn)
        if problems:
            raise SystemExit(
                "REFUSING: tracking invariant violated in built DB:\n  "
                + "\n  ".join(problems)
            )

    print(f"  gates OK   : withheld columns empty; applied evidenced; "
          f"invariants hold")
    print(f"  total jobs : {total}")
    print(f"  job_events : {events}")
    print(f"  by status  : {by_status}")


def main() -> None:
    if not SOURCE_PATH.exists():
        raise SystemExit(f"source DB not found: {SOURCE_PATH}")

    if BUILD_PATH.exists():
        BUILD_PATH.unlink()

    try:
        with get_connection(SOURCE_PATH) as src, get_connection(BUILD_PATH) as dst:
            _clone_schema(src, dst)
            all_cols = _columns(src, "jobs")
            job_cols = _classify("jobs", all_cols, PUBLIC, WITHHELD)
            all_event_cols = _columns(src, "job_events")
            event_cols = _classify("job_events", all_event_cols,
                                   EVENT_PUBLIC, EVENT_WITHHELD)
            print(f"  public cols  : {', '.join(job_cols)}")
            print(f"  withheld     : {', '.join(sorted(set(all_cols) - set(job_cols)))}")
            print(f"  event cols   : {', '.join(event_cols)} "
                  f"(withheld: {', '.join(sorted(set(all_event_cols) - set(event_cols)))})")
            n_jobs = _copy_table(src, dst, "jobs", job_cols)
            n_events = _copy_table(src, dst, "job_events", event_cols)
            print(f"  copied       : {n_jobs} jobs, {n_events} events")

        _run_gates(BUILD_PATH)

        os.replace(BUILD_PATH, DEMO_PATH)     # atomic: only a passing build lands
        print(f"published {DEMO_PATH}")
    finally:
        if BUILD_PATH.exists():
            BUILD_PATH.unlink()               # failed build leaves no artifact
            print(f"removed failed build {BUILD_PATH.name}")


if __name__ == "__main__":
    main()
