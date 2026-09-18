"""Golden dataset loader — Sprint 1 scaffolding.

Seeds from ``eval/datasets/lookup_commerce.jsonl`` (existing swarm_v2 eval
fixture, same file format already used by ``eval/cli.py``). The
``tests/replay/`` fixtures are pytest test files with cases embedded inline
in Python, not a data file — extracting them into this same ``EvalCase``
shape is real work (reading each test, pulling out its query/expected
tuple) deferred to whichever sprint actually runs the harness against a
non-stub agent; listed here as a known gap, not silently skipped.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from seleric_swarm.paths import repo_root

DEFAULT_DATASET_PATHS: tuple[Path, ...] = (
    repo_root() / "eval" / "datasets" / "lookup_commerce.jsonl",
)


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    query: str
    scope: dict[str, object] = Field(default_factory=dict)
    expected: dict[str, object] = Field(default_factory=dict)


def load_golden_dataset(paths: tuple[Path, ...] = DEFAULT_DATASET_PATHS) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                cases.append(EvalCase.model_validate(json.loads(line)))
    return cases
