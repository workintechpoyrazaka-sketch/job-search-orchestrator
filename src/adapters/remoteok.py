"""RemoteOK source adapter: fetch remote jobs and map them to the common schema.
Public API: https://remoteok.com/api (no key required, official JSON feed).
The feed returns a JSON *array* whose first element is a legal/metadata object,
not a job -- real listings are the elements carrying an `id`. Every description
also ends with an anti-spam honeypot line ("Please mention the word ... when
applying"); we strip it here so it never leaks into the scorer or the drafter.
RemoteOK 403s non-browser user agents, so a User-Agent header is mandatory.
This adapter has no knowledge of storage; it only normalizes external data.
"""
import hashlib
import html as html_lib
import re
from datetime import datetime, timezone

import requests

BASE_URL = "https://remoteok.com/api"
SOURCE = "remoteok"
TIMEOUT = 30


def fetch_remoteok(search: str | None = None) -> list[dict]:
    """Fetch raw job listings from RemoteOK's public JSON feed.

    The feed is small (~100 recent roles) and has no reliable server-side search
    param, so we pull it whole and, if `search` is given, filter client-side on
    the title/tags (case-insensitive). The first array element is RemoteOK's
    legal/metadata object, not a job -- we keep only elements carrying an `id`.
    A User-Agent header is required; RemoteOK rejects bot agents with 403.
    """
    headers = {"User-Agent": "job-search-orchestrator (personal use)"}
    resp = requests.get(BASE_URL, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    # Drop the leading legal/metadata object: real listings carry an `id`.
    jobs = [row for row in resp.json() if isinstance(row, dict) and row.get("id")]
    if search:
        needle = search.lower()
        jobs = [
            row for row in jobs
            if needle in (row.get("position") or "").lower()
            or needle in " ".join(row.get("tags") or []).lower()
        ]
    scope = f"/q={search}" if search else ""
    print(f"[fetch] {SOURCE}{scope}: {len(jobs)} jobs")
    return jobs


def _content_hash(title: str, description: str) -> str:
    """Short hash of title+description to detect later content changes."""
    raw = f"{title}\n{description}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _clean_description(raw_html: str) -> str:
    """Strip HTML and RemoteOK's trailing anti-spam honeypot from a description.

    RemoteOK descriptions are HTML and every one ends with an instruction line
    ("Please mention the word X and tag <base64> when applying..."). That line is
    untrusted, injected content -- if it reached the drafter the model might obey
    it -- so we cut from that phrase onward. normalize is the sanitization border:
    fetched content is data, not instructions.
    """
    if not raw_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html_lib.unescape(text)
    text = re.split(r"Please mention the word", text, maxsplit=1)[0]
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _salary(raw: dict) -> str | None:
    """Compose a human-readable USD salary from salary_min/max, or None.

    RemoteOK gives integer USD bounds and uses 0 (falsy) when a bound is absent.
    """
    low = raw.get("salary_min")
    high = raw.get("salary_max")
    if not low and not high:
        return None
    if low and high and low != high:
        return f"${low:,}-${high:,}"
    return f"${(low or high):,}"


def normalize_remoteok(raw: dict) -> dict:
    """Map one raw RemoteOK listing to the common schema."""
    title = raw.get("position") or ""
    description = _clean_description(raw.get("description") or "")
    epoch = raw.get("epoch")
    pub_iso = (
        datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")
        if epoch
        else None
    )
    tags = raw.get("tags") or []
    return {
        "source": SOURCE,
        "external_id": raw.get("id"),
        "url": raw.get("url"),
        "title": title,
        "company": raw.get("company"),
        "category": ", ".join(tags) if tags else None,
        "job_type": None,  # RemoteOK exposes no reliable employment-type field
        "location": raw.get("location") or "",  # raw free text; empty != worldwide
        "salary": _salary(raw),
        "description": description,
        "publication_date": pub_iso,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "content_hash": _content_hash(title, description),
    }


if __name__ == "__main__":
    raw_jobs = fetch_remoteok(search="data")
    if not raw_jobs:
        print("[warn] no jobs returned; check the feed / User-Agent")
    else:
        first = normalize_remoteok(raw_jobs[0])
        print("\n[normalize] first listing mapped to schema:")
        for key, value in first.items():
            preview = str(value)
            if len(preview) > 70:
                preview = preview[:70] + "..."
            print(f"  {key:18} = {preview}")
        # Honeypot must not survive into the stored description.
        leaked = "mention the word" in first["description"].lower()
        print(f"\n[check] honeypot stripped: {'FAIL' if leaked else 'OK'}")
