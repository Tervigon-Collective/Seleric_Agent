"""Funnel step conversion and drop-off — behind ``funnel_decomposition``.

Why this holds no list of steps
-------------------------------
A funnel is whatever the caller measured, so naming its stages here would fix
in the harness a fact the catalogue owns — and would be wrong the moment a
funnel gains a stage, or the question is about a funnel other than the website
one. Membership and order are derived instead:

* the catalogue types each metric (``aggregation``); the count is the base
  stage, the shares of it are the steps,
* a share is bounded by its base, so a later stage is a subset of an earlier
  one and can never carry the larger rate — sorting the rates descending *is*
  the funnel order.

Because every step divides by that one base, the survival rate between two
consecutive stages is just ``rate[i+1] / rate[i]`` — no re-fetching, no joining
two metrics on different date axes. That last point matters: the live
catalogue returns a ``CROSS_AXIS_RATIO_UNSUPPORTED`` warning for exactly that
kind of client-side division (``docs/features/business-state-service/
06_DATA_VALIDATION_FINDINGS.md``), which is why this module divides two
*already-anchored rates* rather than two raw counts.

The base enters as an implicit rate of 1.0 — 100% of it reached itself. A
reading that merely shares the denominator without being a share of it is
reported rather than placed; see ``_classify``.
"""

from __future__ import annotations

from collections.abc import Callable
from itertools import pairwise
from typing import Any, NamedTuple

#: Is this metric id a ratio (a survival rate) rather than a count?
#:
#: Answered from the catalogue's own ``aggregation`` field, never from the id's
#: spelling: the caller passes a lookup backed by the live catalogue. ``None``
#: means the catalogue does not carry the metric, so it cannot be placed.
RatioLookup = Callable[[str], bool | None]


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


def _partition(
    readings: list[StepReading], is_ratio: RatioLookup
) -> tuple[StepReading | None, list[tuple[float, StepReading]], list[StepReading]]:
    """Split readings into the base stage, the shares of it, and the rest.

    A share cannot exceed 100% of its base, so a ratio measuring above 1.0 is
    something else that merely divides by the same denominator — an average
    depth, a cost per unit — and positioning it would invent a conversion above
    100%. A funnel also has exactly one base: where several counts are supplied
    the widest is it, and the others are no more placeable than an untyped id,
    because nothing says which stage they belong to. Everything not placed is
    returned so the caller can name it rather than drop it silently.
    """
    counts: list[StepReading] = []
    steps: list[tuple[float, StepReading]] = []
    unplaceable: list[StepReading] = []
    for r in readings:
        ratio = None if r.value is None else is_ratio(r.metric_id)
        if ratio is None:
            unplaceable.append(r)
        elif ratio is False:
            counts.append(r)
        elif 0.0 <= float(r.value) <= 1.0:
            steps.append((float(r.value), r))
        else:
            unplaceable.append(r)

    base: StepReading | None = None
    if counts:
        counts.sort(key=lambda r: float(r.value), reverse=True)  # type: ignore[arg-type]
        base, *surplus = counts
        unplaceable.extend(surplus)
    steps.sort(key=lambda pair: pair[0], reverse=True)
    return base, steps, unplaceable


def ordered_steps(readings: list[StepReading], is_ratio: RatioLookup) -> list[Step]:
    """Order the supplied readings into a funnel, widest stage first.

    There is no declared step list. A funnel is whatever the caller measured,
    and its order is a property of the numbers: every step is a share of one
    common base, so a later stage is a subset of an earlier one and its rate is
    never the larger. Sorting the rates descending therefore *is* the funnel
    order, and it stays correct when the funnel changes shape or when this runs
    over a funnel that is not the website one at all.

    The base stage is the reading the catalogue types as a count rather than a
    ratio: it is 100% of itself, so it enters at rate 1.0 and leads. A reading
    the catalogue does not carry cannot be typed, so it is left out rather than
    guessed at — ``unplaceable_steps`` names those for the caller's warnings.
    """
    base, placed, _ = _partition(readings, is_ratio)
    ordered: list[tuple[float, StepReading]] = (
        [(1.0, base), *placed] if base is not None else placed
    )
    return [
        Step(
            metric_id=r.metric_id,
            position=position,
            rate=rate,
            raw=float(r.value),  # type: ignore[arg-type]
            ref=r.ref,
        )
        for position, (rate, r) in enumerate(ordered)
    ]


def unplaceable_steps(readings: list[StepReading], is_ratio: RatioLookup) -> list[str]:
    """Readings that could not be positioned, for the caller's warnings.

    Either the catalogue does not carry the metric (so its kind is unknown), it
    divides by the base without being a share of it, or it is a second count
    competing to be the base stage.
    """
    _, _, unplaceable = _partition(readings, is_ratio)
    return sorted({r.metric_id for r in unplaceable})


def transitions(steps: list[Step]) -> list[Transition]:
    """Conversion and drop-off between each consecutive pair of present steps.

    "Consecutive" means consecutive among the steps actually supplied, not
    adjacent in the full funnel — a funnel measured at sessions → checkout
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
