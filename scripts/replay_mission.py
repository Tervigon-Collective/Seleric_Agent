"""Replay a recorded mission's stages (M0.3).

Loads the redacted stage input/output JSON that :func:`stage_span` wrote to the
local trace store (``settings.trace_local_dir``) and prints it, so a failed
mission's understand input/output, plan, tool calls and validation verdict are
inspectable without Langfuse.

Re-running a stage (rather than just printing its record) needs the pipeline's
per-stage re-runners, which land with M1.2+; this script is the reader that
proves the trace was persisted. With M0.4's cassette MCP it will also re-execute
the stage against recorded MCP responses.

Usage:
    python scripts/replay_mission.py <mission_id> [--stage <name>] [--all]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from seleric_swarm.config.settings import get_settings
from seleric_swarm.observability._stages import StageIOStore


def _load_store() -> StageIOStore:
    return StageIOStore(Path(get_settings().trace_local_dir))


def replay_stage(store: StageIOStore, mission_id: str, stage: str) -> dict[str, Any] | None:
    record = store.load(mission_id, stage)
    if record is None:
        print(f"[mission {mission_id}] stage '{stage}' not found in {store._root}")
        return None
    print(f"\n=== stage: {record.get('stage')} (mission {record.get('mission_id')}) ===")
    if record.get("error"):
        print(f"  error: {record['error']}")
    if record.get("output_fingerprint"):
        print(f"  output_fingerprint: {record['output_fingerprint']}")
    if record.get("input") is not None:
        print(f"  input:  {json.dumps(record['input'])[:400]}")
    if record.get("output") is not None:
        print(f"  output: {json.dumps(record['output'])[:400]}")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mission_id", help="mission to replay")
    parser.add_argument("--stage", help="replay only this stage")
    parser.add_argument("--all", action="store_true", help="replay every recorded stage")
    args = parser.parse_args(argv)

    store = _load_store()
    if args.stage:
        replay_stage(store, args.mission_id, args.stage)
        return 0

    stages = store.stages(args.mission_id)
    if not stages:
        print(f"[mission {args.mission_id}] no recorded stages under {store._root}")
        return 1
    if not args.all:
        print(
            f"[mission {args.mission_id}] recorded stages: {', '.join(stages)}\n"
            "pass --all to print them all, or --stage <name> for one."
        )
        return 0
    for stage in stages:
        replay_stage(store, args.mission_id, stage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
