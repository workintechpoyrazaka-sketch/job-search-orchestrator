import os
import asyncpg
from src.api.auth import require_operator
from fastapi import Request, HTTPException, Depends

# DATABASE_URL MUST authenticate as orchestrator_app (the app login role),
# not the superuser — that is what makes the SET LOCAL ROLE boundary real.
DATABASE_URL = os.environ["DATABASE_URL"]


async def open_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=10)


async def public_conn(request: Request):
    """One connection, role-scoped to public_reader, for the whole request.

    Owns the connection lifecycle, so 'does the pool keep one connection
    per request' is answered by construction. Handlers MUST query on the
    yielded conn — a mid-handler pool checkout would run as orchestrator_app.
    SET LOCAL auto-resets at transaction end, so the connection is never
    returned to the pool carrying public_reader.
    """
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE public_reader")
            role = await conn.fetchval("SELECT current_role")
            if role != "public_reader":            # fail-closed readback
                raise HTTPException(500, "public role assertion failed")
            yield conn


async def operator_conn(
    request: Request,
    _: None = Depends(require_operator),
):
    """One connection for the whole request, running as orchestrator_app.

    Mirrors public_conn's lifecycle, with two deliberate differences:
      - require_operator runs FIRST (as a dependency), so an unauthenticated
        request never reaches the pool — 401 before a connection is touched.
      - NO 'SET LOCAL ROLE': this path stays orchestrator_app, which can read
        the withheld columns AND insert job_events, but CANNOT update or delete
        them. The grant table, not this code, is what guarantees that — a
        leaked token still hits a wall at the database.
    """
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        async with conn.transaction():
            role = await conn.fetchval("SELECT current_role")
            if role != "orchestrator_app":         # fail-closed readback
                raise HTTPException(500, "operator role assertion failed")
            yield conn
