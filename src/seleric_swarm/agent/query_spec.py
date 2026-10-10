"""QuerySpec — decide once; execute and check against the same typed decision.

Today intent is re-guessed from the raw question in several places (time windows,
value filters, grain, breakdown fallbacks). This module is the single typed
decision those consumers should share:

    question → Candidates → Understand → QuerySpec → Validate → Execute → Check

``Candidates`` are retrieval hints only — nothing here becomes a filter on its
own. ``QuerySpec`` is compiled from the understand call plus those hints, then
validated in code (ids exist, filter values have volume, baseline precedes
event). Downstream stages consume the validated spec; they do not reparse what
the user typed.

Window slots hold *expressions* (last 3 days ending yesterday), not calendar
dates — code does the maths. Regex stays for machine formats (ISO dates,
fixtures), never for free-text intent.

Feature flag ``Settings.query_spec_mode``:
  - ``off``     — no QuerySpec work (legacy path only)
  - ``shadow``  — build + validate + log disagreements vs legacy; execute legacy
  - ``enforce`` — consumers read the validated spec (fail-open to legacy when empty)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from seleric_swarm.agent.plan import PlanShape
from seleric_swarm.agent.scope import RequiredWindow, ValueFilter
from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.services.time_range import (
    _last_complete_day,
    _month_range,
    _sub_months,
)

_log = logging.getLogger("seleric.agent.query_spec")

QuerySpecMode = Literal["off", "shadow", "enforce"]
WindowRole = Literal["event", "baseline", "context"]
WindowUnit = Literal["day", "week", "month", "quarter", "year"]
WindowEnding = Literal["yesterday", "today", "as_of", "before_event"]
WindowName = Literal[
    "",
    "yesterday",
    "today",
    "this_week",
    "last_week",
    "this_month",
    "last_month",
    "this_year",
    "last_year",
]


# ---------------------------------------------------------------------------
# Slots the understand call may fill (expressions, not dates)
# ---------------------------------------------------------------------------


class WindowExpr(BaseModel):
    """A date expression. Code turns this into calendar dates against as_of."""

    name: WindowName = Field(
        default="",
        description=(
            "Named period when the user says yesterday/today/this|last week|month|year; "
            "empty when using unit+n or absolute ISO dates."
        ),
    )
    unit: WindowUnit | Literal[""] = Field(
        default="",
        description="Unit for a last-N window (last 3 days → day); empty when name or ISO is set.",
    )
    n: int | None = Field(
        default=None,
        description="Count for last-N (last 3 days → 3); for baseline before_event, how many units before the event.",
    )
    ending: WindowEnding = Field(
        default="yesterday",
        description=(
            "Where a last-N window ends: yesterday (complete days), today/as_of (include today), "
            "before_event (baseline immediately before the event window)."
        ),
    )
    start_iso: str = Field(default="", description="Absolute start YYYY-MM-DD when the user names calendar dates.")
    end_iso: str = Field(default="", description="Absolute end YYYY-MM-DD; same as start_iso for a single day.")


class WindowSlot(BaseModel):
    role: WindowRole = Field(
        description="event = the period asked about; baseline = what to compare against; context = surrounding span."
    )
    expr: WindowExpr = Field(default_factory=WindowExpr)
    source_span: str = Field(
        default="",
        description="The words in the message this window came from, verbatim when possible.",
    )


# ---------------------------------------------------------------------------
# Candidates (retrieval only) and the typed decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValueCandidate:
    """A catalogue value hit for a span of the question. Never a filter by itself."""

    term: str
    dimension: str
    value: str
    volume: int
    match: str
    source_span: str
    catalogue_vocabulary: bool = False


@dataclass(frozen=True)
class Candidates:
    values: tuple[ValueCandidate, ...] = ()
    axes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SpecFilter:
    term: str
    dimension: str
    values: tuple[str, ...]
    source_span: str = ""
    volume: int = 0


@dataclass(frozen=True)
class EntityRef:
    dimension: str
    value: str
    source_span: str = ""


@dataclass(frozen=True)
class ResolvedWindow:
    role: WindowRole
    start: date
    end: date
    source_span: str = ""
    token: str = ""

    def as_required(self) -> RequiredWindow:
        return RequiredWindow(start=self.start, end=self.end)


@dataclass(frozen=True)
class QuerySpec:
    """The one structured decision for a turn. Downstream must not reparse the question."""

    shape: PlanShape = "other"
    kind: str = "analysis"
    metrics: tuple[str, ...] = ()  # catalogue ids preferred; phrases ok until plan resolves
    metric_phrases: tuple[str, ...] = ()
    filters: tuple[SpecFilter, ...] = ()
    entities: tuple[EntityRef, ...] = ()
    entity_dimension: str = ""
    breakdowns: tuple[str, ...] = ()
    windows: tuple[ResolvedWindow, ...] = ()
    grain: str | None = None
    direction: str = "either"
    part_of_whole: bool = False
    follows_prior: bool = False
    names_period: bool = False
    question_axes: tuple[tuple[str, str], ...] = ()
    ordinary_words: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    valid: bool = True
    errors: tuple[str, ...] = ()

    def event_window(self) -> ResolvedWindow | None:
        return next((w for w in self.windows if w.role == "event"), None)

    def baseline_window(self) -> ResolvedWindow | None:
        return next((w for w in self.windows if w.role == "baseline"), None)

    def context_windows(self) -> tuple[ResolvedWindow, ...]:
        return tuple(w for w in self.windows if w.role == "context")

    def to_time_range(self) -> TimeRangeV1 | None:
        """Legacy ``TimeRangeV1`` view for consumers not yet on QuerySpec windows."""
        event = self.event_window()
        if event is None:
            # A lone context window still pins the period (lookup over last 3 days).
            lone = self.windows[0] if self.windows else None
            if lone is None:
                return None
            return TimeRangeV1(
                kind="absolute",
                start=lone.start.isoformat(),
                end=lone.end.isoformat(),
                relative_token=lone.token or None,
            )
        baseline = self.baseline_window()
        if baseline is not None:
            return TimeRangeV1(
                kind="comparison",
                start=event.start.isoformat(),
                end=event.end.isoformat(),
                start_b=baseline.start.isoformat(),
                end_b=baseline.end.isoformat(),
                relative_token=(
                    f"{event.token or event.start.isoformat()} vs "
                    f"{baseline.token or baseline.start.isoformat()}"
                ),
            )
        return TimeRangeV1(
            kind="absolute",
            start=event.start.isoformat(),
            end=event.end.isoformat(),
            relative_token=event.token or None,
        )

    def required_windows(self) -> tuple[RequiredWindow, ...]:
        seen: list[RequiredWindow] = []
        for role in ("event", "baseline", "context"):
            for w in self.windows:
                if w.role != role:
                    continue
                rw = w.as_required()
                if rw not in seen:
                    seen.append(rw)
        return tuple(seen)

    def value_filters(self) -> tuple[ValueFilter, ...]:
        """``RequiredScope``-shaped filters (entities are not filters)."""
        by_term: dict[str, list[SpecFilter]] = {}
        for f in self.filters:
            by_term.setdefault(f.term.lower(), []).append(f)
        out: list[ValueFilter] = []
        for term, group in by_term.items():
            dims = frozenset(f.dimension for f in group)
            values = tuple(dict.fromkeys(v for f in group for v in f.values))
            out.append(ValueFilter(term=group[0].term, dimensions=dims, values=values))
        return tuple(out)

    def dump(self) -> dict[str, Any]:
        """JSON-friendly snapshot for turn records and shadow logs."""
        return {
            "shape": self.shape,
            "kind": self.kind,
            "metrics": list(self.metrics),
            "metric_phrases": list(self.metric_phrases),
            "filters": [
                {"term": f.term, "dimension": f.dimension, "values": list(f.values), "volume": f.volume}
                for f in self.filters
            ],
            "entities": [
                {"dimension": e.dimension, "value": e.value, "source_span": e.source_span}
                for e in self.entities
            ],
            "entity_dimension": self.entity_dimension,
            "breakdowns": list(self.breakdowns),
            "windows": [
                {
                    "role": w.role,
                    "start": w.start.isoformat(),
                    "end": w.end.isoformat(),
                    "token": w.token,
                    "source_span": w.source_span,
                }
                for w in self.windows
            ],
            "grain": self.grain,
            "direction": self.direction,
            "part_of_whole": self.part_of_whole,
            "follows_prior": self.follows_prior,
            "names_period": self.names_period,
            "question_axes": [list(a) for a in self.question_axes],
            "ordinary_words": list(self.ordinary_words),
            "assumptions": list(self.assumptions),
            "valid": self.valid,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class AnswerClaim:
    """Structured numeric claim the answer asserts — audited by evidence id, not prose regex.

    Migration step 4: the answer model returns these alongside prose; the claims
    table / evidence lookup replaces total-mismatch regex over free text.
    """

    evidence_id: str
    value: float
    label: str = ""
    metric_id: str = ""


def claims_cover_spec(
    claims: tuple[AnswerClaim, ...] | list[AnswerClaim],
    spec: QuerySpec,
) -> tuple[bool, tuple[str, ...]]:
    """Did the claims cover the spec's metrics? (Windows/filters are evidence-side.)"""
    if not spec.metrics and not spec.metric_phrases:
        return True, ()
    claimed = {c.metric_id for c in claims if c.metric_id}
    missing = tuple(m for m in spec.metrics if m and m not in claimed)
    return (not missing), missing


