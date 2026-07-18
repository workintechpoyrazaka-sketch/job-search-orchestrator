"""Audit: check score_reason's strongest textual claims against the stored text.

WHY THIS EXISTS
    probes/truncation.py established that score_reason is testimony, not a
    mechanism trace: the number is stable, the story is sampled. Before
    publishing the reasons (2026-07-18), every reason making a strong,
    checkable claim about the posting text was verified against the stored
    description. This script is that verification, committed, so "32/34
    grounded" is reproducible rather than an assertion.

RESULT (2026-07-18, frozen as fixtures below)
    32/34 reasons grounded. Two failures, both fabricating eligibility IN
    THE CANDIDATE'S FAVOR:
      - job 412 (Tabby): "explicitly welcomes Turkey-based candidates" --
        neither 'turkey' nor 'worldwide' appears in the 6,066-char text.
      - job 616 (Uken): "worldwide hiring includes Turkey" -- unsupported
        by the text AND contradicted by ground truth: the application form
        required Canada work authorization (discovered at apply time).
    Both rows are published deliberately as evidence of the failure mode.

RUN
    python -m probes.reason_audit              # audits data/demo.db (public)
"""

import re
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "demo.db"

# (job_id, claim quoted from score_reason, pattern, expectation)
# expectation True  -> reason is grounded only if pattern appears in the text
# expectation False -> reason is grounded only if pattern is ABSENT
CHECKS = [
    (391, "MBI clearance required",              r"\bmbi\b",                 True),
    (391, "no explicit citizenship requirement", r"citizenship required:\s*no", True),
    (396, "explicitly excludes data/AI scientists", r"not computer vision or data/ai scientists", True),
    (962, "3+ years requirement",                r"3\+\s*years?",            True),
    (414, "explicit worldwide eligibility",      r"\bworldwide\b",           True),
    (397, "test drives required",                r"test drive",              True),
    (399, "generic talent pool application",     r"talent pool|talent community|future", True),
    # -- the two documented failures ------------------------------------------
    (412, "explicitly welcomes Turkey-based candidates", r"\bturkey\b|\bt\u00fcrkiye\b", True),
    (616, "worldwide hiring includes Turkey",    r"\bturkey\b|\bworldwide\b", True),
]


def main() -> None:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    passed = failed = 0
    for job_id, claim, pattern, expect_present in CHECKS:
        row = conn.execute(
            "SELECT company, description FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise SystemExit(f"job {job_id} missing from {DB}")
        company, desc = row[0], (row[1] or "").lower()
        found = bool(re.search(pattern, desc))
        ok = (found == expect_present)
        passed += ok
        failed += (not ok)
        verdict = "GROUNDED " if ok else "UNSUPPORTED"
        print(f"  [{verdict}] #{job_id} {company}: {claim!r}")
    print(f"\n{passed} grounded, {failed} unsupported "
          f"(expected: 2 unsupported -- jobs 412, 616; see docstring)")


if __name__ == "__main__":
    main()
