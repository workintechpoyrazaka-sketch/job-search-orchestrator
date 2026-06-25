"""SQLite storage layer — system of record for the orchestrator.

init_db: tabloyu (yoksa) oluşturur.
upsert_jobs: ilanları yazar; (source, external_id) çakışırsa tekrar saymaz.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "jobs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,

    -- toplama (Modül 1 doldurur)
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

    -- skorlama (sonraki modül; şimdi NULL)
    prefilter_pass    INTEGER,
    ladder_match      TEXT,
    relevance_score   INTEGER,
    score_reason      TEXT,

    -- takip (sonraki modül)
    status            TEXT DEFAULT 'new',
    status_updated_at TEXT,
    notes             TEXT,

    UNIQUE (source, external_id)
);
"""


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Bağlantı aç; satırlara isimle erişebilmek için Row factory kur."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    """Tabloyu yoksa oluştur."""
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)


def upsert_jobs(conn: sqlite3.Connection, jobs: list[dict]) -> dict:
    """İlanları yaz. (source, external_id) çakışırsa atla.

    Döner: {"new": X, "duplicate": Y} — bu özet KOS için kanıt.
    """
    new_count = 0
    dup_count = 0

    cols = [
        "source", "external_id", "url", "title", "company",
        "category", "job_type", "location", "salary", "description",
        "publication_date", "fetched_at", "content_hash",
    ]
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)

    # INSERT OR IGNORE: UNIQUE çakışırsa satırı sessizce atlar.
    sql = f"INSERT OR IGNORE INTO jobs ({col_names}) VALUES ({placeholders})"

    for job in jobs:
        values = [job.get(c) for c in cols]
        cur = conn.execute(sql, values)
        if cur.rowcount == 1:      # yeni satır eklendi
            new_count += 1
        else:                       # çakışma → atlandı
            dup_count += 1

    conn.commit()
    return {"new": new_count, "duplicate": dup_count}


# Hızlı manuel test: python -m src.core.storage
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
        print(upsert_jobs(conn, sample))   # 1. koşu: {'new': 1, 'duplicate': 0}
        print(upsert_jobs(conn, sample))   # 2. koşu: {'new': 0, 'duplicate': 1}
