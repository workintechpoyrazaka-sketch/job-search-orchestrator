"""Deterministic prefilter: cheap, LLM-free triage before scoring.

Drops only the certain no's (region-locked postings or off-path titles) so
the LLM scorer spends tokens on plausible matches. Relevance nuance (e.g.
"is data labeling the right kind of data role?") is left to the scorer.
"""

import re

from src.core.storage import get_connection

# --- targeting config (edit to retune) ---

# Remote eligibility for a Turkey-based candidate.
ELIGIBLE_HINTS = (
    "worldwide", "anywhere", "global", "emea", "europe",
    "turkey", "turkiye",
)
# Region tokens that exclude a Turkey-based candidate. Matched as whole
# words to avoid false hits (e.g. "usa" inside "usability").
EXCLUDE_REGIONS = (
    "usa", "united states", "canada", "latam", "brazil",
    "uk only", "india", "philippines", "australia",
    "argentina", "mexico",
)

# Career ladder. First match wins; sets ladder_match.
LADDER = [
    ("data_analyst", ("data analyst", "bi analyst", "business intelligence",
                      "reporting analyst", "operations analyst",
                      "product analyst", "data analytics")),
    ("analytics_engineer", ("analytics engineer", "data engineer", "bi developer")),
    ("ai_engineer", ("ai engineer", "ml engineer", "machine learning engineer")),
]

# A title with no data signal and no ladder match is treated as off-path.
DATA_SIGNAL = ("data", "analyst", "analytics", "business intelligence",
               "sql", "reporting", "insight")

# Hard exclusions: off-path even when they mention data.
EXCLUDE_TITLE = ("sales", "marketing", "designer", "customer support",
                 "recruiter", "account executive", "product manager",
                 "product engineer", "copywriter", "social media")


def _has_word(text: str, word: str) -> bool:
    """Whole-word / phrase match, case-insensitive."""
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text) is not None


def is_remote_eligible(location: str | None) -> bool:
    loc = (location or "").lower()
    if not loc:
        return True
    # An eligible region anywhere in a mixed list wins ("Americas, Europe").
    if any(h in loc for h in ELIGIBLE_HINTS):
        return True
    if any(_has_word(loc, r) for r in EXCLUDE_REGIONS):
        return False
    return True  # unknown region: let it through, scorer refines


def match_ladder(title: str | None) -> str | None:
    t = (title or "").lower()
    for tier, terms in LADDER:
        if any(term in t for term in terms):
            return tier
    return None


def is_relevant_title(title: str | None, ladder: str | None) -> bool:
    t = (title or "").lower()
    if ladder:                      # a ladder role always passes
        return True
    if any(x in t for x in EXCLUDE_TITLE):
        return False
    return any(s in t for s in DATA_SIGNAL)


def evaluate(title: str | None, location: str | None) -> dict:
    ladder = match_ladder(title)
    eligible = is_remote_eligible(location)
    relevant = is_relevant_title(title, ladder)
    return {
        "prefilter_pass": 1 if (eligible and relevant) else 0,
        "ladder_match": ladder,
    }


def run_prefilter(conn) -> dict:
    """Evaluate jobs not yet prefiltered; update rows in place."""
    rows = conn.execute(
        "SELECT id, title, location FROM jobs WHERE prefilter_pass IS NULL"
    ).fetchall()

    passed = failed = 0
    for row in rows:
        verdict = evaluate(row["title"], row["location"])
        conn.execute(
            "UPDATE jobs SET prefilter_pass = ?, ladder_match = ? WHERE id = ?",
            (verdict["prefilter_pass"], verdict["ladder_match"], row["id"]),
        )
        passed += verdict["prefilter_pass"]
        failed += 1 - verdict["prefilter_pass"]
    conn.commit()
    return {"processed": len(rows), "passed": passed, "failed": failed}


if __name__ == "__main__":
    with get_connection() as conn:
        s = run_prefilter(conn)
        print(f"[prefilter] processed {s['processed']}: "
              f"{s['passed']} passed, {s['failed']} failed")

        print("\n[sample] passed:")
        for r in conn.execute(
            "SELECT title, location, ladder_match FROM jobs "
            "WHERE prefilter_pass = 1 LIMIT 10"
        ):
            print(f"  [{r['ladder_match'] or '-'}] {r['title']} ({r['location']})")

        print("\n[sample] rejected:")
        for r in conn.execute(
            "SELECT title, location FROM jobs WHERE prefilter_pass = 0 LIMIT 10"
        ):
            print(f"  {r['title']} ({r['location']})")
