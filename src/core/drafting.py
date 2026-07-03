"""Match analysis: Sonnet maps a posting's requirements to the real profile.

Chain link 1 of 2 in Module 3 (prompt chaining). This step produces a
structured, inspectable mapping -- what the candidate genuinely matches vs.
where the gaps are and how to bridge them honestly. Link 2 (cover letter)
consumes this object, so the letter is grounded in a reviewed analysis rather
than free-styling from the raw posting. Grounding is enforced in the prompt:
the model may use ONLY facts from the profile and must never invent tenure,
titles, or experience.
"""

import html
import json
import re

from anthropic import Anthropic

from src.core.storage import get_connection

# Pinned model ID so drafting logic stays reproducible. Sonnet (not Haiku):
# match quality drives letter quality, and honest gap-bridging without
# fabrication is exactly where the stronger model earns its cost.
MODEL = "claude-sonnet-5"

# Grounding doc. Run from repo root (python -m src.core.drafting).
PROFILE_PATH = "profile.md"

SYSTEM_PROMPT = (
    "You are a job-match analyst. You compare one job posting against a "
    "candidate profile and produce a structured mapping of how the "
    "candidate's REAL experience aligns with the posting's requirements.\n\n"
    "GROUNDING RULES (non-negotiable):\n"
    "- Use ONLY facts present in the candidate profile provided in the user "
    "message.\n"
    "- Never invent or imply employers, job titles, years of experience, "
    "dates, metrics, or technologies that are not in the profile.\n"
    "- If the profile does not satisfy a requirement, do NOT force a match. "
    "Put it in \"gaps\" with the closest real skill and an honest bridge.\n"
    "- A \"bridge\" explains how to frame transferable experience truthfully. "
    "It must NEVER instruct claiming tenure, titles, or experience the "
    "candidate lacks.\n\n"
    "TASK:\n"
    "1. Extract 3-6 of the posting's most important requirements. List "
    "hard/gate requirements (years, must-have skills) first.\n"
    "2. For each requirement the profile genuinely supports, add it to "
    "\"matches\" with concrete evidence taken from the profile, and a strength "
    "of \"strong\" | \"moderate\" | \"weak\".\n"
    "3. For each requirement the profile does not support, add it to \"gaps\" "
    "with \"closest_real_skill\" and an honest \"bridge\".\n"
    "4. Write a \"fit_summary\": 1-2 sentences, honest, naming any real gaps.\n\n"
    "OUTPUT:\n"
    "Return ONLY a single JSON object, no prose, no markdown, no code fences. "
    "Schema:\n"
    "{\n"
    '  "top_requirements": [string],\n'
    '  "matches": [{"requirement": string, "evidence": string, '
    '"strength": "strong"|"moderate"|"weak"}],\n'
    '  "gaps": [{"requirement": string, "closest_real_skill": string, '
    '"bridge": string}],\n'
    '  "fit_summary": string\n'
    "}"
)

COVER_LETTER_SYSTEM_PROMPT = (
    "You write concise, honest cover letters for a job candidate. You are "
    "given: (1) a match analysis mapping a specific posting's requirements to "
    "the candidate's real experience, (2) the candidate's profile, and (3) "
    "the role title and company. Write ONE cover letter for that role.\n\n"
    "GROUNDING RULES (non-negotiable):\n"
    "- Use ONLY facts present in the analysis and the profile. Never invent "
    "or imply employers, job titles, years of experience, dates, metrics, or "
    "technologies that are not there.\n"
    "- Never claim tenure, seniority, or experience the candidate lacks. "
    "Follow the \"bridge\" guidance in each gap: frame transferable "
    "experience truthfully, never as the missing credential.\n\n"
    "WRITING GUIDANCE:\n"
    "- Open with the candidate's strongest evidence: lead with a "
    "\"strong\"-strength match and, where possible, the flagship deployed "
    "project with a concrete scale metric. Hook in the first two sentences.\n"
    "- Address the role's most important requirements using the matches. "
    "Prefer \"strong\" evidence over \"moderate\".\n"
    "- Handle real gaps with honesty and brevity, and NEVER enumerate them. "
    "If the analysis lists several gaps, address at most the single most "
    "important one in ONE forward-looking sentence, then let it go -- do not "
    "list, stack, or apologize for the rest. A stretch role is carried by "
    "weight on strong evidence, not by cataloguing what is missing. Omit the "
    "gap entirely if naming it weakens the letter.\n"
    "- Confident, not inflated. Specific, evidence-led, no filler or cliches "
    "(\"passionate about\", \"team player\", \"hit the ground running\").\n\n"
    "FORMAT:\n"
    "- 150-220 words.\n"
    "- Greeting: address the company by name if given (\"Dear <Company> "
    "team,\"), else \"Dear Hiring Team,\". Never invent a person's name.\n"
    "- Plain professional prose, simple sign-off line.\n"
    "- Output ONLY the letter, starting at the greeting. No preamble, no "
    "notes, no markdown, no subject line."
)

