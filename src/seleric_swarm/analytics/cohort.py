"""Cohort shaping — behind ``cohort_analysis``.

A hard constraint from the live catalogue, not a design choice
--------------------------------------------------------------
``docs/features/business-state-service/06_DATA_VALIDATION_FINDINGS.md`` records
that the customer-domain retention metrics have **no daily grain**:
``repeat_rate`` declares ``supported_dimensions: [brand_id]`` only, and a live
query carrying a ``time_range`` returned **one row for the whole window** —
the window acted as a filter on ``customer_ltv.last_order_at``, not as a
``GROUP BY`` axis. ``config/domain_health_profiles.yaml`` already classifies
that shape as ``feature_class: windowed_point``.

So this module must not assume a time series. A cohort is identified by a
*dimension value* (or by its period, when the evidence is a set of windowed
points), and each cohort contributes one measurement. Anything that iterated
days here would silently produce one-row "series" and call the result a
retention curve.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, NamedTuple


class CohortReading(NamedTuple):
    """One cohort's single measurement."""

    label: str
    value: float | None
    period_start: datetime | None = None
    ref: Any = None


class Cohort(NamedTuple):
    label: str
    value: float
    #: Difference from the cohort set's median, which is the comparison a
    #: cohort question is actually asking ("which cohort is unusual?").
    delta_from_median: float
    ref: Any = None


class CohortSpread(NamedTuple):
    cohorts: list[Cohort]
    median: float
    best: Cohort
    worst: Cohort

    @property
    def spread(self) -> float:
        """Best-to-worst gap. A near-zero spread means the cohorts are not
        actually differentiated, which is itself the finding."""
        return self.best.value - self.worst.value


def cohort_spread(readings: list[CohortReading]) -> CohortSpread | None:
    """Rank cohorts against their own median. ``None`` when fewer than two
    cohorts carry a value.

    Two is the floor, not one: a single cohort has nothing to be a cohort
    *analysis* of, and reporting it against its own median (a delta of exactly
    zero) would look like a result while saying nothing.

    Null-valued cohorts are dropped, never zero-filled — an unmeasured cohort
    is not a cohort that retained nobody.
    """
    measured = [r for r in readings if r.value is not None]
    if len(measured) < 2:
        return None

    import statistics

    values = [float(r.value) for r in measured]  # type: ignore[arg-type]
    median = statistics.median(values)

    cohorts = sorted(
        (
            Cohort(
                label=r.label,
                value=float(r.value),  # type: ignore[arg-type]
                delta_from_median=float(r.value) - median,  # type: ignore[arg-type]
                ref=r.ref,
            )
            for r in measured
        ),
        key=lambda c: c.value,
        reverse=True,
    )

    return CohortSpread(cohorts=cohorts, median=median, best=cohorts[0], worst=cohorts[-1])


def label_readings(
    readings: list[CohortReading],
) -> tuple[list[CohortReading], bool]:
    """Ensure every cohort has a distinguishing label.

    Returns ``(readings, labelled_by_period)``. When the evidence carries no
    dimension to cohort on, each artifact's period becomes the label — that is
    the windowed-point case above, where "cohorts" are successive measurement
    windows rather than customer segments. The flag lets the caller say which
    of the two it actually did, because they answer different questions.
    """
    if any(r.label for r in readings):
        return readings, False
    relabelled = [
        r._replace(
            label=r.period_start.date().isoformat() if r.period_start else f"cohort-{index}"
        )
        for index, r in enumerate(readings)
    ]
    return relabelled, True
