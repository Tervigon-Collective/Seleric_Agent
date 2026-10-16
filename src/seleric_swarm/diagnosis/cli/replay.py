"""``python -m seleric_swarm.diagnosis.cli replay <record.json>``: repeat a recorded investigation offline and say
whether it came out identical. Exit status: 0 identical, 1 diverged, 2 the record cannot be replayed."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from seleric_swarm.diagnosis.adapters import (
    legacy_engine_runner,  # noqa: F401  (registers the runner it replays)
)
from seleric_swarm.diagnosis.domain.errors import DiagnosisError
from seleric_swarm.diagnosis.domain.record import RunRecord
from seleric_swarm.diagnosis.trace.run import replay


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="diagnosis replay")
    parser.add_argument("record", type=Path, help="a run record JSON file")
    args = parser.parse_args(argv)
    try:
        record = RunRecord.from_json(args.record.read_text(encoding="utf-8"))
        result = asyncio.run(replay(record))
    except (OSError, ValueError, KeyError, DiagnosisError) as exc:
        print(f"cannot replay {args.record}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if result.identical:
        print(f"{record.investigation_id}: identical ({len(record.events)} events)")
        return 0
    print(f"{record.investigation_id}: DIVERGED\n{result.divergence}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
