"""Golden eval-case schema and loader (M0.4).

A case is the *behavioural* unit the suite scores: a question (or a short
multi-turn exchange), the scope it runs at, and what a correct answer must
look like. Truth is expressed as ``metrics_query`` arguments (catalogue ids),
never SQL, so a case follows the catalogue instead of pinning a query plan.

This module has no dependency on the frozen pipeline contracts: a case's
``expect.spec`` is a *partial* mapping compared field-by-field against
whatever spec the run surfaces (see ``_scorer``). That keeps the loader usable
while M0.2 lands, and lets recorded cassettes stay plain JSON.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CASE_SCHEMA_VERSION = 1


class ValueTruth(BaseModel):
    """One number the answer must produce, and how to check it.

    ``truth`` is the ``metrics_query`` argument mapping (``metric_id`` plus any
    dimensions/window) whose value is the ground truth. The value itself is
    resolved by a truth resolver: recorded for replay, queried live for the
    Jenkins suite. ``value`` may be filled in ahead of time by a recording.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    truth: dict[str, Any] = Field(default_factory=dict)
    value: float | None = None
    tolerance: float = 0.0


class Expect(BaseModel):
    """What a correct run of the case must satisfy (all fields optional)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec: dict[str, Any] = Field(default_factory=dict)
    status: str | None = None
    values: tuple[ValueTruth, ...] = ()
    must_mention: tuple[str, ...] = ()
    forbid: tuple[str, ...] = ()


class EvalCase(BaseModel):
    """One golden case. ``turns`` are asked in order on one thread."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = CASE_SCHEMA_VERSION
    id: str
    as_of: str | None = None
    turns: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    expect: Expect = Field(default_factory=Expect)

    @model_validator(mode="after")
    def _require_turn(self) -> EvalCase:
        if not self.turns:
            raise ValueError(f"case {self.id!r} has no turns")
        return self


def _iter_case_dicts(source: Path) -> list[dict[str, Any]]:
    if source.is_dir():
        rows: list[dict[str, Any]] = []
        for path in sorted(source.glob("*.y*ml")):
            rows.extend(_iter_case_dicts(path))
        return rows

    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = raw.get("cases", [])
    if not isinstance(raw, list):
        raise TypeError(f"{source}: expected a list of cases or a 'cases:' mapping")
    for row in raw:
        if not isinstance(row, dict):
            raise TypeError(f"{source}: every case must be a mapping")
    return raw


def load_cases(sources: str | Path | Sequence[str | Path]) -> list[EvalCase]:
    """Load cases from a file, a directory, or a sequence of either.

    Cases are validated and their ids must be unique across the whole load —
    a duplicate id would silently overwrite a run in the report.
    """

    if isinstance(sources, (str, Path)):
        paths = [Path(sources)]
    else:
        paths = [Path(p) for p in sources]

    cases: list[EvalCase] = []
    seen: dict[str, Path] = {}
    for source in paths:
        for row in _iter_case_dicts(source):
            case = EvalCase.model_validate(row)
            if case.id in seen:
                raise ValueError(f"duplicate case id {case.id!r} in {source} and {seen[case.id]}")
            seen[case.id] = source
            cases.append(case)
    return cases
