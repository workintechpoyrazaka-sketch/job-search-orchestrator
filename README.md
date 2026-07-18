# Job-Search Orchestrator

A personal pipeline that collects remote job postings, scores them with an
LLM, drafts grounded cover letters, and records applications behind a
human-confirmation gate. One user, real data, live board.

**Live dashboard:** https://jobsearchorchestrator.streamlit.app — read-only, serving a committed public mirror
of the real database (283 postings, real companies, real audit trail).

The interesting part is not that an LLM scores jobs. It is what this repo
does about the fact that LLM outputs cannot be taken at their word:

- **`probes/truncation.py`** — an A/B probe proving that a context cap
  which silently filters text changes the model's verdict, and that the
  model's written justification is post-hoc narrative, not a mechanism
  trace: in one documented run the score was right while the stated
  reason cited a phrase that does not appear in the posting.
- **`probes/reason_audit.py`** — before the 34 scoring justifications
  were published, every strong textual claim in them was verified against
  the stored posting text. Result: 32/34 grounded. The two failures
  (unsupported eligibility claims about jobs 412 and 616) are published
  deliberately, as evidence of the failure mode. The audit's first
  version itself contained an ungrounded regex and was caught by its own
  expected-shape check — that fix is in the log too.
- **`job_events`** — an append-only audit trail with a state machine as
  sole producer. Historical gaps were repaired by a committed,
  precondition-gated, dry-run-first script (`seeds/repair_20260718.py`),
  not by untracked hand-edits: where recall disagreed with DB evidence,
  the evidence won.
- **`seeds/build_demo.py`** — the public mirror is built fail-closed:
  every column must be explicitly classified public or withheld
  (cover letters and private notes stay out), gates verify the trail's
  invariants, and only a passing build is atomically published.

## Pipeline

collect → prefilter → score → draft → apply

- **Collect** (`src/collect.py`): four adapters (Remotive, Himalayas,
  RemoteOK, Greenhouse ATS) behind one registry; dedup on
  `UNIQUE(source, external_id)`; existing rows are never silently
  rewritten.
- **Prefilter** (`src/core/prefilter.py`): deterministic, LLM-free triage
  — allowlist location eligibility and title relevance — so paid scoring
  only sees plausible matches (34 of 283 passed).
- **Score** (`src/core/scoring.py`): Claude Haiku rates 0–100 with a
  one-sentence reason; hard seniority gates cap scores into a stretch
  band instead of hiding the role.
- **Draft** (`src/core/drafting.py`): two-link prompt chain on Claude
  Sonnet — structured match analysis first, cover letter written from
  the analysis, grounding rules forbidding invented experience.
- **Apply** (`src/apply.py`): deliberately excluded from the automated
  run. A CLI gate surfaces eligibility facts, scans for red-flag
  phrases, and requires typed human confirmation; the state machine
  records the transition atomically with its event.

## Stack

Python, SQLite, Anthropic API (Haiku + Sonnet), Streamlit + Altair.

## Run it

```
pip install -r requirements.txt
cp profile.example.md profile.md      # fill with real data; gitignored
echo 'ANTHROPIC_API_KEY=...' > .env
python -m src.run                     # collect -> prefilter -> score -> draft
python -m src.apply <job_id>          # apply gate, one job, one human look
```

The dashboard runs against the committed public mirror without any keys:

```
pip install -r requirements-dashboard.txt
DB_PATH=data/demo.db streamlit run dashboard/app.py
```

## Roadmap

Replacing Streamlit Community Cloud with FastAPI + Postgres + React on
Railway — the apply path needs auth, which the current host cannot do.
Not yet built; this README describes what exists.
