"""Postgres port of tracking.verify_invariants. Same contract, same clauses.

CONTRACT (shared with the SQLite sibling — both hold or neither counts):
  - returns list[str] of human-readable violations; empty list == honored
  - NEVER raises on violation; callers decide fatality
  - violation messages mirror the sibling's format so outputs diff cleanly

Clauses:
  1. every non-'new' job has at least one event (no unevidenced state)
  2. every evented job's status equals its latest event's to_status
  3. every evented job's status_updated_at equals its latest event's at
  4. no event references a missing job (structurally impossible under the
     FK in db/schema.sql; kept for contract parity and as insurance
     against a deferred/dropped constraint)

Latest event: ORDER BY at DESC, id DESC — the id tie-break is load-bearing
(events 3 and 4 share an identical timestamp).

KNOWN BLIND SPOT, ported deliberately: clause 3 uses != and therefore
skips evented jobs whose status_updated_at is NULL (NULL != x is not
true). The SQLite sibling behaves identically; fixing one implementation
alone would make the siblings disagree, which is worse.

Standalone: python -m src.core.invariants_pg   (exit 1 on violations)
DSN: DATABASE_URL, with PG_DSN as an explicit override for pointing the
gate at a scratch store. Neither set raises at import -- a deploy gate
that can silently verify the wrong database is worse than no gate.
"""

import os
import sys

import psycopg

PG_DSN = os.environ.get("PG_DSN") or os.environ["DATABASE_URL"]

_LATEST = """(SELECT e.{col} FROM job_events e WHERE e.job_id = j.id
              ORDER BY e.at DESC, e.id DESC LIMIT 1)"""


def verify_invariants(conn: psycopg.Connection) -> list[str]:
    problems: list[str] = []
    with conn.cursor() as cur:
        cur.execute("""
            SELECT j.id FROM jobs j
            WHERE j.status != 'new'
              AND NOT EXISTS (SELECT 1 FROM job_events e WHERE e.job_id = j.id)
            ORDER BY j.id
        """)
        rows = cur.fetchall()
        if rows:
            problems.append(f"non-'new' jobs with no event: {[r[0] for r in rows]}")

        cur.execute(f"""
            SELECT j.id, j.status, {_LATEST.format(col='to_status')} AS latest
            FROM jobs j
            WHERE EXISTS (SELECT 1 FROM job_events e WHERE e.job_id = j.id)
              AND j.status != {_LATEST.format(col='to_status')}
            ORDER BY j.id
        """)
        rows = cur.fetchall()
        if rows:
            problems.append(f"status != latest event.to_status: {[tuple(r) for r in rows]}")

        cur.execute(f"""
            SELECT j.id FROM jobs j
            WHERE EXISTS (SELECT 1 FROM job_events e WHERE e.job_id = j.id)
              AND j.status_updated_at != {_LATEST.format(col='at')}
            ORDER BY j.id
        """)
        rows = cur.fetchall()
        if rows:
            problems.append(f"status_updated_at != latest event.at: {[r[0] for r in rows]}")

        cur.execute("""
            SELECT e.id FROM job_events e
            WHERE NOT EXISTS (SELECT 1 FROM jobs j WHERE j.id = e.job_id)
            ORDER BY e.id
        """)
        rows = cur.fetchall()
        if rows:
            problems.append(f"events referencing missing jobs: {[r[0] for r in rows]}")
    return problems


def main() -> int:
    with psycopg.connect(PG_DSN) as conn:
        problems = verify_invariants(conn)
    if problems:
        for p in problems:
            print(f"INVARIANT VIOLATION: {p}", file=sys.stderr)
        return 1
    print("invariants OK: 4 clauses, 0 violations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
