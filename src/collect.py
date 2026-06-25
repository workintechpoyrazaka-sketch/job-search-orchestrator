"""Module 1 (collect): fetch from a source, normalize, and persist with dedup.

Run: python -m src.collect
"""

from src.adapters import remotive
from src.core.storage import get_connection, init_db, upsert_jobs


def collect_remotive(category: str = "data", limit: int = 50) -> dict:
    """Collect Remotive listings into the database; return the upsert summary."""
    init_db()

    raw_jobs = remotive.fetch_remotive(category=category, limit=limit)
    normalized = [remotive.normalize_remotive(j) for j in raw_jobs]

    with get_connection() as conn:
        result = upsert_jobs(conn, normalized)
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    print(
        f"[collect] {category}: {result['new']} new, "
        f"{result['duplicate']} duplicate (db total: {total})"
    )
    return result


if __name__ == "__main__":
    collect_remotive(category="data", limit=50)
