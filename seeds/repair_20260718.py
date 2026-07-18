"""One-time repair of the job_events audit trail. Committed so the repair
is citable, reviewable, and re-runnable in dry-run forever.

WHAT THIS FIXES (found in cold review, 2026-07-18):

1. Events 3 and 4 (the 2026-07-17 hand backfill) carry at='2026-07-14',
   "reconstructed from recall". The DB itself contradicts the recall:
   jobs 414 and 614 both have status_updated_at='2026-07-08T12:37:58' --
   identical to the second, i.e. one bulk UPDATE flipped both to 'applied'
   on July 8. The DB timestamp is evidence; the recalled date was not.
   Fix: set each event's `at` to the DB-evidenced timestamp, restoring the
   at == status_updated_at invariant, and amend the note to record both
   the correction and the superseded claim.

2. Six status changes predate state-machine coverage and have NO event at
   all: jobs 392, 398, 406, 411, 412 (drafted) and 616 (archived). The
   2026-07-17 backfill repaired exactly the rows the build_demo gate
   checks and stopped there. This completes the history instead.
   Fix: insert one event per row, at = the row's status_updated_at,
   from_status derived from evidence (see _derive_from_status), note
   marking it backfilled.

WHY DIRECT INSERT/UPDATE AND NOT _apply_transition:
   The engine refuses no-op moves (drafted -> drafted is illegal) and
   stamps `at` with now(). Historical repair needs historical timestamps
   and rows already in their target state. This script is the documented
   exception; the engine remains the sole producer of NEW transitions.

RUN:
   python -m seeds.repair_20260718            # dry-run: prints plan, writes nothing
   python -m seeds.repair_20260718 --commit   # applies, in one transaction, then verifies
"""

import sys

from src.core.storage import get_connection

# --- expected preconditions: refuse to run against a DB in any other state ---

# (event_id, job_id, wrong_at, correct_at)
EVENT_DATE_FIXES = [
    (3, 414, "2026-07-14", "2026-07-08T12:37:58"),
    (4, 614, "2026-07-14", "2026-07-08T12:37:58"),
]

NOTE_AMENDMENT = (
    " [Corrected 2026-07-18 by seeds/repair_20260718.py: `at` was "
    "'2026-07-14' from recall; DB evidence (status_updated_at, one bulk "
    "UPDATE stamping both applied rows '2026-07-08T12:37:58') supersedes "
    "recall. Recalled date retained here as superseded testimony.]"
)

# job_ids that must currently be event-less and get a backfilled event
MISSING_EVENT_JOBS = [392, 398, 406, 411, 412, 616]

BACKFILL_NOTE = (
    "Backfilled 2026-07-18 by seeds/repair_20260718.py. Predates state "
    "machine coverage; `at` taken from status_updated_at (DB evidence), "
    "from_status derived (see script). Not written by _apply_transition."
)


def _derive_from_status(row) -> str:
    """Evidence-based from_status for a backfilled event.

    drafted: the only legal edge in is new -> drafted, and run_drafting
    selects status='new'. Code-evidenced.
    archived: reachable from new/drafted/applied. A non-null cover_letter
    proves the row passed through 'drafted'; absence means it was archived
    straight from 'new' (nothing else writes cover_letter).
    """
    if row["status"] == "drafted":
        return "new"
    if row["status"] == "archived":
        return "drafted" if row["cover_letter"] else "new"
    raise SystemExit(f"unexpected status {row['status']!r} for job {row['id']}")