@dataclass(frozen=True)
class ShadowDisagreement:
    field: str
    legacy: Any
    spec: Any
    detail: str = ""


@dataclass
class SpecOutcome:
    candidates: Candidates = field(default_factory=Candidates)
    spec: QuerySpec | None = None
    shadow: tuple[ShadowDisagreement, ...] = ()
    stats: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Candidates from catalogue_resolve_values
# ---------------------------------------------------------------------------


def candidates_from_resolution(resolution: dict[str, Any] | None) -> Candidates:
    """Wrap gateway value hits. Whole multi-token names stay one candidate each."""
    values: list[ValueCandidate] = []
    for term in (resolution or {}).get("terms") or []:
        span = str(term.get("term") or "")
        vocab = bool(term.get("catalogue_vocabulary"))
        best = str(term.get("best_match") or "")
        for d in term.get("dimensions") or []:
            dim = str(d.get("dimension") or "")
            if not dim:
                continue
            for v in d.get("values") or []:
                values.append(
                    ValueCandidate(
                        term=span,
                        dimension=dim,
                        value=str(v.get("value") or ""),
                        volume=int(v.get("volume") or 0),
                        match=str(v.get("match") or best or ""),
                        source_span=span,
                        catalogue_vocabulary=vocab,
                    )
                )
    axes = tuple(
        sorted((str(k), str(v)) for k, v in ((resolution or {}).get("axes") or {}).items() if k and v)
    )
    return Candidates(values=tuple(values), axes=axes)


