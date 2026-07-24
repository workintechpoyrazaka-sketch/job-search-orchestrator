"""Single-command orchestrator: collect -> prefilter -> score -> draft.

Runs the four IDEMPOTENT stages in sequence, sharing one connection, one
Anthropic client, and one loaded profile. Any stage failure stops the run
hard (fail-fast): drafting on a partially-scored pool would waste Sonnet
tokens on the wrong jobs and hide the failure. Because every stage is
idempotent, a stopped run is safe to fix and re-run from the top.

`apply` is deliberately NOT wired in. It is the one stage with an irreversible
external side effect and a mandatory human forcing function (the eligibility
gate). Automating it would reintroduce the exact risk that gate exists to kill.
Applications go through `python -m src.apply <id>`, one job, one human look.

Usage:
    python -m src.run                 # full pipeline
    python -m src.run --skip-collect  # re-score/re-draft without re-hitting boards
    python -m src.run --draft-limit 1 # cap drafting (single-row smoke)
"""

import argparse
import os
import sys

from src.core.storage import get_connection


def _require_api_key() -> None:
    """Fail fast if the key is missing -- BEFORE collecting anything.

    A missing key should stop the run at second zero, not after we have
    collected 200 jobs and prefiltered them, only to die at the scoring call.
    """
    from dotenv import load_dotenv

    load_dotenv()  # ANTHROPIC_API_KEY -> environment
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set (checked .env and environment). "
            "Scoring and drafting cannot run without it."
        )


def run_all(skip_collect: bool = False, draft_limit: int | None = None,
            min_score: int | None = None) -> dict:
    """Assemble shared resources once, then run the four stages in order.

    Returns a combined totals dict. Raises on the first stage that fails --
    the caller (or _main) surfaces which stage died.
    """
    # Fail fast on a missing key before any work happens.
    _require_api_key()

    from anthropic import Anthropic
    from src.core.drafting import load_profile

    totals: dict = {}

    # Stage 1: collect. Manages its own connections; commits and closes before
    # returning, so the shared conn opened below reads its committed state.
    if skip_collect:
        print("[run] skipping collect (--skip-collect)")
    else:
        from src.collect import collect_many

        print("[run] === collect ===")
        totals["collect"] = collect_many()

    # Shared resources for the DB-bound stages.
    client = Anthropic()          # reads ANTHROPIC_API_KEY from environment
    profile = load_profile()

    from src.core.prefilter import run_prefilter
    from src.core.scoring import run_scoring
    from src.core.drafting import run_drafting

    conn = get_connection()
    try:
        print("[run] === prefilter ===")
        totals["prefilter"] = run_prefilter(conn)

        print("[run] === score ===")
        totals["score"] = run_scoring(conn, client)

        print("[run] === draft ===")
        totals["draft"] = run_drafting(conn, client, profile, limit=draft_limit,
                                       min_score=min_score)
    finally:
        conn.close()  # explicit close; stages commit their own writes

    print(f"[run] DONE: {totals}")
    return totals


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.run")
    parser.add_argument(
        "--skip-collect", action="store_true",
        help="skip the collect stage (re-score/re-draft existing jobs)",
    )
    parser.add_argument(
        "--draft-limit", type=int, default=None,
        help="cap the number of cover letters drafted (e.g. 1 for a smoke run)",
    )
    parser.add_argument(
        "--min-score", type=int, default=None,
        help="override the drafting score threshold (default: drafting.MIN_SCORE)",
    )
    args = parser.parse_args(argv[1:])

    try:
        run_all(skip_collect=args.skip_collect, draft_limit=args.draft_limit,
                min_score=args.min_score)
    except Exception as e:
        # Fail-fast: report where we are and stop. Re-run is safe (idempotent).
        print(f"[run] FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
