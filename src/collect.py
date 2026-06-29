"""Module 1 (collect): fetch from a source, normalize, and persist with dedup.
Run: python -m src.collect
"""
import time

from src.adapters import remotive
from src.core.storage import get_connection, init_db, upsert_jobs

# Search terms tuned to the Data Analyst ladder. Each term = one API call.
# `search` matches title+description, so this casts a wider net than a single
# category. The deterministic prefilter trims the noise afterwards (no LLM cost).
SEARCH_QUERIES = [
    "data analyst",
    "business intelligence",
    "analytics",
]
# Remotive asks for infrequent calls (~2/min). Space queries out to be polite.
CALL_DELAY_SEC = 30


def collect_remotive(category: str | None = None, search: str | None = None,
                     limit: int = 50) -> dict:
    """Collect one Remotive query into the database; return the upsert summary."""
    init_db()
    raw_jobs = remotive.fetch_remotive(category=category, search=search, limit=limit)
    normalized = [remotive.normalize_remotive(j) for j in raw_jobs]
    with get_connection() as conn:
        result = upsert_jobs(conn, normalized)
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    label = f"search={search}" if search else f"category={category or 'data'}"
    print(
        f"[collect] {label}: {result['new']} new, "
        f"{result['duplicate']} duplicate (db total: {total})"
    )
    return result


def collect_many(queries: list[str] = SEARCH_QUERIES, limit: int = 50,
                 delay: int = CALL_DELAY_SEC) -> dict:
    """Run several search queries to widen the pool.

    Dedup is automatic: upsert relies on UNIQUE(source, external_id), so a job
    returned by more than one query is only stored once. Calls are spaced out
    to respect Remotive's rate guidance.
    """
    totals = {"new": 0, "duplicate": 0}
    for i, q in enumerate(queries):
        r = collect_remotive(search=q, limit=limit)
        totals["new"] += r["new"]
        totals["duplicate"] += r["duplicate"]
        if i < len(queries) - 1:
            print(f"[collect] sleeping {delay}s (rate limit)...")
            time.sleep(delay)
    print(
        f"[collect] DONE across {len(queries)} queries: "
        f"{totals['new']} new, {totals['duplicate']} duplicate"
    )
    return totals


if __name__ == "__main__":
    collect_many()