# ---------------------------------------------------------------------------
# Window expression → calendar dates (no question-text regex)
# ---------------------------------------------------------------------------


def _end_day(anchor: date, ending: WindowEnding) -> date:
    if ending == "today" or ending == "as_of":
        return anchor
    # yesterday / before_event (before_event resolved relative to event elsewhere)
    return date.fromisoformat(_last_complete_day(anchor))


def resolve_window_expr(
    expr: WindowExpr,
    *,
    as_of: date,
    event_start: date | None = None,
) -> tuple[date, date, str]:
    """Return (start, end, token). Raises ValueError when the expression is empty/invalid."""
    if expr.start_iso:
        start = date.fromisoformat(expr.start_iso[:10])
        end = date.fromisoformat((expr.end_iso or expr.start_iso)[:10])
        if end < start:
            start, end = end, start
        return start, end, "absolute"

    name = (expr.name or "").strip().lower()
    if name == "yesterday":
        day = as_of - timedelta(days=1)
        return day, day, "yesterday"
    if name == "today":
        return as_of, as_of, "today"
    if name == "this_week":
        start = as_of - timedelta(days=as_of.weekday())
        return start, as_of, "this_week"
    if name == "last_week":
        this_monday = as_of - timedelta(days=as_of.weekday())
        start = this_monday - timedelta(weeks=1)
        end = this_monday - timedelta(days=1)
        return start, end, "last_week"
    if name == "this_month":
        return as_of.replace(day=1), as_of, "this_month"
    if name == "last_month":
        prev = _sub_months(as_of, 1)
        start_s, end_s = _month_range(prev.year, prev.month)
        return date.fromisoformat(start_s), date.fromisoformat(end_s), "last_month"
    if name == "this_year":
        return as_of.replace(month=1, day=1), as_of, "this_year"
    if name == "last_year":
        y = as_of.year - 1
        return date(y, 1, 1), date(y, 12, 31), "last_year"

    unit = (expr.unit or "").strip().lower()
    n = int(expr.n or 0)
    if unit and n > 0:
        if expr.ending == "before_event":
            if event_start is None:
                raise ValueError("before_event baseline needs a resolved event window")
            end = event_start - timedelta(days=1)
            if unit == "day":
                start = end - timedelta(days=n - 1)
                return start, end, f"prior_{n}d"
            if unit == "week":
                start = end - timedelta(weeks=n) + timedelta(days=1)
                return start, end, f"prior_{n}w"
            if unit == "month":
                start = _sub_months(end + timedelta(days=1), n)
                return start, end, f"prior_{n}m"
            if unit == "quarter":
                start = _sub_months(end + timedelta(days=1), n * 3)
                return start, end, f"prior_{n}q"
            raise ValueError(f"unsupported baseline unit: {unit}")
        end = _end_day(as_of, expr.ending)
        if unit == "day":
            start = end - timedelta(days=n - 1)
            return start, end, f"last_{n}d"
        if unit == "week":
            start = end - timedelta(weeks=n) + timedelta(days=1)
            return start, end, f"last_{n}w"
        if unit == "month":
            start = _sub_months(end + timedelta(days=1), n)
            return start, end, f"last_{n}m"
        if unit == "quarter":
            start = _sub_months(end + timedelta(days=1), n * 3)
            return start, end, f"last_{n}q"
        if unit == "year":
            start = _sub_months(end + timedelta(days=1), n * 12)
            return start, end, f"last_{n}y"
        raise ValueError(f"unsupported window unit: {unit}")

    raise ValueError("empty window expression")


