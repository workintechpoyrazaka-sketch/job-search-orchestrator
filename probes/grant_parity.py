"""Deploy gate: assert public_reader's actual column grants mirror
build_demo.py PUBLIC / EVENT_PUBLIC exactly. Exit 1 on any asymmetry.

Single authority: the allowlists live in build_demo.py. This probe proves
the database agrees with them; it does not define them. Assumes public_reader
holds only column-scoped SELECTs (never table-level SELECT), which is how
roles.sql grants it.
"""
import os
import sys
import asyncio
import asyncpg
from seeds.build_demo import PUBLIC, EVENT_PUBLIC

ROLE = "public_reader"
DATABASE_URL = os.environ["DATABASE_URL"]
EXPECTED = {"jobs": set(PUBLIC), "job_events": set(EVENT_PUBLIC)}


async def granted_columns(conn, table):
    rows = await conn.fetch(
        """
        SELECT column_name FROM information_schema.column_privileges
        WHERE grantee = $1 AND table_name = $2 AND privilege_type = 'SELECT'
        """,
        ROLE, table,
    )
    return {r["column_name"] for r in rows}


async def main():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        violations = []
        for table, expected in EXPECTED.items():
            actual = await granted_columns(conn, table)
            for col in sorted(expected - actual):
                violations.append(f"{table}.{col}: in allowlist, NOT granted to {ROLE}")
            for col in sorted(actual - expected):
                violations.append(f"{table}.{col}: granted to {ROLE}, NOT in allowlist (LEAK)")
    finally:
        await conn.close()

    if violations:
        print(f"grant parity FAILED ({len(violations)}):")
        for v in violations:
            print(f"  {v}")
        sys.exit(1)
    print(f"grant parity OK: jobs={len(EXPECTED['jobs'])} cols, "
          f"job_events={len(EXPECTED['job_events'])} cols, all mirror {ROLE}")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