_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_BLOCK_END = re.compile(r"</(p|h[1-6]|li|ul|ol|div)>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_BLANKS = re.compile(r"\n\s*\n+")


def _clean_html(raw):
    """Flatten posting HTML to plain text.

    Descriptions arrive as raw HTML (<p>, <h3>, <li>...). Turn block
    boundaries into newlines, drop remaining tags, unescape entities. Keeps
    requirements readable and avoids spending tokens on markup. No length cap
    here (unlike scoring): the requirements live inside the description and we
    need all of them.
    """
    if not raw:
        return ""
    text = _BR.sub("\n", raw)
    text = _BLOCK_END.sub("\n", text)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    text = _BLANKS.sub("\n\n", text)
    return text.strip()


def load_profile(path=PROFILE_PATH):
    """Read the grounding profile text."""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _build_user_message(title, company, location, description, profile_text):
    desc = _clean_html(description)
    return (
        "POSTING:\n"
        f"Title: {title or '-'}\n"
        f"Company: {company or '-'}\n"
        f"Location: {location or '-'}\n"
        f"Description:\n{desc}\n\n"
        "CANDIDATE PROFILE:\n"
        f"{profile_text}"
    )


def _parse_analysis(text):
    """Pull the analysis object out of the model's reply.

    Defensive: slice the outermost braces in case the model adds stray text,
    then verify the four expected keys. Fail loud -- a malformed analysis
    would silently poison the letter downstream.
    """
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in reply: {text!r}")
    obj = json.loads(text[start:end + 1])
    for key in ("top_requirements", "matches", "gaps", "fit_summary"):
        if key not in obj:
            raise ValueError(f"missing key {key!r} in analysis: {obj!r}")
    return obj


def _extract_text(resp):
    """Return the model's text output, tolerant of thinking blocks.

    Sonnet 5 emits a ThinkingBlock before the answer, so content[0] is not
    the text. Select by block type instead of position.
    """
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise ValueError(f"no text block in reply: {resp.content!r}")


def analyze_match(client, title, company, location, description, profile_text):
    """One Sonnet call -> structured match analysis (chain link 1 of 2)."""
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": _build_user_message(
                title, company, location, description, profile_text),
        }],
    )
    return _parse_analysis(_extract_text(resp))


def write_cover_letter(client, analysis, profile_text, title, company):
    """Chain link 2: turn the match analysis into a grounded cover letter.

    Consumes link 1's analysis object (not the raw posting) -- pure prompt
    chaining, so grounding flows through the reviewed analysis rather than the
    model re-reading the posting. Returns the letter text for human review;
    nothing is sent. max_tokens is a cap, not a charge, so keep it generous
    to leave room for extended thinking without truncating a short letter.
    """
    user_msg = (
        f"Role: {title or '-'}\n"
        f"Company: {company or '-'}\n\n"
        "MATCH ANALYSIS:\n"
        f"{json.dumps(analysis, ensure_ascii=False, indent=2)}\n\n"
        "CANDIDATE PROFILE:\n"
        f"{profile_text}"
    )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=COVER_LETTER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    letter = _extract_text(resp).strip()
    if not letter:                                    # fail loud, never draft empty
        raise ValueError("empty cover letter from model")
    return letter


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()                 # ANTHROPIC_API_KEY -> environment
    client = Anthropic()          # SDK reads the key from the environment
    profile = load_profile()

    # Smoke test: full two-link chain on one posting (Dwelly). No DB write --
    # storing the draft + status='drafted' comes once we approve the output.
    with get_connection() as conn:
        row = conn.execute(
            "SELECT title, company, location, description FROM jobs "
            "WHERE company = 'Dwelly' LIMIT 1"
        ).fetchone()

    analysis = analyze_match(
        client, row["title"], row["company"],
        row["location"], row["description"], profile,
    )
    letter = write_cover_letter(
        client, analysis, profile, row["title"], row["company"],
    )
    print(letter)
