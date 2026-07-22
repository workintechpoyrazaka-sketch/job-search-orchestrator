from fastapi import APIRouter, Depends, HTTPException
from src.api.db import operator_conn

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
