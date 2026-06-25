"""Remotive source adapter: fetch remote jobs and map them to the common schema.

Public API: https://remotive.com/api/remote-jobs (no key required).
Postings are delayed ~24h; the provider asks for at most ~4 calls/day.
This adapter has no knowledge of storage; it only normalizes external data.
"""

import hashlib
from datetime import datetime, timezone

import requests

BASE_URL = "https://remotive.com/api/remote-jobs"
SOURCE = "remotive"
TIMEOUT = 30


def fetch_remotive(category: str = "data", limit: int = 50) -> list[dict]:
    """Fetch raw job listings for a Remotive category slug."""
    params = {"category": category, "limit": limit}
    headers = {"User-Agent": "job-search-orchestrator (personal use)"}

    resp = requests.get(BASE_URL, params=params, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()

    jobs = resp.json().get("jobs", [])
    print(f"[fetch] {SOURCE}/{category}: {len(jobs)} jobs")
    return jobs


def _content_hash(title: str, description: str) -> str:
    """Short hash of title+description to detect later content changes."""
    raw = f"{title}\n{description}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def normalize_remotive(raw: dict) -> dict:
    """Map one raw Remotive listing to the common schema."""
    title = raw.get("title") or ""
    description = raw.get("description") or ""

    return {
        "source": SOURCE,
        "external_id": str(raw.get("id")),
        "url": raw.get("url"),
        "title": title,
        "company": raw.get("company_name"),
        "category": raw.get("category"),
        "job_type": raw.get("job_type"),
        "location": raw.get("candidate_required_location"),
        "salary": raw.get("salary") or None,
        "description": description,
        "publication_date": raw.get("publication_date"),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "content_hash": _content_hash(title, description),
    }


if __name__ == "__main__":
    raw_jobs = fetch_remotive(category="data", limit=3)
    if not raw_jobs:
        print("[warn] no jobs returned; the category slug may be wrong")
    else:
        first = normalize_remotive(raw_jobs[0])
        print("\n[normalize] first listing mapped to schema:")
        for key, value in first.items():
            preview = str(value)
            if len(preview) > 70:
                preview = preview[:70] + "..."
            print(f"  {key:18} = {preview}")