def resolve_window_slots(
    slots: list[WindowSlot] | tuple[WindowSlot, ...],
    *,
    as_of: date,
) -> tuple[ResolvedWindow, ...]:
    """Resolve all slots. Event first so baselines can use ``before_event``."""
    ordered = sorted(slots or (), key=lambda s: {"event": 0, "context": 1, "baseline": 2}.get(s.role, 9))
    event_start: date | None = None
    out: list[ResolvedWindow] = []
    for slot in ordered:
        try:
            start, end, token = resolve_window_expr(
                slot.expr, as_of=as_of, event_start=event_start
            )
        except ValueError as exc:
            _log.info("window_slot_skipped role=%s err=%s", slot.role, exc)
            continue
        if slot.role == "event":
            event_start = start
        out.append(
            ResolvedWindow(
                role=slot.role, start=start, end=end, source_span=slot.source_span, token=token
            )
        )
    # Stable role order for consumers: event, baseline, context
    role_order = {"event": 0, "baseline": 1, "context": 2}
    out.sort(key=lambda w: role_order.get(w.role, 9))
    return tuple(out)


# ---------------------------------------------------------------------------
# Compile + validate
# ---------------------------------------------------------------------------


def _metric_phrases(understanding: Any) -> tuple[str, ...]:
    phrases: list[str] = []
    for slot in getattr(understanding, "metrics", None) or ():
        words = (getattr(slot, "words", None) or "").strip()
        mid = (getattr(slot, "metric_id", None) or "").strip()
        if words:
            phrases.append(words)
        elif mid:
            phrases.append(mid)
    rank = getattr(understanding, "rank_by", None)
    if rank is not None:
        words = (getattr(rank, "words", None) or "").strip()
        if words and words not in phrases:
            phrases.append(words)
    return tuple(phrases)


def _metric_ids(understanding: Any) -> tuple[str, ...]:
    ids: list[str] = []
    for slot in getattr(understanding, "metrics", None) or ():
        mid = (getattr(slot, "metric_id", None) or "").strip()
        if mid and mid not in ids:
            ids.append(mid)
    return tuple(ids)


