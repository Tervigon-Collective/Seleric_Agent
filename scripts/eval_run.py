"""Run the golden eval suite (M0.4).

    uv run python scripts/eval_run.py --mode replay
    uv run python scripts/eval_run.py --mode live --compare eval/baselines/golden.json
    uv run python scripts/eval_run.py --mode record --suite golden

Exits non-zero when ``--compare`` finds a metric worse than the baseline, or
when a replay cassette is missing.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Seleric eval suite.")
    parser.add_argument("--mode", choices=["replay", "live", "record"], default="replay")
    parser.add_argument(
        "--cases",
        action="append",
        default=None,
        help="case file or directory (repeatable); default eval/golden",
    )
    parser.add_argument("--suite", default="golden", help="suite name (cassette + report key)")
    parser.add_argument("--cassette-dir", default="eval/cassettes")
    parser.add_argument("--compare", default=None, help="baseline JSON to compare against")
    parser.add_argument("--out", default="eval/reports", help="report output directory")
    parser.add_argument("--quiet", action="store_true", help="suppress per-case progress")
    return parser


def _prepare_env(mode: str) -> None:
    # Replay must not touch the network: a deterministic fake model and
    # in-memory persistence are the CI contract (recorded MCP responses serve
    # the rest). Record/live keep the environment the caller configured.
    if mode == "replay":
        os.environ.setdefault("APP_ENV", "test")
        os.environ.setdefault("LLM_PROVIDER", "fake")
        os.environ.setdefault("LANGFUSE_TRACING", "false")
        os.environ.setdefault("PERSISTENCE_BACKEND", "memory")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _prepare_env(args.mode)

    from seleric_swarm.evals import render_markdown, run_suite

    cases = args.cases or ["eval/golden"]
    progress = None if args.quiet else (lambda line: print(line, file=sys.stderr, flush=True))

    try:
        report = asyncio.run(
            run_suite(
                cases,
                args.mode,
                suite=args.suite,
                cassette_dir=Path(args.cassette_dir),
                baseline_path=args.compare,
                out_dir=args.out,
                progress=progress,
            )
        )
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(render_markdown(report))
    return 0 if report.comparison.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
