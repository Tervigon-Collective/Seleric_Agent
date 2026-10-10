"""The scope a question demands, as plain frozen types (no parsing here).

Split out of ``agent/scope.py`` so stage modules (``seleric_swarm.spec``) can build a
``RequiredScope`` without importing that module's legacy regex helpers (A1/A2). The
classes are re-exported from ``agent/scope.py`` unchanged, so existing imports keep working.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ValueFilter:
    """A word the user said that the data records as a value.

    ``dimensions`` — every dimension holding that exact value (the same word can
    live on several views, e.g. ``lt_utm_medium`` and ``utm_medium``); evidence
    filtered or grouped by any one of them covers it. ``values`` — the exact
    spellings plus same-dimension abbreviations to filter on."""

    term: str
    dimensions: frozenset[str]
    values: tuple[str, ...]


@dataclass(frozen=True)
class RequiredWindow:
    """One period the question demanded, as a closed date span.

    A *comparison* question names two of these. Live 2026-10-06
    (MS3-167d9f4838): "the last 3 days versus today" resolved to a single window
    — ``services/time_range.py`` returned on its first match and ``today`` was
    dropped — so the mission fetched, analysed and shipped only the 3-day half
    as ``completed``. ``RequiredScope`` had no field to hold the second window,
    so ``check_scope_coverage`` had nothing to reconcile and no gate could
    fire. This is that field.
    """

    start: date
    end: date

    def __str__(self) -> str:
        return self.start.isoformat() if self.start == self.end else f"{self.start}..{self.end}"


@dataclass(frozen=True)
class RequiredScope:
    """Hard constraints resolved to catalogue dimension ids.

    ``breakdowns`` — one **candidate set** per requested breakdown term. A grain
    word is often ambiguous in the catalogue ("channel" → ``channel`` /
    ``lt_channel`` / ``acquisition_channel``, all real dimensions), so a term
    resolves to the *set* of dimensions that share its grain language rather than
    one arbitrarily-picked id. Coverage is satisfied when the answer groups by
    **any** id in each set — the guard still catches "no grouping at all", but no
    longer forces a REVISE when the model chose a valid sibling dimension.
    ``value_filters`` — named values the answer must be filtered to.
    ``temporal_grain`` — the time grain the query requests (day/week/month/quarter/year).
       Evidence at a finer grain can satisfy a coarser grain request via aggregation.
    ``windows`` — the dated periods the query demands; **two entries for a
       comparison question**. Reconced against the evidence's own
       ``period_start``/``period_end`` in ``check_scope_coverage``.
    (Extension point, not yet populated: exclusion dims.)
    """

    breakdowns: frozenset[frozenset[str]] = frozenset()
    value_filters: tuple[ValueFilter, ...] = ()
    temporal_grain: str | None = None
    windows: tuple[RequiredWindow, ...] = ()
    # Concept axes the user's own words set (gateway catalogue_resolve_values "axes", e.g. date=finance
    # for "net profit on the P&L"): merged into every concept resolution and checked on the evidence.
    question_axes: tuple[tuple[str, str], ...] = ()
    # Dimensions a why-question names as where to look ("was it product mix or channel mix?"): hypotheses the
    # diagnosis segments first, never a table the answer must be grouped by.
    segment_hints: frozenset[str] = frozenset()
    # The question weighs a named value against the whole ("is Meta responsible for the drop"): the answer needs
    # the total and the part, so the value must appear in the evidence, not filter every figure.
    values_weighed_against_whole: bool = False

    def is_empty(self) -> bool:
        return not (
            self.breakdowns
            or self.value_filters
            or self.temporal_grain
            or self.windows
            or self.question_axes
        )

    def axis(self, name: str) -> str | None:
        return dict(self.question_axes).get(name)