def _pick_entities_and_filters(
    *,
    candidates: Candidates,
    understanding: Any,
    ordinary: frozenset[str],
) -> tuple[tuple[EntityRef, ...], tuple[SpecFilter, ...]]:
    """Entities for entity_comparison; filters for everything else.

    Exact whole-value matches with volume > 0 only. Volume-0 hits (e.g. a
    fragment like ``Adv+`` that is also a live value with no rows) stay
    candidates and never become filters — validation would drop them anyway.
    """
    entity_dim = (getattr(understanding, "entity_dimension", None) or "").strip()
    shape = getattr(understanding, "shape", None) or "other"
    entities: list[EntityRef] = []
    filters: list[SpecFilter] = []
    seen_entity: set[tuple[str, str]] = set()
    seen_filter: set[tuple[str, str, str]] = set()

    # Prefer exact matches; keep the highest-volume hit per (term, dimension).
    best: dict[tuple[str, str], ValueCandidate] = {}
    for c in candidates.values:
        if c.catalogue_vocabulary or c.match not in ("exact", "abbreviation"):
            continue
        if c.term.strip().lower() in ordinary:
            continue
        key = (c.term.strip().lower(), c.dimension)
        prev = best.get(key)
        if prev is None or c.volume > prev.volume:
            best[key] = c

    for c in best.values():
        if c.volume <= 0:
            continue
        if shape == "entity_comparison" and entity_dim and c.dimension == entity_dim:
            key = (c.dimension, c.value)
            if key not in seen_entity:
                seen_entity.add(key)
                entities.append(
                    EntityRef(dimension=c.dimension, value=c.value, source_span=c.source_span)
                )
            continue
        # Same term on several dims → one SpecFilter per dim (RequiredScope merges).
        fkey = (c.term.lower(), c.dimension, c.value)
        if fkey in seen_filter:
            continue
        seen_filter.add(fkey)
        filters.append(
            SpecFilter(
                term=c.term,
                dimension=c.dimension,
                values=(c.value,),
                source_span=c.source_span,
                volume=c.volume,
            )
        )
    return tuple(entities), tuple(filters)


def compile_query_spec(
    understanding: Any | None,
    *,
    candidates: Candidates | None = None,
    as_of: date,
    catalogue: Any | None = None,
) -> QuerySpec:
    """Build a QuerySpec from the understand call + candidates. No question-text scan."""
    if understanding is None:
        return QuerySpec(valid=False, errors=("no_understanding",))

    candidates = candidates or Candidates()
    ordinary = frozenset(
        w.strip().lower() for w in (getattr(understanding, "ordinary_words", None) or []) if w
    )
    entities, filters = _pick_entities_and_filters(
        candidates=candidates, understanding=understanding, ordinary=ordinary
    )

    raw_windows = list(getattr(understanding, "windows", None) or [])
    resolved = resolve_window_slots(raw_windows, as_of=as_of)

    grain = getattr(understanding, "grain", None) or "none"
    if grain == "none":
        grain = None

    breakdowns = tuple(
        d for d in (getattr(understanding, "breakdown_dimensions", None) or []) if d
    )
    # Drop time dimensions from breakdowns when the catalogue can say so.
    if catalogue is not None and callable(getattr(catalogue, "is_time_dimension", None)):
        breakdowns = tuple(d for d in breakdowns if not catalogue.is_time_dimension(d))

    return QuerySpec(
        shape=getattr(understanding, "shape", None) or "other",
        kind=getattr(understanding, "kind", None) or "analysis",
        metrics=_metric_ids(understanding),
        metric_phrases=_metric_phrases(understanding),
        filters=filters,
        entities=entities,
        entity_dimension=(getattr(understanding, "entity_dimension", None) or "").strip(),
        breakdowns=breakdowns,
        windows=resolved,
        grain=grain,
        direction=getattr(understanding, "direction", None) or "either",
        part_of_whole=bool(getattr(understanding, "part_of_whole", False)),
        follows_prior=bool(
            getattr(understanding, "follows_prior", False)
            or getattr(understanding, "accepts_offer", False)
        ),
        names_period=bool(getattr(understanding, "names_period", False)),
        question_axes=candidates.axes,
        ordinary_words=tuple(sorted(ordinary)),
    )


