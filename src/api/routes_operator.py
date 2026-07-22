from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.db import operator_conn, sqlite_writer_conn
from src.core.tracking import (
    TRANSITIONS,
    TransitionError,
    apply_to_job,
    scan_red_flags,
)

# Deliberately NOT importing PUBLIC: the operator surface exists to read the
# columns the public allowlist withholds. notes/cover_letter are ungranted to
# public_reader, so their presence in a response is itself proof the request
# ran on the operator path — the read layer physically cannot return them.
router = APIRouter(prefix="/api/operator", tags=["operator"])


@router.get("/jobs/{job_id}")
async def job_detail_operator(job_id: int, conn=Depends(operator_conn)):
    row = await conn.fetchrow(
        "SELECT id, title, company, notes, cover_letter FROM jobs WHERE id = $1",
        job_id,
    )
    if row is None:
        raise HTTPException(404, "job not found")
    return dict(row)


class ApplyRequest(BaseModel):
    """Per-job apply payload. Single id in the path; no bulk variant exists,
    so no page load or batch op can ever emit an 'applied' event.
    """
    note: str
    confirm: bool = False
    acknowledge_red_flags: bool = False


@router.post("/jobs/{job_id}/apply")
def apply_job_operator(
    job_id: int,
    body: ApplyRequest,
    conn=Depends(sqlite_writer_conn),
):
    """Record an application — the web analogue of the CLI bouncer.

    Writes SQLite (the system of record until cutover), NOT the asyncpg pool.
    Surfaces the same gate confirm_and_apply does — eligibility facts plus a
    red-flag scan — but as a request contract instead of a terminal prompt: the
    transition stays a deliberate, per-job human act. The write routes through
    apply_to_job so tracking.py's TRANSITIONS / REQUIRES_NOTE stays the single
    authority and _apply_transition remains the sole producer of the applied
    event. This route is a legitimate SECOND bouncer (it may assert
    authorized=True) precisely because it surfaces the gate before it does.
    """
    row = conn.execute(
        "SELECT status, title, company, location, url, description "
        "FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "job not found")

    # Fail fast: don't gate a job the state machine won't move anyway.
    # Reads TRANSITIONS as the single authority; does not copy it.
    status = row["status"]
    if "applied" not in TRANSITIONS.get(status, set()):
        valid_from = sorted(s for s, t in TRANSITIONS.items() if "applied" in t)
        raise HTTPException(
            409,
            f"job {job_id} is '{status}'; cannot move to 'applied' "
            f"(valid from: {', '.join(valid_from)})",
        )

    if not body.note.strip():
        raise HTTPException(400, "a non-empty note is required (ATS metadata)")

    flags = scan_red_flags(row["description"])

    # Applying is a deliberate act. Without an explicit confirm, surface the
    # facts (incl. any red flags) and refuse — the caller comes back confirmed.
    if not body.confirm:
        raise HTTPException(
            409,
            {
                "error": "confirmation required",
                "job": {k: row[k] for k in ("title", "company", "location", "url")},
                "red_flags": flags,
            },
        )

    # Red-flag escalation: muscle memory must not carry an application past a
    # visible disqualifier. A plain confirm won't do it — the caller must
    # separately acknowledge the exact hits (the web 'type yes in full').
    if flags and not body.acknowledge_red_flags:
        raise HTTPException(
            409,
            {
                "error": "red flags present; set acknowledge_red_flags to apply",
                "red_flags": flags,
            },
        )

    try:
        apply_to_job(conn, job_id, body.note, authorized=True)
    except TransitionError as e:
        # Lost race (status moved between read and write) or engine refusal.
        raise HTTPException(409, str(e))

    return {"id": job_id, "status": "applied", "note": body.note}
