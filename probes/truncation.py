"""A/B probe: a context cap that FILTERS silently changes the model's verdict.

WHAT THIS IS
    A demonstration, not evidence. The fixture below is CONSTRUCTED to exhibit
    the defect: a hard seniority requirement is deliberately placed past
    character 2000 of the description. It reproduces, on your machine, a defect
    originally measured against a real posting (row 406: 4983 chars, the string
    "3+ year" at offset 2386, cap 2000, score 85 -> 72). That measurement is the
    evidence and it lives in the body of commit 747ec3c. This file lets you
    watch the same thing happen instead of taking it on faith.

WHAT IT MEASURES
    scoring.MAX_DESC_CHARS is read at CALL TIME by _build_user_message
    (scoring.py:41), so it can be patched between two calls to score_job. Same
    client, same pinned model, same temperature=0, same fixture, same code path,
    same slice operation. ONE variable moves: the slice index.

    If the score changes, the cap was not bounding the payload. It was deciding
    what the model was allowed to read.

WHAT IT DOES NOT MEASURE
    Nothing is read from or written to any database. The fixture is inline, so
    this runs against a clean clone with no data/ directory. It also proves
    nothing about WHY the model scores as it does: score_reason is a witness,
    not a mechanism trace. The proof is the delta under one moved variable.

SECOND THING TO WATCH: THE MARKER TABLE AUDITS THE REASON
    The offsets are printed above the scores for a reason. Every marker at -1 is
    absent from the fixture entirely, so any reason that cites one is telling you
    about the model, not about the job. First run of this fixture: the model
    scored the full text 45 and explained that a "senior requirement" made it a
    stretch. `senior` is at -1. It never appears. The verdict was right and the
    named cause was invented -- in the same output that prints the disproof.

    This is the standing finding, reproduced yet again: the number is stable,
    the story is sampled. Read the reason as testimony to be checked against the
    offsets, never as a trace of what the model did.

COST
    Two Haiku calls (~1k input tokens each). Fractions of a cent. Requires
    ANTHROPIC_API_KEY in .env at the repo root.

RUN
    python -m probes.truncation        # from the repo root
"""

from dotenv import load_dotenv

load_dotenv(".env")  # explicit: documented to run from the repo root

from anthropic import Anthropic

from src.core import scoring

# The cap this probe demonstrates the failure of. Historical value, not the
# current one -- src.core.scoring.MAX_DESC_CHARS is now 60000 (a bound, not a
# filter). We patch the module global directly, so the live value is irrelevant
# to what runs here.
FILTERING_CAP = 2000
BOUNDING_CAP = 100_000

MARKERS = ["3+ year", "years of experience", "senior", "united kingdom", "uk only"]

FIXTURE_TITLE = "Data Analyst"
FIXTURE_COMPANY = "Northwind Analytics"
FIXTURE_LOCATION = "Remote (Worldwide)"

# CONSTRUCTED FIXTURE. The only load-bearing property is the offset of
# "3+ years of experience" -- it must land past FILTERING_CAP. Everything above
# it is realistic filler whose job is to push it there. Edit the prose above the
# requirement and you may drag the marker in-frame; _validate_fixture() below
# refuses to run if that happens, because a probe that measures nothing should
# fail loudly rather than print a clean null result.
FIXTURE_DESCRIPTION = """About Northwind Analytics

Northwind Analytics builds decision-support tooling for mid-market logistics
operators. We are a distributed team and we hire remotely. Our analysts sit
close to the operators they serve: you will not be handed a ticket queue and
asked to produce charts against a deadline someone else set.

The role

You will own a reporting surface end to end. That means talking to the people
who use it, deciding what the numbers should mean before deciding how to
compute them, and being the person who notices when a metric has quietly
stopped measuring what its name says it measures.

A typical month might include: rebuilding a dashboard that everyone opens and
nobody trusts, tracing a revenue discrepancy back through four transformation
steps to a timezone assumption, sitting in on customer calls to hear the
vocabulary operators actually use, and writing the short document that stops
the same question being asked a fifth time.

What the work actually looks like

Most of our analysts spend more time reading than querying. The data is not
clean and the schema is not documented, and the fastest route to a correct
answer is usually to find the person who wrote the pipeline and ask them what
they were worried about at the time. We would rather you shipped one number
you can defend than six you cannot.

We care a great deal about instrumentation. If you build a check, we will ask
whether that check could pass while the thing it checks is broken. This is not
a trick question and it is not rhetorical. It is most of the job.

Our stack

Postgres as the system of record, dbt for transformations, Python for anything
dbt should not be doing, and a BI layer we are actively unhappy with and would
welcome opinions about. You will not need to know all of this on day one. You
will need to be comfortable being the person who does not know, out loud, in
front of colleagues.

Interview process

A conversation, a take-home you are welcome to decline in favour of talking
through work you have already done, and a session where we look at something
broken together. No whiteboard algorithms. No take-home that takes a weekend.

What we are looking for

We are looking for someone with 3+ years of experience in an analytics role
where they owned outcomes rather than tickets. We are flexible about titles
and inflexible about that ownership: if your previous job was called Data
Analyst but you were handed specifications and asked to implement them, this
will be a difficult transition and we would rather say so now.

You should be fluent in SQL to the point of boredom. Window functions should
not be a lookup. You should be able to read a query someone else wrote and
form an opinion about what they misunderstood.

Nice to have

Experience with logistics or operations data. Familiarity with dbt testing
patterns. A public artifact of any kind - a repository, a write-up, a talk -
where you explain something you got wrong and what the evidence was.

Benefits

Remote-first, asynchronous by default, four-day fortnight in August, and a
hardware budget that assumes you know what you want.
"""