def validate_query_spec(
    spec: QuerySpec,
    *,
    catalogue: Any | None = None,
    dimension_ids: frozenset[str] | set[str] | None = None,
) -> QuerySpec:
    """Reject impossible filters / windows / ids. Returns a (possibly narrowed) copy."""
    errors: list[str] = []
    assumptions: list[str] = list(spec.assumptions)
    filters = list(spec.filters)
    entities = list(spec.entities)
    metrics = list(spec.metrics)
    breakdowns = list(spec.breakdowns)
    windows = list(spec.windows)

    # Volume-0 filters (Adv+ style fragments) — drop with an assumption.
    kept_filters: list[SpecFilter] = []
    for f in filters:
        if f.volume <= 0:
            assumptions.append(f"dropped filter {f.term!r} on {f.dimension}: volume 0")
            continue
        kept_filters.append(f)
    filters = kept_filters

    dims = frozenset(dimension_ids or ())
    if catalogue is not None:
        known_metrics = frozenset(getattr(catalogue, "metric_ids", lambda: ())() or ())
        if known_metrics:
            bad = [m for m in metrics if m and m not in known_metrics and f"metric.{m}" not in known_metrics]
            for m in bad:
                errors.append(f"unknown metric id: {m}")
            metrics = [m for m in metrics if m not in bad]
        if not dims and callable(getattr(catalogue, "dimension_ids", None)):
            try:
                dims = frozenset(catalogue.dimension_ids())
            except TypeError:
                dims = frozenset(getattr(catalogue, "dimension_ids", ()) or ())

    if dims:
        bad_b = [d for d in breakdowns if d not in dims]
        for d in bad_b:
            errors.append(f"unknown breakdown dimension: {d}")
        breakdowns = [d for d in breakdowns if d in dims]
        if spec.entity_dimension and spec.entity_dimension not in dims:
            errors.append(f"unknown entity_dimension: {spec.entity_dimension}")

    event = next((w for w in windows if w.role == "event"), None)
    baseline = next((w for w in windows if w.role == "baseline"), None)
    if event and baseline and baseline.end >= event.start:
        errors.append(
            f"baseline {baseline.start}..{baseline.end} must end before event {event.start}..{event.end}"
        )

    valid = not errors
    return replace(
        spec,
        filters=tuple(filters),
        entities=tuple(entities),
        metrics=tuple(metrics),
        breakdowns=tuple(breakdowns),
        windows=tuple(windows),
        assumptions=tuple(assumptions),
        valid=valid and spec.valid,
        errors=tuple([*spec.errors, *errors]),
    )


# ---------------------------------------------------------------------------
# Shadow comparison vs legacy regex / resolution paths
# ---------------------------------------------------------------------------


def legacy_windows_snapshot(window: TimeRangeV1 | None) -> list[dict[str, str]]:
    if window is None or not window.start or not window.end:
        return []
    out = [{"role": "event", "start": window.start[:10], "end": window.end[:10]}]
    if window.kind == "comparison" and window.start_b and window.end_b:
        out.append({"role": "baseline", "start": window.start_b[:10], "end": window.end_b[:10]})
    return out


