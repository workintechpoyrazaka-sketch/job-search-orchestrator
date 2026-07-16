"""Greenhouse source adapter: fetch ATS job posts and map them to the common schema.

Public API: https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs (no key,
documented as un-rate-limited). Greenhouse is an ATS, NOT an aggregator: there is
no global feed, only per-company boards addressed by a `board_token` (slug). SLUGS
below is that list -- the one piece of config this adapter owns. `fetch` ignores
`search` and pulls each board whole (boards are tens of rows), exactly like the
RemoteOK adapter; the deterministic prefilter trims afterwards.

Why this source is different in kind: every other adapter stores an aggregator's
CLAIM about a job. A Greenhouse row comes from the employer's own ATS -- the same
record the application form hangs off. `absolute_url` is the real door, not a
router, and a role absent here is absent, whatever a job board still displays.

Three quirks, each verified against a live board (not assumed from the docs):
  - `content` is entity-ESCAPED HTML ("&lt;p&gt;"), unlike RemoteOK's raw HTML,
    and entities inside it are escaped twice ("&amp;nbsp;"). Strip-then-unescape
    would match no tags at all. The order below is unescape -> strip -> unescape.
  - Boards carry a talent-pool pseudo-post ("Interested in working for X?") with
    internal_job_id = null. It is not a job; it is dropped in fetch.
  - Titles carry stray whitespace (" Video Editor", "Finance Manager "): strip.

This adapter has no knowledge of storage; it only normalizes external data.
"""
import hashlib
import html as html_lib
import re
from datetime import datetime, timezone

import requests

BASE_URL = "https://boards-api.greenhouse.io/v1/boards"
SOURCE = "greenhouse"
TIMEOUT = 30

# Board tokens to poll. Found in a company's board URL:
# https://job-boards.greenhouse.io/<slug>  ->  slug.
# Only slugs VERIFIED to resolve belong here -- a typo yields a silent 404 and an
# empty board, which looks identical to "no jobs today". _fetch_board raises on
# HTTP error rather than swallowing, so a dead slug fails loudly on the next run.
SLUGS = [
    "sportygroup",
]


def _fetch_board(slug: str) -> list[dict]:
    """Fetch one board's published posts, with descriptions. Raises on HTTP error.

    `content=true` adds the description, departments and offices to each row; the
    list endpoint omits all three by default. One call returns the whole board
    (no pagination observed at this size), so there is no page loop to get wrong.
    """
    url = f"{BASE_URL}/{slug}/jobs"
    headers = {"User-Agent": "job-search-orchestrator (personal use)"}
    resp = requests.get(url, params={"content": "true"}, headers=headers,
                        timeout=TIMEOUT)
    resp.raise_for_status()
    jobs = resp.json().get("jobs", [])
    # Stamp the slug onto each row: `id` alone is not provably unique ACROSS
    # boards, and normalize needs the slug to build a collision-proof external_id.
    for job in jobs:
        job["_slug"] = slug
    return jobs


def fetch_greenhouse(search: str | None = None) -> list[dict]:
    """Fetch raw posts from every board in SLUGS.

    `search` is accepted to match the registry's lambda shape but is applied
    client-side on the title only, if given at all: the boards are small and the
    API has no search parameter, so the collect loop passes queries that this
    source cannot use server-side (same contract as RemoteOK).

    Talent-pool pseudo-posts are dropped here: they carry internal_job_id = null,
    have no requisition, and are a standing "send us your CV" catch-all. Letting
    one through would spend an LLM scoring call on a non-job.
    """
    jobs: list[dict] = []
    for slug in SLUGS:
        board = _fetch_board(slug)
        real = [j for j in board if j.get("internal_job_id") is not None]
        dropped = len(board) - len(real)
        note = f" ({dropped} non-job dropped)" if dropped else ""
        print(f"[fetch] {SOURCE}/{slug}: {len(real)} jobs{note}")
        jobs.extend(real)
    if search:
        needle = search.lower()
        jobs = [j for j in jobs if needle in (j.get("title") or "").lower()]
        print(f"[fetch] {SOURCE}/q={search}: {len(jobs)} after title filter")
    return jobs