GATE_MARKER = "3+ years of experience"


def _validate_fixture() -> int:
    """Refuse to run unless the fixture can actually exhibit the defect.

    A probe whose fixture has drifted would send the requirement inside the
    frame at BOTH caps, return two identical scores, and read as evidence that
    the defect does not exist. That failure is silent and convincing, which is
    the only kind worth guarding against.
    """
    at = FIXTURE_DESCRIPTION.lower().find(GATE_MARKER)
    if at == -1:
        raise SystemExit(
            f"fixture invalid: {GATE_MARKER!r} is not in the description at all. "
            "This probe would compare two identical payloads and prove nothing."
        )
    if at <= FILTERING_CAP:
        raise SystemExit(
            f"fixture invalid: {GATE_MARKER!r} sits at offset {at}, which is "
            f"INSIDE the {FILTERING_CAP}-char frame. Both calls would send the "
            "requirement and the probe would report a false null. Lengthen the "
            "prose above the requirement."
        )
    if len(FIXTURE_DESCRIPTION) >= BOUNDING_CAP:
        raise SystemExit(
            f"fixture invalid: description is {len(FIXTURE_DESCRIPTION)} chars, "
            f"which the {BOUNDING_CAP} 'bounding' cap would also truncate."
        )
    return at


def main() -> None:
    gate_at = _validate_fixture()

    print(f"=== fixture: {FIXTURE_TITLE} @ {FIXTURE_COMPANY}")
    print(f"location   : {FIXTURE_LOCATION!r}")
    print(f"desc_len   : {len(FIXTURE_DESCRIPTION)}")
    print(f"gate at    : {gate_at}  (past the {FILTERING_CAP}-char cap by "
          f"{gate_at - FILTERING_CAP})")
    print(f"--- marker offsets (-1 = absent from the description entirely)")
    for m in MARKERS:
        at = FIXTURE_DESCRIPTION.lower().find(m)
        if at == -1:
            verdict = "n/a"
        elif at < FILTERING_CAP:
            verdict = f"IN FRAME @ {FILTERING_CAP}"
        else:
            verdict = f"PAST CAP @ {FILTERING_CAP}"
        print(f"  {m:<22} @ {at:<6} {verdict}")

    client = Anthropic()
    results = {}
    for cap in (FILTERING_CAP, BOUNDING_CAP):
        scoring.MAX_DESC_CHARS = cap  # read at call time by _build_user_message
        sent = len(FIXTURE_DESCRIPTION[:cap])
        result = scoring.score_job(
            client,
            FIXTURE_TITLE,
            FIXTURE_COMPANY,
            FIXTURE_LOCATION,
            FIXTURE_DESCRIPTION,
        )
        results[cap] = result["score"]
        pct = sent / len(FIXTURE_DESCRIPTION)
        print(f"\n=== cap={cap} -> {sent}/{len(FIXTURE_DESCRIPTION)} chars sent ({pct:.0%})")
        print(f"score : {result['score']}")
        print(f"reason: {result['reason']}")

    delta = results[BOUNDING_CAP] - results[FILTERING_CAP]
    print(f"\n=== delta: {results[FILTERING_CAP]} -> {results[BOUNDING_CAP]} ({delta:+d})")
    if delta == 0:
        print("No delta. The cap did not change the verdict on THIS fixture --")
        print("which is a result about this fixture, not about the defect.")
    else:
        print("One variable moved: the slice index. The gate did not misjudge")
        print("the requirement -- at cap=2000 it never received it.")


if __name__ == "__main__":
    main()
