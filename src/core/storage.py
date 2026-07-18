"""SQLite storage layer: the orchestrator's system of record."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "jobs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source            TEXT NOT NULL,
    external_id       TEXT NOT NULL,
    url               TEXT,
    title             TEXT,
    company           TEXT,
    category          TEXT,
    job_type          TEXT,
    location          TEXT,
    salary            TEXT,
    description       TEXT,
    publication_date  TEXT,
    fetched_at        TEXT,
    content_hash      TEXT,
    prefilter_pass    INTEGER,
    ladder_match      TEXT,
    relevance_score   INTEGER,
    score_reason      TEXT,
    status            TEXT DEFAULT 'new',
    status_updated_at TEXT,
    notes             TEXT,
    UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS job_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    at          TEXT NOT NULL,
    note        TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
"""

COLUMNS = [
    "source", "external_id", "url", "title", "company",
    "category", "job_type", "location", "salary", "description",
    "publication_date", "fetched_at", "content_hash",
]


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open a connection with name-based row access."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    """Create the jobs table if it does not exist."""
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)


def insert_new_jobs(conn: sqlite3.Connection, jobs: list[dict]) -> dict:
    """Insert jobs, skipping duplicates on (source, external_id).

    NOT an upsert. On conflict the existing row is left UNTOUCHED, so
    changed adapter values never reach rows already in the DB. Consequence:
    the `Worldwide` tokens the Himalayas adapter fabricated from empty
    locationRestrictions (fixed 2026-07; silence-for-silence) are frozen
    in rows collected before the fix, and re-collection cannot rewrite
    them -- that requires an explicit migration. A count is deliberately
    not stated here: an earlier version said "278", which a later purge
    made stale, and surviving fabricated tokens are now indistinguishable
    from legitimate worldwide-filtered results. Docstrings should not
    carry numbers the DB can contradict.
    Returns a {"new": int, "duplicate": int} summary.
    """
    placeholders = ", ".join(["?"] * len(COLUMNS))
    col_names = ", ".join(COLUMNS)
    # INSERT OR IGNORE relies on the UNIQUE(source, external_id) constraint
    # to drop re-seen postings without an extra SELECT.
    sql = f"INSERT OR IGNORE INTO jobs ({col_names}) VALUES ({placeholders})"

    new_count = dup_count = 0
    for job in jobs:
        cur = conn.execute(sql, [job.get(c) for c in COLUMNS])
        if cur.rowcount == 1:
            new_count += 1
        else:
            dup_count += 1
    conn.commit()
    return {"new": new_count, "duplicate": dup_count}


if __name__ == "__main__":
    init_db()
    sample = [{
        "source": "remotive", "external_id": "test-1",
        "url": "https://example.com", "title": "Data Analyst",
        "company": "Acme", "category": "data", "job_type": "full_time",
        "location": "Worldwide", "salary": None,
        "description": "test", "publication_date": "2026-06-25",
        "fetched_at": "2026-06-25T12:00:00", "content_hash": "abc",
    }]
    with get_connection() as conn:
        print(insert_new_jobs(conn, sample))
        print(insert_new_jobs(conn, sample))