def _check_preconditions(conn) -> list[dict]:
    """Verify the DB is in exactly the state this repair was written for.
    Returns the planned backfill rows. Any mismatch -> SystemExit, no write."""
    for ev_id, job_id, wrong_at, correct_at in EVENT_DATE_FIXES:
        ev = conn.execute(
            "SELECT job_id, at FROM job_events WHERE id = ?", (ev_id,)
        ).fetchone()
        if ev is None or ev["job_id"] != job_id or ev["at"] != wrong_at:
            raise SystemExit(
                f"precondition failed: event {ev_id} is not "
                f"(job {job_id}, at={wrong_at!r}); found {dict(ev) if ev else None}. "
                "DB differs from the state this repair targets. Nothing written."
            )
        job = conn.execute(
            "SELECT status_updated_at FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if job["status_updated_at"] != correct_at:
            raise SystemExit(
                f"precondition failed: job {job_id}.status_updated_at is "
                f"{job['status_updated_at']!r}, expected {correct_at!r}."
            )

    plans = []
    for job_id in MISSING_EVENT_JOBS:
        n = conn.execute(
            "SELECT COUNT(*) FROM job_events WHERE job_id = ?", (job_id,)
        ).fetchone()[0]
        if n:
            raise SystemExit(
                f"precondition failed: job {job_id} already has {n} event(s); "
                "this repair expects it event-less. Nothing written."
            )
        row = conn.execute(
            "SELECT id, status, status_updated_at, cover_letter "
            "FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None or row["status"] not in ("drafted", "archived"):
            raise SystemExit(f"precondition failed: job {job_id} missing/unexpected.")
        plans.append({
            "job_id": row["id"],
            "from_status": _derive_from_status(row),
            "to_status": row["status"],
            "at": row["status_updated_at"],
        })
    return plans


def _verify_invariants(conn) -> None:
    """The tracking.py docstring's invariant, checked for real. Fail loud."""
    bad = conn.execute("""
        SELECT j.id FROM jobs j
        WHERE j.status != 'new'
          AND NOT EXISTS (SELECT 1 FROM job_events e WHERE e.job_id = j.id)
    """).fetchall()
    if bad:
        raise SystemExit(f"INVARIANT BROKEN: non-new jobs with no event: {[r[0] for r in bad]}")

    bad = conn.execute("""
        SELECT j.id, j.status FROM jobs j
        WHERE EXISTS (SELECT 1 FROM job_events e WHERE e.job_id = j.id)
          AND j.status != (
              SELECT e.to_status FROM job_events e WHERE e.job_id = j.id
              ORDER BY e.at DESC, e.id DESC LIMIT 1)
    """).fetchall()
    if bad:
        raise SystemExit(f"INVARIANT BROKEN: status != latest event: {[tuple(r) for r in bad]}")

    bad = conn.execute("""
        SELECT j.id FROM jobs j
        WHERE EXISTS (SELECT 1 FROM job_events e WHERE e.job_id = j.id)
          AND j.status_updated_at != (
              SELECT e.at FROM job_events e WHERE e.job_id = j.id
              ORDER BY e.at DESC, e.id DESC LIMIT 1)
    """).fetchall()
    if bad:
        raise SystemExit(f"INVARIANT BROKEN: status_updated_at != latest event.at: {[r[0] for r in bad]}")

    print("invariants OK: every non-new job evidenced; status == latest event; "
          "timestamps aligned.")


def main() -> None:
    commit = "--commit" in sys.argv
    conn = get_connection()
    try:
        plans = _check_preconditions(conn)

        print(f"mode: {'COMMIT' if commit else 'DRY-RUN (nothing will be written)'}\n")
        print("-- date corrections --")
        for ev_id, job_id, wrong_at, correct_at in EVENT_DATE_FIXES:
            print(f"  event {ev_id} (job {job_id}): at {wrong_at!r} -> {correct_at!r}, note amended")
        print("-- backfilled events --")
        for p in plans:
            print(f"  job {p['job_id']}: {p['from_status']} -> {p['to_status']} at {p['at']}")

        if not commit:
            print("\ndry-run complete. Re-run with --commit to apply.")
            return

        with conn:  # one transaction: all or nothing
            for ev_id, _job_id, _wrong, correct_at in EVENT_DATE_FIXES:
                conn.execute(
                    "UPDATE job_events SET at = ?, note = note || ? WHERE id = ?",
                    (correct_at, NOTE_AMENDMENT, ev_id),
                )
            for p in plans:
                conn.execute(
                    "INSERT INTO job_events (job_id, from_status, to_status, at, note) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (p["job_id"], p["from_status"], p["to_status"], p["at"], BACKFILL_NOTE),
                )

        print("\nwritten. verifying from the live answer:")
        _verify_invariants(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
