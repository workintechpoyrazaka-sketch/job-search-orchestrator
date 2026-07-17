"""
Build a synthetic, PII-free demo database for the public dashboard.

Why this exists: the real data/jobs.db is gitignored PII (real companies
targeted, real application outcomes, profile-grounded cover letters). The
deployed dashboard must never see it. This script generates a stand-in with
fabricated-but-clearly-sample rows so the live portfolio link shows the
pipeline's shape without leaking a real job search.

Honesty boundary:
  - job listings are synthesized (sample data, understood as such).
  - job_events is left EMPTY. Faking audit events would be fabricating
    execution history, which is a different (and dishonest) thing than
    populating demo furniture. The audit trail stays honestly empty.

Schema is NOT redefined here. We import init_db from the real storage layer
so the demo DB is byte-identical in schema to production by construction.

Run from repo root:  python -m seeds.build_demo
Output:              data/demo.db  (committed; see .gitignore negation)
"""

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.core.storage import get_connection, init_db

# Deterministic output: same seed -> same demo.db every run (reproducible).
random.seed(1974)

DEMO_PATH = Path(__file__).resolve().parents[1] / "data" / "demo.db"

# Obviously-fictional companies. Fake-but-labeled, never plausibly-real:
# no one should mistake the demo for a real hiring signal or collide with
# an actual company name.
COMPANIES = [
    "Northwind Analytics", "Globex Data", "Initech Remote", "Umbrella Insights",
    "Hooli Metrics", "Stark Data Co", "Wayne Analytics", "Cyberdyne Reporting",
    "Soylent BI", "Vandelay Data", "Wonka Analytics", "Acme Insights",
    "Prestige Data Group", "Duff Analytics", "Gekko Metrics", "Tyrell Reporting",
]

TITLES = [
    "Data Analyst", "Senior Data Analyst", "BI Analyst", "Analytics Engineer",
    "Business Intelligence Developer", "Reporting Analyst", "Data Scientist",
    "Product Analyst", "Marketing Analyst", "Operations Analyst",
    "Data Visualization Engineer", "Insights Analyst",
]

SOURCES = ["remotive", "himalayas", "remoteok"]
LOCATIONS = ["Remote (Worldwide)", "Remote (EMEA)", "Remote (Global)", "Remote"]
JOB_TYPES = ["full_time", "contract"]

# Insert columns, explicit. We insert directly rather than via insert_new_jobs
# because the demo must SET status/score/notes, which insert_new_jobs (a
# collect-only helper) does not touch.
INSERT_COLS = [
    "source", "external_id", "url", "title", "company", "category",
    "job_type", "location", "salary", "description", "publication_date",
    "fetched_at", "content_hash", "prefilter_pass", "ladder_match",
    "relevance_score", "score_reason", "status", "status_updated_at", "notes",
]

# Target funnel shape (mirrors the real board's proportions loosely):
# mostly new, a handful drafted, a couple applied, one archived.
STATUS_PLAN = (["new"] * 26) + (["drafted"] * 5) + (["applied"] * 3) + (["archived"] * 1)


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _score_for(status: str) -> int | None:
    """Realistic score distribution. 'new' rows are a mix of scored and
    unscored (NULL) so the histogram's scored/unscored split looks alive."""
    if status in ("drafted", "applied"):
        return random.randint(78, 92)      # only strong rows get drafted/applied
    if status == "archived":
        return random.randint(70, 85)
    # 'new': ~40% still unscored (NULL), rest spread across the full range
    if random.random() < 0.4:
        return None
    return random.randint(15, 95)


def build_rows() -> list[tuple]:
    rows = []
    for i, status in enumerate(STATUS_PLAN):
        source = random.choice(SOURCES)
        company = random.choice(COMPANIES)
        title = random.choice(TITLES)
        slug = title.lower().replace(" ", "-")
        score = _score_for(status)
        drafted_or_beyond = status in ("drafted", "applied", "archived")
        rows.append((
            source,
            f"demo-{i:04d}",                                   # external_id
            f"https://example.com/{company.lower().replace(' ', '-')}/jobs/{slug}",
            title,
            company,
            "Data & Analytics",                               # category
            random.choice(JOB_TYPES),
            random.choice(LOCATIONS),
            None,                                             # salary (often absent)
            f"Sample listing for a {title} role at {company}. "
            "Synthetic demo data — not a real posting.",      # description
            _iso(random.randint(1, 40)),                      # publication_date
            _iso(random.randint(0, 5)),                       # fetched_at
            f"demohash{i:04d}",                               # content_hash
            1 if score is not None else None,                 # prefilter_pass
            "data_analyst" if drafted_or_beyond else None,    # ladder_match
            score,                                            # relevance_score
            "Synthetic score for demo." if score is not None else None,
            status,
            _iso(random.randint(0, 10)) if status != "new" else None,  # status_updated_at
            # notes only on applied/archived, generic — no real ATS/PII text
            "Demo note." if status in ("applied", "archived") else None,
        ))
    return rows


def main() -> None:
    if DEMO_PATH.exists():
        DEMO_PATH.unlink()                                   # idempotent: fresh each run
        print(f"removed existing {DEMO_PATH.name}")

    init_db(DEMO_PATH)                                        # real schema, no fabricated DDL
    rows = build_rows()

    placeholders = ", ".join(["?"] * len(INSERT_COLS))
    sql = f"INSERT INTO jobs ({', '.join(INSERT_COLS)}) VALUES ({placeholders})"

    with get_connection(DEMO_PATH) as conn:
        conn.executemany(sql, rows)

    # Verify from the live answer, not from the plan we just wrote.
    with get_connection(DEMO_PATH) as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM job_events").fetchone()[0]
        by_status = conn.execute(
            "SELECT status, COUNT(*) FROM jobs GROUP BY status"
        ).fetchall()
        scored = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE relevance_score IS NOT NULL"
        ).fetchone()[0]

    print(f"built {DEMO_PATH}")
    print(f"  total jobs : {total}")
    print(f"  job_events : {events}  (must be 0 — honestly empty)")
    print(f"  scored     : {scored} / {total}")
    print(f"  by status  : {dict(by_status)}")


if __name__ == "__main__":
    main()