def _content_hash(title: str, description: str) -> str:
    """Short hash of title+description to detect later content changes."""
    raw = f"{title}\n{description}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _clean_description(raw_content: str) -> str:
    """Unescape, strip HTML, and collapse whitespace. Sanitization border.

    Greenhouse ships the description as ESCAPED HTML, so the first unescape turns
    "&lt;p&gt;" into a real tag before the tag regex can see it -- reversing
    RemoteOK's order would silently leave the markup intact. The SECOND unescape
    handles entities that were escaped twice ("&amp;nbsp;" -> "&nbsp;" -> \xa0);
    one pass leaves them as literal text in the stored description.

    Like every normalize, this is the border where fetched content becomes DATA:
    whatever the description says, downstream treats it as text to score, never
    as instructions to follow.
    """
    if not raw_content:
        return ""
    text = html_lib.unescape(raw_content)      # &lt;p&gt; -> <p>
    text = re.sub(r"<[^>]+>", " ", text)       # drop tags
    text = html_lib.unescape(text)             # &nbsp; / &amp; left by pass 1
    text = re.sub(r"\s+", " ", text).strip()   # \s+ also eats \xa0
    return text


def _publication_date(first_published: str | None) -> str | None:
    """Normalize Greenhouse's offset timestamp to the UTC ISO form we store.

    The API returns e.g. "2025-12-11T11:18:33-05:00". Other adapters store UTC
    seconds-precision ISO, so convert rather than pass through: comparing a
    -05:00 string to a +00:00 string lexically would misorder the board.
    """
    if not first_published:
        return None
    parsed = datetime.fromisoformat(first_published)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _category(departments: list | None) -> str | None:
    """Comma-join department names ("Data", "Engineering"), or None."""
    names = [d.get("name") for d in (departments or []) if d.get("name")]
    return ", ".join(names) if names else None


def normalize_greenhouse(raw: dict) -> dict:
    """Map one raw Greenhouse post to the common schema.

    NOTE on `location`: the API's location.name is NOT trustworthy as an
    eligibility signal -- live boards carry "Global - Remote" on posts whose own
    title says "(Europe)" or "(Europe only)". It is stored verbatim as free text,
    the same as RemoteOK's, and the real guard stays where it always was: the
    human at the application form. Do not let a downstream reader treat this
    field as clearance.
    """
    title = (raw.get("title") or "").strip()
    description = _clean_description(raw.get("content") or "")
    return {
        "source": SOURCE,
        "external_id": f"{raw.get('_slug')}:{raw.get('id')}",
        "url": raw.get("absolute_url"),
        "title": title,
        "company": raw.get("company_name"),
        "category": _category(raw.get("departments")),
        "job_type": None,  # Greenhouse exposes no employment-type field
        "location": (raw.get("location") or {}).get("name") or "",
        "salary": None,  # needs pay_input_ranges=true; rarely populated
        "description": description,
        "publication_date": _publication_date(raw.get("first_published")),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "content_hash": _content_hash(title, description),
    }


if __name__ == "__main__":
    raw_jobs = fetch_greenhouse()
    if not raw_jobs:
        print("[warn] no jobs returned; check SLUGS resolve")
    else:
        first = normalize_greenhouse(raw_jobs[0])
        print("\n[normalize] first listing mapped to schema:")
        for key, value in first.items():
            preview = str(value)
            if len(preview) > 70:
                preview = preview[:70] + "..."
            print(f"  {key:18} = {preview}")
        # Markup must not survive into the stored description.
        desc = first["description"]
        leaked = "<" in desc or "&lt;" in desc or "&nbsp;" in desc
        print(f"\n[check] html stripped: {'FAIL' if leaked else 'OK'}")
        print(f"[check] keys match schema: {len(first)} (expect 13)")