def shadow_compare(
    spec: QuerySpec,
    *,
    legacy_window: TimeRangeV1 | None,
    legacy_grain: str | None,
    legacy_breakdowns: frozenset[frozenset[str]] | None = None,
    legacy_filter_terms: frozenset[str] | None = None,
) -> tuple[ShadowDisagreement, ...]:
    """Fields where the typed spec and the legacy path disagree."""
    disagreements: list[ShadowDisagreement] = []

    spec_wins = [
        {"role": w.role, "start": w.start.isoformat(), "end": w.end.isoformat()}
        for w in spec.windows
        if w.role in ("event", "baseline")
    ]
    legacy_wins = legacy_windows_snapshot(legacy_window)
    # Compare by dated spans ignoring role labels when counts differ (context-only specs).
    spec_spans = sorted((w["start"], w["end"]) for w in spec_wins)
    legacy_spans = sorted((w["start"], w["end"]) for w in legacy_wins)
    if spec_spans and legacy_spans and spec_spans != legacy_spans:
        disagreements.append(
            ShadowDisagreement(
                field="windows",
                legacy=legacy_wins,
                spec=spec_wins,
                detail="dated spans differ",
            )
        )
    elif bool(spec_spans) != bool(legacy_spans):
        disagreements.append(
            ShadowDisagreement(
                field="windows",
                legacy=legacy_wins,
                spec=spec_wins,
                detail="one side empty",
            )
        )

    if (spec.grain or None) != (legacy_grain or None) and (spec.grain or legacy_grain):
        disagreements.append(
            ShadowDisagreement(field="grain", legacy=legacy_grain, spec=spec.grain)
        )

    if legacy_breakdowns is not None:
        # Legacy stores candidate sets; match if every spec dim sits in some legacy set
        # and every legacy set has a hit — else flag.
        if spec.breakdowns or legacy_breakdowns:
            covered = all(
                any(d in cand for cand in legacy_breakdowns) for d in spec.breakdowns
            ) if spec.breakdowns else not legacy_breakdowns
            legacy_hit = all(
                any(d in cand for d in spec.breakdowns) for cand in legacy_breakdowns
            ) if legacy_breakdowns else not spec.breakdowns
            if not (covered and legacy_hit):
                disagreements.append(
                    ShadowDisagreement(
                        field="breakdowns",
                        legacy=[sorted(s) for s in legacy_breakdowns],
                        spec=list(spec.breakdowns),
                    )
                )

    if legacy_filter_terms is not None:
        spec_terms = frozenset(f.term.lower() for f in spec.filters)
        # Entities are intentional non-filters on the spec side.
        entity_terms = frozenset(e.source_span.lower() for e in spec.entities if e.source_span)
        legacy_only = legacy_filter_terms - spec_terms - entity_terms
        spec_only = spec_terms - legacy_filter_terms
        if legacy_only or spec_only:
            disagreements.append(
                ShadowDisagreement(
                    field="filters",
                    legacy=sorted(legacy_filter_terms),
                    spec=sorted(spec_terms),
                    detail=f"legacy_only={sorted(legacy_only)} spec_only={sorted(spec_only)}",
                )
            )

    return tuple(disagreements)


def build_spec_outcome(
    understanding: Any | None,
    *,
    resolution: dict[str, Any] | None,
    as_of: date,
    catalogue: Any | None = None,
    mode: QuerySpecMode = "shadow",
    legacy_window: TimeRangeV1 | None = None,
    legacy_grain: str | None = None,
    legacy_breakdowns: frozenset[frozenset[str]] | None = None,
    legacy_filter_terms: frozenset[str] | None = None,
) -> SpecOutcome:
    """Candidates → compile → validate → optional shadow. ``mode=off`` skips work."""
    if mode == "off":
        return SpecOutcome(stats={"mode": "off", "status": "skipped"})

    candidates = candidates_from_resolution(resolution)
    spec = compile_query_spec(
        understanding, candidates=candidates, as_of=as_of, catalogue=catalogue
    )
    spec = validate_query_spec(spec, catalogue=catalogue)
    shadow = (
        shadow_compare(
            spec,
            legacy_window=legacy_window,
            legacy_grain=legacy_grain,
            legacy_breakdowns=legacy_breakdowns,
            legacy_filter_terms=legacy_filter_terms,
        )
        if mode in ("shadow", "enforce")
        else ()
    )
    stats = {
        "mode": mode,
        "status": "ok",
        "valid": spec.valid,
        "windows": len(spec.windows),
        "filters": len(spec.filters),
        "entities": len(spec.entities),
        "shadow_disagreements": len(shadow),
        "assumptions": list(spec.assumptions),
        "errors": list(spec.errors),
    }
    if shadow:
        _log.info(
            "query_spec_shadow %s",
            {
                "disagreements": [
                    {"field": d.field, "legacy": d.legacy, "spec": d.spec, "detail": d.detail}
                    for d in shadow
                ],
                **{k: stats[k] for k in ("windows", "filters", "entities")},
            },
        )
    else:
        _log.info("query_spec_stats %s", stats)
    return SpecOutcome(candidates=candidates, spec=spec, shadow=shadow, stats=stats)


