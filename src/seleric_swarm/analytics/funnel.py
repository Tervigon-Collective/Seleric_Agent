"""Funnel step conversion and drop-off — behind ``funnel_decomposition``.

Why this is simple arithmetic rather than a join
------------------------------------------------
``policy_config.FUNNEL_STEPS`` is deliberately restricted to metrics that
share one denominator. Verified in ``config/metric_registry.yaml:148-215``:
``pdp_view_rate``, ``atc_rate``, ``checkout_rate`` and ``purchase_cvr`` are all
``X_sessions / sessions``, and ``metric.sessions`` is ``count(sessions)``.

Because every rate is anchored on the same base, the survival rate between two
consecutive steps is just ``rate[i+1] / rate[i]`` — no re-fetching, no joining
two metrics on different date axes. That last point matters: the live
catalogue returns a ``CROSS_AXIS_RATIO_UNSUPPORTED`` warning for exactly that
kind of client-side division (``docs/features/business-state-service/
06_DATA_VALIDATION_FINDINGS.md``), which is why this module divides two
*already-session-anchored rates* rather than two raw counts.

``metric.sessions`` enters as an implicit rate of 1.0 — 100% of sessions are
sessions. Adding a metric with a different denominator would make every
conversion downstream of it meaningless, so ``ordered_steps`` refuses to place
anything not in ``FUNNEL_STEPS``.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any, NamedTuple

from seleric_swarm.toolsets import policy_config as policy

#: The base step is a count, not a rate; it is 100% of itself by definition.
_BASE_STEP = policy.FUNNEL_STEPS[0]


class StepReading(NamedTuple):
    metric_id: str
    value: float | None
    ref: Any = None


class Step(NamedTuple):
    metric_id: str
    position: int
    #: Session-anchored survival rate. 1.0 for the base step.
    rate: float
    #: The raw measured value, unchanged — the base step's is a session count,
    #: every other step's is already a ratio.
    raw: float
    ref: Any = None


class Transition(NamedTuple):
    from_step: str
    to_step: str
    #: Fraction of the previous step that reached this one.
    conversion: float
    #: 1 - conversion. The share lost at this stage.
    drop_off: float


def ordered_steps(readings: list[StepReading]) -> list[Step]:
    """Place readings onto the declared funnel, in order, dropping unknowns.

    Unrecognized metric ids are dropped rather than appended — a metric with a
    different denominator cannot be positioned on a session-anchored funnel,
    and guessing its position from its name is the heuristic this codebase
    deleted. The caller warns about what was dropped.
    """
    by_metric = {r.metric_id: r for r in readings if r.value is not None}
    out: list[Step] = []
    for position, metric_id in enumerate(policy.FUNNEL_STEPS):
        reading = by_metric.get(metric_id)
        if reading is None:
            continue
        raw = float(reading.value)  # type: ignore[arg-type]
        rate = 1.0 if metric_id == _BASE_STEP else raw
        out.append(
            Step(metric_id=metric_id, position=position, rate=rate, raw=raw, ref=reading.ref)
        )
    return out


def unknown_steps(readings: list[StepReading]) -> list[str]:
    """Metric ids that are not part of the declared funnel."""
    known = set(policy.FUNNEL_STEPS)
    return sorted({r.metric_id for r in readings if r.metric_id not in known})


def transitions(steps: list[Step]) -> list[Transition]:
    """Conversion and drop-off between each consecutive pair of present steps.

    "Consecutive" means consecutive among the steps actually supplied, not
    adjacent in ``FUNNEL_STEPS`` — a funnel measured at sessions → checkout
    with the middle steps missing still yields one honest transition, it just
    spans more of the funnel. ``from_step``/``to_step`` name which, so the
    reader is never misled about what was skipped.
    """
    out: list[Transition] = []
    for earlier, later in pairwise(steps):
        if earlier.rate == 0:
            # Nothing reached the earlier step, so the conversion out of it is
            # undefined rather than zero or infinite. Omit it.
            continue
        conversion = later.rate / earlier.rate
        out.append(
            Transition(
                from_step=earlier.metric_id,
                to_step=later.metric_id,
                conversion=conversion,
                drop_off=1.0 - conversion,
            )
        )
    return out


def worst_transition(items: list[Transition]) -> Transition | None:
    """The single largest drop-off, which is what a funnel question is usually
    actually asking. ``None`` when there is nothing to compare."""
    return max(items, key=lambda t: t.drop_off) if items else None
