from fastapi import APIRouter, Depends, HTTPException
from seeds.build_demo import PUBLIC, EVENT_PUBLIC   # single authority — no re-typed column list
from src.api.db import public_conn

router = APIRouter(prefix="/api", tags=["public"])

# Column identifiers can't be parameterized; PUBLIC/EVENT_PUBLIC are trusted
# in-repo constants, so f-string interpolation is correct here. User-supplied
# VALUES (job_id, limit, offset) stay parameterized ($1/$2).
_JOB_COLS = ", ".join(PUBLIC)
_EVENT_COLS = ", ".join(EVENT_PUBLIC)


@router.get("/jobs")
async def list_jobs(conn=Depends(public_conn), limit: int = 50, offset: int = 0):
    rows = await conn.fetch(
        f"SELECT {_JOB_COLS} FROM jobs ORDER BY id DESC LIMIT $1 OFFSET $2",
        limit, offset,
    )
    return [dict(r) for r in rows]


@router.get("/jobs/{job_id}")
async def job_detail(job_id: int, conn=Depends(public_conn)):
    row = await conn.fetchrow(f"SELECT {_JOB_COLS} FROM jobs WHERE id = $1", job_id)
    if row is None:
        raise HTTPException(404, "job not found")
    return dict(row)


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: int, conn=Depends(public_conn)):
    rows = await conn.fetch(
        f"SELECT {_EVENT_COLS} FROM job_events WHERE job_id = $1 "
        "ORDER BY at DESC, id DESC",   # same load-bearing tie-break as the invariant
        job_id,
    )
    return [dict(r) for r in rows]
