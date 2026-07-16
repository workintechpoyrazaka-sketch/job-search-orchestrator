"""Apply CLI: the human bouncer guarding the door to 'applied'.

This module is the ONLY sanctioned producer of authorized=True for
apply_to_job(). It does the messy, interactive work that must stay OUT of the
pure engine (src/core/tracking.py): it surfaces the eligibility-relevant facts
about a job, scans the description for known red-flag phrases, and forces an
explicit human confirmation before any application is recorded.

Why here and not in tracking.py: tracking.py is a pure, deterministic engine
-- testable, reusable from a script / GUI / API. Interactive I/O would lock it
to a terminal forever. The bouncer lives at the edge; the engine stays clean.

Friction scales with risk: a clean description takes an ordinary [y/N]; a
description that trips a red-flag phrase demands the human type 'yes' in full,
so muscle memory cannot carry an application past a visible disqualifier.
"""

import sys

from src.core.storage import get_connection
from src.core.tracking import (
    TRANSITIONS,
    TransitionError,
    apply_to_job,
    scan_red_flags,
)

# ANSI colors -- harmless if piped; this is a human-facing terminal tool.
_RED = "\033[91m"
_YELLOW = "\033[93m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

# Fields shown at the gate: the ones that make eligibility answerable at a
# glance. (Structured geographic_restrictions / sponsorship extraction is
# queued as Path 2; until then the red-flag scan reads the raw description.)
_APPLY_FIELDS = ("title", "company", "location", "url")


def confirm_and_apply(conn, job_id: int, note: str) -> bool:
    """Surface eligibility facts, then require explicit human confirmation
    before recording an application.

    Returns True if the application was recorded, False if the human aborted
    (abort is a normal outcome, not an error). Raises TransitionError for a
    missing job. Does NOT compute eligibility -- it makes the human look, then
    passes their verified assertion to the engine, which does the atomic write.
    """
    row = conn.execute(
        "SELECT status, title, company, location, url, description "
        "FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        raise TransitionError(f"no job with id={job_id}")

    # Fail fast: don't force a read if the state machine won't allow the write.
    # Reuses TRANSITIONS as the single authority (reads it; does not copy it).
    status = row["status"]
    if "applied" not in TRANSITIONS.get(status, set()):
        valid_from = sorted(s for s, t in TRANSITIONS.items() if "applied" in t)
        print(
            f"job {job_id} is {status!r}; cannot move to 'applied' "
            f"(valid from: {', '.join(valid_from)}). Nothing done."
        )
        return False

    flags = scan_red_flags(row["description"])

    # --- surface the facts -------------------------------------------------
    print()
    print(f"{_BOLD}-- APPLYING TO JOB {job_id} --{_RESET}")
    for f in _APPLY_FIELDS:
        print(f"  {f:<9}: {row[f] or '(none)'}")
    print()

    if flags:
        print(f"{_RED}{_BOLD}  ! {len(flags)} RED FLAG(S) in description:{_RESET}")
        for hit in flags:
            print(f"{_RED}      - {hit!r}{_RESET}")
        print(f"{_RED}  Verify you are eligible for THIS role before applying.{_RESET}")
    else:
        print(
            f"{_YELLOW}  No known red-flag phrases matched. This is NOT a "
            f"clearance --{_RESET}"
        )
        print(
            f"{_YELLOW}  eligibility often lives only on the application form. "
            f"Verify it yourself.{_RESET}"
        )
    print()

    # --- risk-scaled confirmation -----------------------------------------
    # Safe default is ALWAYS abort: blank/Enter/unexpected -> no write.
    if flags:
        answer = input(
            f"{_BOLD}Red flags present. Type 'yes' in full to record the "
            f"application, anything else aborts: {_RESET}"
        ).strip()
        confirmed = answer.lower() == "yes"
    else:
        answer = input(
            f"{_BOLD}Record application to job {job_id}? [y/N]: {_RESET}"
        ).strip().lower()
        confirmed = answer in ("y", "yes")

    if not confirmed:
        print("Aborted. Nothing written.")
        return False

    # The only sanctioned producer of authorized=True. The engine enforces the
    # non-empty note (REQUIRES_NOTE) and does the atomic dual write.
    apply_to_job(conn, job_id, note, authorized=True)
    print(f"OK  job {job_id} -> applied. Event recorded: {note!r}")
    return True


def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m src.apply <job_id> [note]")
        return 2
    try:
        job_id = int(argv[1])
    except ValueError:
        print(f"job_id must be an integer, got {argv[1]!r}")
        return 2

    note = " ".join(argv[2:]).strip()
    if not note:
        note = input("ATS note (e.g. 'Recruitee, submitted 2026-07-13'): ").strip()
    if not note:
        print("A note is required (the ATS metadata for this application).")
        return 2

    conn = get_connection()
    try:
        confirm_and_apply(conn, job_id, note)
    except TransitionError as e:
        print(f"error: {e}")
        return 1
    finally:
        conn.close()  # explicit close: the CM commits, it does not close
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
