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

import re
import psycopg
from datetime import datetime, timezone


# --- policy: state shape lives here, not in the schema -----------------------

# Legal forward-only moves. Every state can also reach 'archived' (the single
# escape). 'archived' and 'rejected' are terminal (empty destination sets).
TRANSITIONS: dict[str, set[str]] = {
    "new":      {"drafted", "applied", "archived"},
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


def status_domain_sql() -> str:
    """Project TRANSITIONS into a CHECK clause for the schema layer.

    Generated, never hand-written: the value set is derived from TRANSITIONS
    (keys plus every destination), so adding a state there regenerates the
    constraint and no second list can drift. Enforces the legal DOMAIN (which
    values may exist), never the legal TRANSITIONS -- the graph stays here.

    Safe to interpolate: the state names are module-level literals, never
    caller input, so there is no injection surface.
    """
    states = sorted(set(TRANSITIONS) | {v for s in TRANSITIONS.values() for v in s})
    values = ", ".join(f"'{s}'" for s in states)
    return f"CHECK (status IN ({values}))"

# Phrases that hint a job may be eligibility-gated in a way the pipeline cannot
# see from structured fields (the Uken blind spot). This list is an ASSISTANT,
# NOT AN ORACLE: a hit directs the eye to a likely disqualifier, but an EMPTY
# result is NOT a clearance -- it only means no *known* phrase matched. New
# postings will phrase restrictions in ways not listed here. The apply gate
# must force a human look regardless of whether this returns anything.
# Queued (improvement backlog): replace with structured extraction (Path 2).
RED_FLAG_PATTERNS: list[str] = [
    r"u\.?s\.?\s+only",
    r"us[- ]based only",
    r"must\s+(?:be\s+)?(?:reside|located|based)",
    r"located in the (?:us|united states|uk|eu)",
    r"authoriz(?:ed|ation)\s+to work",
    r"work\s+authoriz",
    r"no\s+sponsorship",
    r"(?:visa\s+)?sponsorship\s+(?:is\s+)?not",
    r"unable to sponsor",
    r"no\s+c2c",
    r"citizens?\s+only",
    r"eligible to work in",
    r"within\s+(?:the\s+)?(?:us|usa|uk|eu|canada)",
    r"(?:utc|gmt)\s*[+\-]\s*\d",
]


def scan_red_flags(text: str | None) -> list[str]:
    """Return the red-flag phrases found in `text` (case-insensitive).

    Pure and testable: no printing, no I/O. Returns the literal matched
    substrings so the caller can show the human exactly what tripped.
    An EMPTY list means "no known pattern matched" -- NOT "eligible".
    The caller must treat absence as unknown, never as clearance.
    """
    if not text:
        return []
    hits: list[str] = []
    for pat in RED_FLAG_PATTERNS:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            hits.append(m.group(0))
    return hits


class TransitionError(Exception):
    """Raised when a requested status change violates policy.

    Nothing is written when this is raised -- guards run before the
    transaction opens, so a rejected move leaves the DB untouched.
    """


def _now() -> datetime:
    """One timezone-aware UTC timestamp. psycopg adapts it to timestamptz;
    written to both jobs.status_updated_at and job_events.at so they match."""
    return datetime.now(timezone.utc)


def _apply_transition(
    conn: psycopg.Connection,
    job_id: int,
    to_status: str,
    note: str | None = None,
) -> None:
    """Atomically move one job to a new status and record the event.

    The ONLY function that writes a status change. Contract:
      1. Inside the transaction, SELECT current status FOR UPDATE (locks the
         row, closing the read->write TOCTOU window). Missing row -> raise.
      2. Validate from_status -> to_status against TRANSITIONS; illegal -> raise.
      3. If to_status in REQUIRES_NOTE, note must be non-empty -> else raise.
      4. UPDATE jobs + INSERT job_events. Both or neither.

    The guards run INSIDE the transaction, because the FOR UPDATE lock must be
    held from the status read through the write. A raise still leaves the DB
    untouched -- conn.transaction() rolls back the (write-free) transaction on
    any exception. The timestamp is computed once and written to both rows so
    status_updated_at and the event's `at` are equal by construction.
    """
    with conn.transaction():
        # 1. resolve current status under a row lock (orphan guard + TOCTOU close)
        row = conn.execute(
            "SELECT status FROM jobs WHERE id = %s FOR UPDATE", (job_id,)
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
        conn.execute(
            "UPDATE jobs SET status = %s, status_updated_at = %s WHERE id = %s",
            (to_status, now, job_id),
        )
        conn.execute(
            "INSERT INTO job_events (job_id, from_status, to_status, at, note) "
            "VALUES (%s, %s, %s, %s, %s)",
            (job_id, from_status, to_status, now, note),
        )


def transition(
    conn: psycopg.Connection,
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
    conn: psycopg.Connection,
    job_id: int,
    note: str,
    *,
    authorized: bool,
) -> None:
    """The one door to 'applied'. Pure engine -- no I/O, deterministic.

    `authorized` is the human's assertion that work-authorization / location
    eligibility has been verified for THIS job (the Uken blind spot: a fact
    that lives on the application form, not in the stored posting). This
    function does not compute that fact -- it refuses to write unless the fact
    is asserted True. The forcing function that MAKES the human look lives one
    layer out (confirm_and_apply, the CLI bouncer), which is the only sanctioned
    producer of authorized=True. Keeping this function free of input()/print
    is what keeps it testable and reusable from a script, GUI, or API.

    `note` carries the ATS metadata; REQUIRES_NOTE enforces it non-empty in the
    core. An unauthorized call raises and nothing is written.
    """
    if not authorized:
        raise TransitionError(
            f"job {job_id}: not authorized -- eligibility must be verified "
            f"before applying (use confirm_and_apply)"
        )
    _apply_transition(conn, job_id, "applied", note)


def verify_invariants(conn: psycopg.Connection) -> list[str]:
    """Check the invariant stated at the top of this module, for real.

    Returns a list of human-readable violations; empty list means the DB
    honors the contract. Three clauses:
      1. every non-'new' job has at least one event (no unevidenced state)
      2. every evented job's status equals its latest event's to_status
      3. every evented job's status_updated_at equals its latest event's at
    Stated here since Module 4; enforced nowhere until 2026-07-18. Callers
    decide whether violations are fatal (build_demo: yes).
    """
    problems: list[str] = []

    rows = conn.execute("""
        SELECT j.id FROM jobs j
        WHERE j.status != 'new'
          AND NOT EXISTS (SELECT 1 FROM job_events e WHERE e.job_id = j.id)
    """).fetchall()
    if rows:
        problems.append(f"non-'new' jobs with no event: {[r[0] for r in rows]}")

    rows = conn.execute("""
        SELECT j.id, j.status,
               (SELECT e.to_status FROM job_events e WHERE e.job_id = j.id
                ORDER BY e.at DESC, e.id DESC LIMIT 1) AS latest
        FROM jobs j
        WHERE EXISTS (SELECT 1 FROM job_events e WHERE e.job_id = j.id)
          AND j.status != (SELECT e.to_status FROM job_events e
                           WHERE e.job_id = j.id
                           ORDER BY e.at DESC, e.id DESC LIMIT 1)
    """).fetchall()
    if rows:
        problems.append(f"status != latest event.to_status: {[tuple(r) for r in rows]}")

    rows = conn.execute("""
        SELECT j.id FROM jobs j
        WHERE EXISTS (SELECT 1 FROM job_events e WHERE e.job_id = j.id)
          AND j.status_updated_at != (SELECT e.at FROM job_events e
                                      WHERE e.job_id = j.id
                                      ORDER BY e.at DESC, e.id DESC LIMIT 1)
    """).fetchall()
    if rows:
        problems.append(f"status_updated_at != latest event.at: {[r[0] for r in rows]}")

    orphans = conn.execute("""
        SELECT e.id FROM job_events e
        WHERE NOT EXISTS (SELECT 1 FROM jobs j WHERE j.id = e.job_id)
    """).fetchall()
    if orphans:
        problems.append(f"events referencing missing jobs: {[r[0] for r in orphans]}")

    return problems
