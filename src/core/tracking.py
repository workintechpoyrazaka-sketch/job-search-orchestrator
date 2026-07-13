"""State-machine tracking: the only sanctioned way a job changes status.

Design (Module 4):
  - jobs.status stays the working state; job_events is the append-only audit
    trail. Every status change writes BOTH, atomically, in one transaction.
    A job's current status must never drift from its latest event.
  - Policy lives here, not in the schema. The schema has no CHECK constraint;
    TRANSITIONS and REQUIRES_NOTE below are the single authority on legality
    and note-requirements.
  - One private core (_apply_transition) performs every write. Two public
    doors funnel into it: transition() for ordinary moves, apply_to_job() for
    the one move (-> applied) that carries an external precondition.
"""

import sqlite3
from datetime import datetime, timezone


# --- policy: state shape lives here, not in the schema -----------------------

# Legal forward-only moves. Every state can also reach 'archived' (the single
# escape). 'archived' and 'rejected' are terminal (empty destination sets).
TRANSITIONS: dict[str, set[str]] = {
    "new":      {"drafted", "archived"},
    "drafted":  {"applied", "archived"},
    "applied":  {"rejected", "archived"},
    "archived": set(),
    "rejected": set(),
}

# Destinations that must carry a non-empty note. 'archived' records WHY a lead
# was killed; 'applied' records the ATS metadata (Recruitee/BambooHR/etc.).
# Both are the same kind of rule -- a policy about the destination -- so both
# live here. What is unique to 'applied' (authorization) lives in its door.
REQUIRES_NOTE: set[str] = {"archived", "applied"}


class TransitionError(Exception):
    """Raised when a requested status change violates policy.

    Nothing is written when this is raised -- guards run before the
    transaction opens, so a rejected move leaves the DB untouched.
    """


def _now() -> str:
    """One ISO-8601 UTC timestamp string. Matches status_updated_at's format."""
    return datetime.now(timezone.utc).isoformat()


def _apply_transition(
    conn: sqlite3.Connection,
    job_id: int,
    to_status: str,
    note: str | None = None,
) -> None:
    """Atomically move one job to a new status and record the event.

    The ONLY function that writes a status change. Contract:
      1. SELECT current status; missing row -> raise (free orphan guard).
      2. Validate current -> to_status against TRANSITIONS; illegal -> raise.
      3. If to_status in REQUIRES_NOTE, note must be non-empty -> else raise.
      4. In ONE transaction: UPDATE jobs + INSERT job_events. Both or neither.

    All guards run BEFORE the transaction opens, so any raise leaves the DB
    untouched. The timestamp is computed once and written to both rows so the
    job's status_updated_at and the event's `at` are equal by construction.
    """
    # 1. resolve current status (orphan guard)
    row = conn.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if row is None:
        raise TransitionError(f"no job with id={job_id}")
    from_status = row["status"]

    # 2. legality
    allowed = TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise TransitionError(
            f"illegal transition {from_status!r} -> {to_status!r} "
            f"(allowed: {sorted(allowed) or 'none, terminal'})"
        )

    # 3. note requirement
    if to_status in REQUIRES_NOTE and not (note and note.strip()):
        raise TransitionError(
            f"transition to {to_status!r} requires a non-empty note"
        )

    # 4. atomic dual write -- one event, one timestamp, two rows
    now = _now()
    with conn:  # transaction: commits on success, rolls back on exception
        conn.execute(
            "UPDATE jobs SET status = ?, status_updated_at = ? WHERE id = ?",
            (to_status, now, job_id),
        )
        conn.execute(
            "INSERT INTO job_events (job_id, from_status, to_status, at, note) "
            "VALUES (?, ?, ?, ?, ?)",
            (job_id, from_status, to_status, now, note),
        )


def transition(
    conn: sqlite3.Connection,
    job_id: int,
    to_status: str,
    note: str | None = None,
) -> None:
    """Public door for every ordinary status change.

    Handles new->drafted, any->archived, applied->rejected. Refuses 'applied'
    outright: that move has an external precondition (a real ATS submission
    plus a work-authorization check) and must go through apply_to_job(), which
    is the only place that check lives. Routing it here would let a caller
    reach 'applied' without ever running authorization.
    """
    if to_status == "applied":
        raise TransitionError("use apply_to_job() to reach 'applied'")
    _apply_transition(conn, job_id, to_status, note)


def apply_to_job(
    conn: sqlite3.Connection,
    job_id: int,
    note: str,
) -> None:
    """The one door to 'applied'.

    Runs the work-authorization check (the Uken blind spot: eligibility that
    the pipeline cannot see from the job body) BEFORE any write. `note` carries
    the ATS metadata and is enforced non-empty by REQUIRES_NOTE in the core.
    An authorization failure raises and nothing is written.
    """
    # Part 3 seam -- authorization is a real requirement, not yet built.
    # Left as an explicit raise so the door exists but cannot silently pass
    # an unauthorized application. Do NOT delete this to "make it work".
    raise NotImplementedError(
        "apply_to_job: work-authorization check (Module 4 Part 3) not built"
    )
    # Once authorization exists and passes, the write is exactly:
    #   _apply_transition(conn, job_id, "applied", note)
