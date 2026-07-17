"""Module 1 (collect): fetch from each source, normalize, and persist with dedup.
Run: python -m src.collect
"""
import time

from src.adapters import greenhouse, himalayas, remoteok, remotive
from src.core.storage import get_connection, init_db, insert_new_jobs

# Search terms tuned to the Data Analyst ladder. Each term = one API call per
# source. `search` matches title+description, so this casts a wide net; the
# deterministic prefilter trims the noise afterwards (no LLM cost).
SEARCH_QUERIES = [
    "data analyst",
    "business intelligence",
    "analytics",
]
# Space calls out to respect each source's rate guidance (Remotive ~2/min).
CALL_DELAY_SEC = 30

# Adapter registry: source -> (fetch-by-keyword, normalize). Each fetch is
# wrapped to a single `search` argument so the core loop stays source-agnostic.
# This is the "single core, many adapters" seam: adding a source is one import
# plus one entry here; storage / prefilter / scoring never change.
# Himalayas is queried worldwide-only: an applicant with no local work
# authorization can only apply to worldwide-open roles, so filtering server-side
# maximizes eligible candidates per call. Remotive has no such filter, so its
# eligibility is handled downstream by the prefilter as before.
ADAPTERS = {
    remotive.SOURCE: (
        lambda q: remotive.fetch_remotive(search=q, limit=50),
        remotive.normalize_remotive,
    ),
    himalayas.SOURCE: (
        lambda q: himalayas.fetch_himalayas(search=q, worldwide=True),
        himalayas.normalize_himalayas,
    ),
    remoteok.SOURCE: (
        lambda q: remoteok.fetch_remoteok(),  # q ignored: küçük feed, budamayı prefilter yapar
        remoteok.normalize_remoteok,
    ),
    greenhouse.SOURCE: (
        lambda q: greenhouse.fetch_greenhouse(),  # q ignored: whole board, prefilter trims
        greenhouse.normalize_greenhouse,
    ),
}


def collect_one(source: str, search: str) -> dict:
    """Collect one (source, query) into the database; return the upsert summary."""
    init_db()
    fetch, normalize = ADAPTERS[source]
    raw_jobs = fetch(search)
    normalized = [normalize(j) for j in raw_jobs]
    with get_connection() as conn:
        result = insert_new_jobs(conn, normalized)
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    print(
        f"[collect] {source}/search={search}: {result['new']} new, "
        f"{result['duplicate']} duplicate (db total: {total})"
    )
    return result


def collect_many(sources: list[str] | None = None,
                 queries: list[str] = SEARCH_QUERIES,
                 delay: int = CALL_DELAY_SEC) -> dict:
    """Run every query against every source to widen the pool.

    Dedup is automatic: upsert relies on UNIQUE(source, external_id), so a
    posting is stored once per source. Calls are spaced out to respect rate
    guidance. Sources are independent: one source's limits never affect another.
    """
    sources = sources or list(ADAPTERS)
    totals = {"new": 0, "duplicate": 0}
    calls = [(s, q) for s in sources for q in queries]
    for i, (s, q) in enumerate(calls):
        r = collect_one(s, q)
        totals["new"] += r["new"]
        totals["duplicate"] += r["duplicate"]
        if i < len(calls) - 1:
            print(f"[collect] sleeping {delay}s (rate limit)...")
            time.sleep(delay)
    print(
        f"[collect] DONE across {len(sources)} sources x {len(queries)} queries: "
        f"{totals['new']} new, {totals['duplicate']} duplicate"
    )
    return totals


if __name__ == "__main__":
    collect_many()
