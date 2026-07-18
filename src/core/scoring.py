"""LLM relevance scoring: Haiku rates prefiltered jobs 0-100.

Second half of Module 2. Only postings the deterministic prefilter passed
reach the model, so tokens are spent on plausible matches. Every score
carries a reason (layer 1 of the trust model): the model justifies itself,
never a bare number.
"""

import json

from anthropic import Anthropic

from src.core.storage import get_connection

# Pinned model ID (not the alias) so the score logic stays reproducible.
MODEL = "claude-haiku-4-5-20251001"
# Cheap tier for high-volume triage. Cap description length to bound tokens.
# Bound, not filter: guards against a pathological payload, never decides
# which text the model may judge. Corpus: 283 rows, longest 53,423 chars.
# Was 2000, which sent 40% of the mean row and hid seniority gates past the
# cut (row 406: '3+ year' at 2386 -> 85 truncated vs 72 at full text).
MAX_DESC_CHARS = 60000

SYSTEM_PROMPT = (
    "You screen remote job postings for a Turkey-based data analyst seeking "
    "international remote roles: Data Analyst, BI, Reporting, Operations, or "
    "Product Analyst. Rate fit from 0 to 100, where 100 is an ideal remote "
    "data analyst role open to candidates in Turkey, and 0 is irrelevant or "
    "region-locked against Turkey. Be strict: tangential or vague roles score "
    "low.\n"
    # Seniority ranking (not a filter): a hard tenure/seniority gate the
    # candidate does not meet still stays applicable as a stretch, but must
    # not rank above a clean entry/mid role. The drafting layer bridges the
    # gap honestly downstream; scoring's job here is to order the pool.
    "If the posting carries a hard seniority or tenure gate (e.g. 5+ years, "
    "Senior/Lead/Principal) that an entry-level candidate does not meet, cap "
    "the score in the 78-83 band even when skill keywords overlap strongly -- "
    "eligible as a stretch, never a full match.\n"
    "Respond with ONLY a JSON object, no prose, no code fences:\n"
    '{"score": <integer 0-100>, "reason": "<one short sentence>"}'
)


def _build_user_message(title, company, location, description):
    desc = (description or "")[:MAX_DESC_CHARS]
    return (
        f"Title: {title or '-'}\n"
        f"Company: {company or '-'}\n"
        f"Location: {location or '-'}\n"
        f"Description: {desc}"
    )


def _parse_score(text):
    """Pull {'score': int, 'reason': str} out of the model's reply.

    Defensive: slice the outermost braces in case the model adds stray text.
    Fail loud if it still will not parse -- a silent bad score would poison
    everything downstream.
    """
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in reply: {text!r}")
    obj = json.loads(text[start:end + 1])
    score = max(0, min(100, int(obj["score"])))   # coerce + clamp to 0-100
    return {"score": score, "reason": str(obj["reason"]).strip()}


def _extract_text(resp):
    """Return the model's text output, tolerant of thinking blocks.

    Same doctrine as drafting._extract_text (Module 3): select the block
    by TYPE, never by position. Haiku 4.5 happens to put text at
    content[0] today, which is why the positional read never failed --
    but "works because the current model is shaped that way" is exactly
    the kind of accident that breaks silently on a repin. Ported
    2026-07-18 after cold review caught the lesson applied in one module
    and not its sibling.
    """
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise ValueError(f"no text block in reply: {resp.content!r}")


def score_job(client, title, company, location, description):
    """One Haiku call -> a clamped relevance score and its reason."""
    resp = client.messages.create(
        model=MODEL,
        max_tokens=200,
        temperature=0,                                # stable scoring
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": _build_user_message(title, company, location, description),
        }],
    )
    return _parse_score(_extract_text(resp))


def run_scoring(conn, client, limit=None):
    """Score prefiltered, not-yet-scored jobs; update rows in place."""
    sql = ("SELECT id, title, company, location, description FROM jobs "
           "WHERE prefilter_pass = 1 AND relevance_score IS NULL")
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()

    scored = 0
    for row in rows:
        result = score_job(
            client, row["title"], row["company"],
            row["location"], row["description"],
        )
        conn.execute(
            "UPDATE jobs SET relevance_score = ?, score_reason = ? WHERE id = ?",
            (result["score"], result["reason"], row["id"]),
        )
        conn.commit()                                 # checkpoint per row
        scored += 1
        print(f"  [{result['score']:3d}] {row['title']} -- {result['reason']}")
    return {"scored": scored}


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()                 # ANTHROPIC_API_KEY -> environment
    client = Anthropic()          # SDK reads the key from the environment

    with get_connection() as conn:
        summary = run_scoring(conn, client)   # first: one job only
        print(f"[scoring] scored {summary['scored']} job(s)")