# ---------------------------------------------------------------------------
# Follow-ups: edit the previous turn's spec
# ---------------------------------------------------------------------------


def apply_spec_edit(
    prior: QuerySpec,
    understanding: Any | None,
    *,
    candidates: Candidates | None = None,
    as_of: date,
    catalogue: Any | None = None,
) -> QuerySpec:
    """Follow-ups amend the prior spec instead of starting from a blank reading.

    - Keeps prior windows when the new turn names none.
    - Keeps prior entities when the new turn is a "why" / associated follow-up.
    - Recompiles metrics/breakdowns from the new understanding when present.
    """
    fresh = compile_query_spec(
        understanding, candidates=candidates or Candidates(), as_of=as_of, catalogue=catalogue
    )
    if understanding is None:
        return prior

    windows = fresh.windows if fresh.windows else prior.windows
    entities = fresh.entities if fresh.entities else prior.entities
    entity_dimension = fresh.entity_dimension or prior.entity_dimension
    # A pure "why" follow-up keeps the prior baseline/event pair.
    if getattr(understanding, "follows_prior", False) and not fresh.windows:
        windows = prior.windows
    if getattr(understanding, "follows_prior", False) and not fresh.entities:
        entities = prior.entities
        entity_dimension = entity_dimension or prior.entity_dimension

    metrics = fresh.metrics or prior.metrics
    phrases = fresh.metric_phrases or prior.metric_phrases
    breakdowns = fresh.breakdowns if fresh.breakdowns else prior.breakdowns
    filters = fresh.filters if fresh.filters else prior.filters

    merged = replace(
        fresh,
        metrics=metrics,
        metric_phrases=phrases,
        filters=filters,
        entities=entities,
        entity_dimension=entity_dimension,
        breakdowns=breakdowns,
        windows=windows,
        follows_prior=True,
        question_axes=fresh.question_axes or prior.question_axes,
    )
    return validate_query_spec(merged, catalogue=catalogue)


def spec_from_dump(payload: dict[str, Any] | None) -> QuerySpec | None:
    """Rehydrate a QuerySpec from a turn-record dump (follow-up edits)."""
    if not payload:
        return None
    try:
        windows = tuple(
            ResolvedWindow(
                role=w["role"],  # type: ignore[arg-type]
                start=date.fromisoformat(w["start"]),
                end=date.fromisoformat(w["end"]),
                source_span=str(w.get("source_span") or ""),
                token=str(w.get("token") or ""),
            )
            for w in payload.get("windows") or []
        )
        filters = tuple(
            SpecFilter(
                term=str(f["term"]),
                dimension=str(f["dimension"]),
                values=tuple(f.get("values") or ()),
                volume=int(f.get("volume") or 0),
            )
            for f in payload.get("filters") or []
        )
        entities = tuple(
            EntityRef(
                dimension=str(e["dimension"]),
                value=str(e["value"]),
                source_span=str(e.get("source_span") or ""),
            )
            for e in payload.get("entities") or []
        )
        return QuerySpec(
            shape=payload.get("shape") or "other",  # type: ignore[arg-type]
            kind=str(payload.get("kind") or "analysis"),
            metrics=tuple(payload.get("metrics") or ()),
            metric_phrases=tuple(payload.get("metric_phrases") or ()),
            filters=filters,
            entities=entities,
            entity_dimension=str(payload.get("entity_dimension") or ""),
            breakdowns=tuple(payload.get("breakdowns") or ()),
            windows=windows,
            grain=payload.get("grain"),
            direction=str(payload.get("direction") or "either"),
            part_of_whole=bool(payload.get("part_of_whole")),
            follows_prior=bool(payload.get("follows_prior")),
            names_period=bool(payload.get("names_period")),
            question_axes=tuple(
                (str(a[0]), str(a[1])) for a in (payload.get("question_axes") or []) if len(a) == 2
            ),
            ordinary_words=tuple(payload.get("ordinary_words") or ()),
            assumptions=tuple(payload.get("assumptions") or ()),
            valid=bool(payload.get("valid", True)),
            errors=tuple(payload.get("errors") or ()),
        )
    except Exception:
        _log.warning("spec_from_dump_failed", exc_info=True)
        return None
