"""Himalayas source adapter: fetch remote jobs and map them to the common schema.
Public API: https://himalayas.app/jobs/api/search (no key required).
Data is cached and refreshed every 24h; polling more often than daily is useless.
Search pagination is page-based (page=1,2,...), unlike the browse endpoint's offset.
This adapter has no knowledge of storage; it only normalizes external data.
"""
import hashlib
from datetime import datetime, timezone

import requests

BASE_URL = "https://himalayas.app/jobs/api/search"
SOURCE = "himalayas"
TIMEOUT = 30


def fetch_himalayas(search: str, page: int = 1, sort: str = "recent",
                    worldwide: bool = False) -> list[dict]:
    """Fetch raw job listings from Himalayas' search endpoint.

    `search` is a keyword query matched across the posting. Set `worldwide=True`
    to request only roles open to any location (server-side filter) -- this is
    the eligible set for an applicant with no local work authorization. Results
    are paginated via `page` (20 per page); one page per query for the MVP.
    """
    params = {"q": search, "page": page, "sort": sort}
    if worldwide:
        params["worldwide"] = "true"
    headers = {"User-Agent": "job-search-orchestrator (personal use)"}
    resp = requests.get(BASE_URL, params=params, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    jobs = resp.json().get("jobs", [])
    scope = " [worldwide]" if worldwide else ""
    print(f"[fetch] {SOURCE}/q={search}{scope} (page {page}): {len(jobs)} jobs")
    return jobs


def _content_hash(title: str, description: str) -> str:
    """Short hash of title+description to detect later content changes."""
    raw = f"{title}\n{description}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _location(location_restrictions: list) -> str:
    """Map Himalayas' locationRestrictions array to the common `location` text.

    An empty array means worldwide on Himalayas. We emit the exact token
    'Worldwide' so the existing prefilter (built against Remotive, which also
    uses 'Worldwide') treats these rows identically, with no prefilter change.
    A non-empty array becomes a comma-joined country list.
    """
    if not location_restrictions:
        return "Worldwide"
    return ", ".join(location_restrictions)


def _salary(raw: dict) -> str | None:
    """Compose a human-readable salary string from min/max/currency, or None."""
    low = raw.get("minSalary")
    high = raw.get("maxSalary")
    currency = raw.get("currency") or ""
    if not low and not high:
        return None
    if low and high and low != high:
        amount = f"{low}-{high}"
    else:
        amount = str(low or high)
    return f"{amount} {currency}".strip()


def normalize_himalayas(raw: dict) -> dict:
    """Map one raw Himalayas listing to the common schema."""
    title = raw.get("title") or ""
    description = raw.get("description") or ""
    pub_date = raw.get("pubDate")
    pub_iso = (
        datetime.fromtimestamp(pub_date, tz=timezone.utc).isoformat(timespec="seconds")
        if pub_date
        else None
    )
    categories = raw.get("categories") or []
    return {
        "source": SOURCE,
        "external_id": raw.get("guid"),
        "url": raw.get("applicationLink"),
        "title": title,
        "company": raw.get("companyName"),
        "category": ", ".join(categories) if categories else None,
        "job_type": raw.get("employmentType"),
        "location": _location(raw.get("locationRestrictions") or []),
        "salary": _salary(raw),
        "description": description,
        "publication_date": pub_iso,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "content_hash": _content_hash(title, description),
    }


if __name__ == "__main__":
    raw_jobs = fetch_himalayas(search="data analyst", worldwide=True)
    if not raw_jobs:
        print("[warn] no jobs returned; check the query")
    else:
        first = normalize_himalayas(raw_jobs[0])
        print("\n[normalize] first listing mapped to schema:")
        for key, value in first.items():
            preview = str(value)
            if len(preview) > 70:
                preview = preview[:70] + "..."
            print(f"  {key:18} = {preview}")
