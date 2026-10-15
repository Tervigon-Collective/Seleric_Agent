"""Diagnosis eval case format (plan P0.1): load and validate; no engine imports.

A case is one JSON file under ``eval/diag/cases/``. It names a *world* (a versioned fixture of real brand
series, P0.2), an optional *injection* that plants a known cause into that world (P0.3), or an independent
*record* of a real incident (P0.5), and the *truth* the scorer compares the engine's answer with (P0.6).

The same format is what ``export-fixture`` writes (plan D-21 item 7), so a live failure becomes an eval case.

Evidence rule: a case may only carry truth that is planted or independently recorded. A real incident with no
known cause is ``kind: "stability"``: it is scored on stability and reconstruction, never on a cause.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

# What the world contains, which decides what a correct answer is.
KINDS = ("injection", "record", "null", "adversarial", "stability")
# findable: a cause is in the data and graph; hidden: a real cause exists but no evidence of it is available;
# measurement: the metric moved because the measurement broke; none: nothing real changed or the premise is false.
CAUSE_CLASSES = ("findable", "hidden", "measurement", "none")
# What a correct engine says. Maps to engine verdicts in the scorer, not here.
EXPECTED_OUTCOMES = ("cause_found", "located_not_identified", "measurement_artifact", "no_change", "premise_false")
DIRECTIONS = ("up", "down")
MODES = ("event", "period", "drift")

_CLASS_OUTCOMES = {
    "findable": {"cause_found"},
    "hidden": {"located_not_identified"},
    "measurement": {"measurement_artifact"},
    "none": {"no_change", "premise_false"},
}


class CaseError(ValueError):
    """A case file is malformed. The message names the case and the field."""


@dataclass(frozen=True)
class Request:
    outcome: str
    event_days: tuple[date, ...]
    baseline_days: tuple[date, ...] = ()  # period mode: same length as event_days
    claimed_direction: str | None = None
    scope: dict[str, str] = field(default_factory=dict)
    mode: str = "event"


@dataclass(frozen=True)
class World:
    fixture: str  # id of a fixture file (P0.2) under eval/diag/fixtures/
    sha256: str = ""  # fingerprint of the fixture the case was written against; the loader of a fixture checks it


@dataclass(frozen=True)
class Injection:
    """One planted change. ``type`` is a key of the injection library (P0.3); ``params`` are its arguments."""

    type: str
    params: dict[str, Any]
    onset: date | None = None


@dataclass(frozen=True)
class Cause:
    metric: str  # catalogue/graph id of the planted or recorded cause
    sign: str | None = None  # "up" | "down" | None when unknown
    onset: date | None = None
    share_of_change: float | None = None  # planted share of the outcome's change, 0..1; None when unknown


@dataclass(frozen=True)
class Decomposition:
    """Where the change sits, when that is the truth (an exact split, or a mix shift), as opposed to why it happened."""

    dimension: str
    top_segments: tuple[str, ...]  # the segment carrying the largest share of the change is one of these
    dominant: str | None = None  # "mix" | "rate": which effect dominates inside the dimension, when stated


@dataclass(frozen=True)
class Truth:
    cause_class: str
    expected: str
    causes: tuple[Cause, ...] = ()
    must_not_attribute: tuple[str, ...] = ()  # metrics the engine must not name as a supported cause
    unexplained_share: tuple[float, float] | None = None  # allowed range of the reported unexplained share
    note: str = ""  # why this is the truth (source of the record, what was planted)
    decomposition: Decomposition | None = None
    signal_z: float | None = None  # planted change of the outcome in robust sigmas of its own variability; None = not planted


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    question: str
    request: Request
    world: World
    truth: Truth
    brand: int = 20
    tags: tuple[str, ...] = ()
    injections: tuple[Injection, ...] = ()
    record: str = ""  # independent source for kind "record" (where the truth was confirmed)
    schema_version: int = SCHEMA_VERSION


def _date(case: str, where: str, raw: Any) -> date:
    try:
        return date.fromisoformat(str(raw))
    except ValueError as e:
        raise CaseError(f"{case}: {where}: not an ISO date: {raw!r}") from e


def _opt_date(case: str, where: str, raw: Any) -> date | None:
    return None if raw in (None, "") else _date(case, where, raw)


def _one_of(case: str, where: str, value: Any, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise CaseError(f"{case}: {where}: {value!r} not in {list(allowed)}")
    return str(value)


def _need(case: str, d: dict[str, Any], key: str, where: str = "") -> Any:
    if key not in d or d[key] in (None, ""):
        raise CaseError(f"{case}: missing {where}{key}")
    return d[key]


def _share(case: str, where: str, raw: Any) -> float | None:
    if raw is None:
        return None
    v = float(raw)
    if not 0.0 <= v <= 1.0:
        raise CaseError(f"{case}: {where}: {v} outside 0..1")
    return v


def parse_case(raw: dict[str, Any]) -> Case:
    cid = str(raw.get("id") or "?")
    if raw.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise CaseError(f"{cid}: schema_version {raw.get('schema_version')} != {SCHEMA_VERSION}")
    kind = _one_of(cid, "kind", _need(cid, raw, "kind"), KINDS)

    rq = _need(cid, raw, "request")
    event_days = tuple(_date(cid, "request.event_days", x) for x in _need(cid, rq, "event_days", "request."))
    baseline = tuple(_date(cid, "request.baseline_days", x) for x in rq.get("baseline_days") or ())
    mode = _one_of(cid, "request.mode", rq.get("mode", "period" if baseline else "event"), MODES)
    if mode == "period" and len(baseline) != len(event_days):
        raise CaseError(f"{cid}: period mode needs baseline_days of the same length as event_days")
    if baseline and set(baseline) & set(event_days):
        raise CaseError(f"{cid}: baseline_days overlap event_days")
    direction = rq.get("claimed_direction")
    if direction is not None:
        _one_of(cid, "request.claimed_direction", direction, DIRECTIONS)
    request = Request(str(_need(cid, rq, "outcome", "request.")), event_days, baseline, direction,
                      dict(rq.get("scope") or {}), mode)

    w = _need(cid, raw, "world")
    world = World(str(_need(cid, w, "fixture", "world.")), str(w.get("sha256") or ""))

    t = _need(cid, raw, "truth")
    cls = _one_of(cid, "truth.cause_class", _need(cid, t, "cause_class", "truth."), CAUSE_CLASSES)
    expected = _one_of(cid, "truth.expected", _need(cid, t, "expected", "truth."), EXPECTED_OUTCOMES)
    if expected not in _CLASS_OUTCOMES[cls]:
        raise CaseError(f"{cid}: cause_class {cls!r} cannot expect {expected!r}; allowed {sorted(_CLASS_OUTCOMES[cls])}")
    causes = tuple(
        Cause(str(_need(cid, c, "metric", "truth.causes[].")), c.get("sign"), _opt_date(cid, "truth.causes[].onset", c.get("onset")),
              _share(cid, "truth.causes[].share_of_change", c.get("share_of_change")))
        for c in t.get("causes") or ()
    )
    if cls == "findable" and not causes:
        raise CaseError(f"{cid}: a findable case must name at least one cause")
    if cls != "findable" and causes:
        raise CaseError(f"{cid}: only findable cases list causes; use must_not_attribute for {cls!r}")
    for c in causes:
        if c.sign is not None:
            _one_of(cid, "truth.causes[].sign", c.sign, DIRECTIONS)
    shares = [c.share_of_change for c in causes if c.share_of_change is not None]
    if sum(shares) > 1.0 + 1e-9:
        raise CaseError(f"{cid}: planted shares add up to {sum(shares):.3f} > 1")
    ur = t.get("unexplained_share")
    if ur is not None and not (len(ur) == 2 and 0.0 <= ur[0] <= ur[1] <= 1.0):
        raise CaseError(f"{cid}: truth.unexplained_share must be [lo, hi] within 0..1")
    dc = t.get("decomposition")
    decomposition = None
    if dc:
        dominant = dc.get("dominant")
        if dominant not in (None, "mix", "rate"):
            raise CaseError(f"{cid}: truth.decomposition.dominant must be mix or rate")
        raw_top = _need(cid, dc, "top_segment", "truth.decomposition.")
        decomposition = Decomposition(str(_need(cid, dc, "dimension", "truth.decomposition.")),
                                      tuple(str(x) for x in (raw_top if isinstance(raw_top, list) else [raw_top])), dominant)
    signal_z = t.get("signal_z")
    if signal_z is not None and float(signal_z) < 0:
        raise CaseError(f"{cid}: truth.signal_z must be >= 0")
    truth = Truth(cls, expected, causes, tuple(str(x) for x in t.get("must_not_attribute") or ()),
                  (float(ur[0]), float(ur[1])) if ur else None, str(t.get("note") or ""), decomposition,
                  float(signal_z) if signal_z is not None else None)

    injections = tuple(
        Injection(str(_need(cid, i, "type", "injections[].")), dict(i.get("params") or {}), _opt_date(cid, "injections[].onset", i.get("onset")))
        for i in raw.get("injections") or ()
    )
    record = str(raw.get("record") or "")
    if kind == "injection" and not injections:
        raise CaseError(f"{cid}: kind injection needs injections")
    if kind == "record" and not record:
        raise CaseError(f"{cid}: kind record needs record (the independent source of the truth)")
    if kind == "stability" and (causes or cls != "hidden"):
        raise CaseError(f"{cid}: a stability case has an unknown cause: cause_class hidden, no causes")
    if kind == "null" and cls != "none":
        raise CaseError(f"{cid}: a null case has cause_class none")
    return Case(cid, kind, str(_need(cid, raw, "question")), request, world, truth, int(raw.get("brand", 20)),
                tuple(str(x) for x in raw.get("tags") or ()), injections, record)


def load_case(path: Path) -> Case:
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise CaseError(f"{path.name}: invalid JSON: {e}") from e
    case = parse_case(raw)
    if case.id != path.stem:
        raise CaseError(f"{path.name}: id {case.id!r} must equal the file name")
    return case


def load_cases(root: Path) -> list[Case]:
    cases = [load_case(p) for p in sorted(root.glob("*.json"))]
    seen: set[str] = set()
    for c in cases:
        if c.id in seen:
            raise CaseError(f"duplicate case id {c.id}")
        seen.add(c.id)
    return cases
