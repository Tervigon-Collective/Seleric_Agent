"""Exploration engine — what is worth knowing in this data right now?

Pure calculation over already-fetched daily series (``toolsets/exploration.py``
plans and fetches; this module reasons), in the same split as
``causal/diagnosis.py``. Where diagnosis answers "why did X change?", this
answers the question before it: *which* metric, segment or relationship is
behaving unusually, so there is something worth diagnosing.

One run is a batch of hypothesis tests over the explored subspace:

- per metric, on its total: **period_change** (this window vs the previous one,
  judged against window-to-window moves in its own history), **trend** and
  **change_point** over the fetched daily history;
- per additive metric and breakdown dimension: **distribution_shift**
  (Adtributor's explanatory power + surprise over the window change) and
  **outstanding** (one segment far larger than the rest predict);
- across metrics: **co_movement** of day-to-day changes, skipped for pairs the
  catalogue already relates (lineage, or the same units measured twice).

Every test yields a p-value under its own null hypothesis; Benjamini-Hochberg
over *all* of them (not only the reported ones) keeps the false-discovery rate
at ``EXPLORE_FDR`` — the more the engine looks, the more it demands. Survivors
must also clear a practical-size floor, then rank by
``kind prior x impact x significance`` (Tang et al.'s impact x significance,
with impact = the explored subspace's share of the whole metric).

Each insight carries typed ``follow_ups``: the next probe that would deepen it
(drill into the segment, diagnose the move). The agent — or a later turn —
chooses among them, which is what makes exploration a walk rather than a
single fetch. Every insight is an OBSERVATION except co-movement, an
ASSOCIATION; none of them is a cause.

Nothing here names a metric, dimension, value or business threshold.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from itertools import pairwise
from typing import Any, Literal

from seleric_swarm.causal import diagnosis as dx
from seleric_swarm.exploration import insights as st
from seleric_swarm.toolsets import policy_config as P

Series = dict[date, float]
Kind = Literal["period_change", "trend", "change_point", "distribution_shift", "outstanding", "co_movement"]


@dataclass(frozen=True)
class FollowUp:
    """A next probe: a registered tool and the arguments that would run it."""

    tool: str
    args: dict[str, Any]
    reason: str


@dataclass
class Insight:
    kind: Kind
    metric: str
    statement: str
    p_value: float
    significance: float  # in [0, 1]
    effect: float  # practical size (relative change, share, |r|) — tie-break and floor
    practical: bool
    impact: float = 1.0
    dimension: str | None = None
    segments: tuple[str, ...] = ()
    related_metric: str | None = None
    q_value: float | None = None
    score: float = 0.0
    classification: Literal["OBSERVATION", "ASSOCIATION"] = "OBSERVATION"
    stats: dict[str, float] = field(default_factory=dict)
    days: tuple[date, ...] = ()  # days whose evidence backs the statement
    at: date | None = None  # the day a step change starts
    follow_ups: list[FollowUp] = field(default_factory=list)


@dataclass
class ExplorationInput:
    window: list[date]  # the current window's complete days, ascending
    series: dict[str, Series]  # metric -> daily totals over the whole fetched span
    lineage: dict[str, dx.MetricMeta]
    segments: dict[str, dict[str, dict[str, Series]]] = field(default_factory=dict)  # metric -> dim -> seg -> daily
    history_windows: int = P.EXPLORE_HISTORY_WINDOWS
    subspace_share: dict[str, float] = field(default_factory=dict)  # metric -> share of the unfiltered total
    filters: dict[str, Any] = field(default_factory=dict)
    scope_tokens: frozenset[str] = frozenset()
    top_k: int = 8


@dataclass
class ExplorationReport:
    window: list[date]
    insights: list[Insight]
    tested: int
    survived: int
    data_quality: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        def enc(v: Any) -> Any:
            if isinstance(v, date):
                return v.isoformat()
            if isinstance(v, dict):
                return {k: enc(x) for k, x in v.items()}
            if isinstance(v, (list, tuple)):
                return [enc(x) for x in v]
            if isinstance(v, float) and not math.isfinite(v):
                return None
            return v

        return enc({
            "window": self.window,
            "tested": self.tested,
            "survived": self.survived,
            "fdr": P.EXPLORE_FDR,
            "insights": [asdict(i) for i in self.insights],
            "data_quality": self.data_quality,
        })


# --------------------------------------------------------------------------- windows
def windows(window: list[date], count: int) -> list[list[date]]:
    """``[current, previous, ...]``: ``count`` equal-length windows before the current one."""
    n = len(window)
    return [[window[0] - timedelta(days=j * n - i) for i in range(n)] for j in range(count + 1)]


def window_value(series: Series, days: list[date], additive: bool) -> float | None:
    """A window's value: the sum of an additive metric (a day without a row is 0 —
    Cube returns no row for an empty day), the mean daily value of anything else."""
    present = [series[d] for d in days if d in series and math.isfinite(series[d])]
    if not present:
        return None
    if additive:
        return float(sum(present))
    return float(sum(present) / len(present)) if len(present) >= 0.5 * len(days) else None


def _period(days: list[date] | tuple[date, ...]) -> str:
    return days[0].isoformat() if len(days) == 1 else f"{days[0].isoformat()} to {days[-1].isoformat()}"


def _dim_label(dimension: str) -> str:
    return dimension.replace("_", " ").strip()


def _pct(v: float) -> str:
    return f"{abs(v) * 100:.0f}%"


# --------------------------------------------------------------------------- extractors
def _period_change(inp: ExplorationInput, metric: str, wins: list[list[date]]) -> Insight | None:
    meta = inp.lineage.get(metric, dx.MetricMeta(metric))
    values = [window_value(inp.series[metric], w, meta.additive) for w in wins]
    cur, prev = values[0], values[1] if len(values) > 1 else None
    if cur is None or prev is None or abs(prev) < 1e-12:
        return None
    rel = [
        newer / older - 1
        for newer, older in pairwise(values)
        if newer is not None and older is not None and abs(older) > 1e-12
    ]
    if len(rel) < 4:  # the observed change plus >= 3 historical ones
        return None
    observed, history = cur / prev - 1, rel[1:]
    pred = st.prediction_t(observed, history)
    if pred is None:
        return None
    t, df = pred
    p = st.t_tail(t, df, two_sided=True)
    typical = sorted(abs(h) for h in history)[len(history) // 2]
    label = dx._label(metric, inp.lineage)
    n = len(wins[0])
    statement = (
        f"{label} was {dx._val(cur, metric, inp.lineage)} over {_period(wins[0])}, "
        f"{dx._chg(cur, prev)} vs the previous {n} days ({dx._val(prev, metric, inp.lineage)}). "
        f"Over the {len(history)} windows before that, a typical move was ±{_pct(typical)}"
        + ("" if meta.additive else " (window values are daily averages)")
        + "."
    )
    follow: list[FollowUp] = []
    if n <= 14:
        follow.append(FollowUp(
            "diagnose_metric_change",
            {"metric_id": metric, "event_start": wins[0][0].isoformat(), "event_end": wins[0][-1].isoformat(),
             "claimed_direction": "up" if observed > 0 else "down",
             **({"filters": dict(inp.filters)} if inp.filters else {})},
            f"explain why {label} {'rose' if observed > 0 else 'fell'}",
        ))
    return Insight(
        kind="period_change", metric=metric, statement=statement, p_value=p,
        significance=1 - p, effect=abs(observed),
        practical=abs(observed) >= P.EXPLORE_MIN_RELATIVE_CHANGE,
        stats={"current": cur, "previous": prev, "change": observed, "typical_change": typical, "t": t},
        days=tuple(sorted({*wins[0], *wins[1]})), follow_ups=follow,
    )


def _trend(inp: ExplorationInput, metric: str, span: list[date]) -> Insight | None:
    s = {d: v for d, v in inp.series[metric].items() if span[0] <= d <= span[-1]}
    tr = st.trend(s)
    if tr is None:
        return None
    label = dx._label(metric, inp.lineage)
    direction = "up" if tr.slope_per_day > 0 else "down"
    days = sorted(s)
    statement = (
        f"{label} has been trending {direction} by about {_pct(tr.relative_per_week)} of its level per week "
        f"over {_period(days)} (day-of-week effects removed)."
    )
    return Insight(
        kind="trend", metric=metric, statement=statement, p_value=tr.p_value,
        significance=(1 - tr.p_value) * tr.partial_r2, effect=abs(tr.relative_per_week),
        practical=abs(tr.relative_per_week) >= P.EXPLORE_MIN_TREND_PER_WEEK,
        stats={"relative_per_week": tr.relative_per_week, "partial_r2": tr.partial_r2, "n_days": float(tr.n_days)},
        days=tuple(days),
        follow_ups=[FollowUp(
            "query_metrics",
            {"metric_id": metric, "grain": "week", "period_start": days[0].isoformat(), "period_end": days[-1].isoformat(),
             **({"dimensions": dict(inp.filters)} if inp.filters else {})},
            f"show the weekly series behind the {label} trend",
        )],
    )


def _change_point(inp: ExplorationInput, metric: str, span: list[date]) -> Insight | None:
    s = {d: v for d, v in inp.series[metric].items() if span[0] <= d <= span[-1]}
    cp = st.change_point(s)
    if cp is None:
        return None
    label = dx._label(metric, inp.lineage)
    last = max(s)
    statement = (
        f"{label} stepped {'up' if cp.relative > 0 else 'down'} about {_pct(cp.relative)} from "
        f"{cp.first_day.isoformat()}: about {dx._val(cp.before, metric, inp.lineage)} a day before, "
        f"{dx._val(cp.after, metric, inp.lineage)} a day since."
    )
    event_end = min(cp.first_day + timedelta(days=6), last)
    return Insight(
        kind="change_point", metric=metric, statement=statement, p_value=cp.p_value,
        significance=1 - cp.p_value, effect=abs(cp.relative),
        practical=abs(cp.relative) >= P.EXPLORE_MIN_SHIFT,
        stats={"before": cp.before, "after": cp.after, "change": cp.relative},
        days=tuple(sorted(s)), at=cp.first_day,
        follow_ups=[FollowUp(
            "diagnose_metric_change",
            {"metric_id": metric, "event_start": cp.first_day.isoformat(), "event_end": event_end.isoformat(),
             "claimed_direction": "up" if cp.relative > 0 else "down",
             **({"filters": dict(inp.filters)} if inp.filters else {})},
            f"explain the step change in {label} from {cp.first_day.isoformat()}",
        )],
    )


def _drill(inp: ExplorationInput, metric: str, dimension: str, segment: str, why: str) -> FollowUp:
    return FollowUp("explore_data", {"metric_ids": [metric], "filters": {**inp.filters, dimension: segment}}, why)


def _segment_totals(seg: dict[str, Series], days: list[date]) -> dict[str, float]:
    return {k: float(sum(s.get(d, 0.0) for d in days)) for k, s in seg.items()}


def _distribution_shift(
    inp: ExplorationInput, metric: str, dimension: str, seg: dict[str, Series], wins: list[list[date]]
) -> Insight | None:
    totals = [_segment_totals(seg, w) for w in wins]
    history = [d for k in range(1, len(totals) - 1) if (d := st.js_divergence(totals[k + 1], totals[k])) is not None]
    sh = st.distribution_shift(
        totals[1], totals[0], history, t_eep=P.EXPLORE_ADTRIBUTOR_T_EEP, t_ep=P.EXPLORE_ADTRIBUTOR_T_EP,
    )
    if sh is None:
        return None
    label = dx._label(metric, inp.lineage)
    dim = _dim_label(dimension)
    def val(v: float) -> str:
        return dx._val(v, metric, inp.lineage)

    parts = [f"{r.segment} {val(r.previous)} → {val(r.current)} ({dx._chg(r.current, r.previous)})" for r in sh.explaining]
    if sh.explained is not None:
        statement = (
            f"By {dim}, the {dx._chg(sh.current_total, sh.previous_total)} change in {label} "
            f"({_period(wins[0])} vs the previous {len(wins[0])} days) sits mostly in "
            + "; ".join(parts) + f" — together {_pct(sh.explained)} of the change."
        )
    else:
        parts = [f"{r.segment} {_pct(r.previous_share)} → {_pct(r.current_share)} of the total" for r in sh.explaining]
        statement = (
            f"{label} was about flat overall ({_period(wins[0])} vs the previous {len(wins[0])} days), "
            f"but its mix by {dim} shifted: " + "; ".join(parts) + "."
        )
    if sh.opposing:
        statement += " Moving the other way: " + "; ".join(
            f"{r.segment} ({dx._chg(r.current, r.previous)})" for r in sh.opposing
        ) + "."
    share_move = max(abs(r.current_share - r.previous_share) for r in sh.explaining)
    top = sh.explaining[0].segment
    follow = [_drill(inp, metric, dimension, top, f"look inside {top} ({dim}) for what moved")]
    if len(wins[0]) <= 14:
        follow.append(FollowUp(
            "diagnose_metric_change",
            {"metric_id": metric, "event_start": wins[0][0].isoformat(), "event_end": wins[0][-1].isoformat(),
             "filters": {**inp.filters, dimension: top}},
            f"explain the move in {label} for {top}",
        ))
    return Insight(
        kind="distribution_shift", metric=metric, dimension=dimension,
        segments=tuple(r.segment for r in sh.explaining), statement=statement, p_value=sh.p_value,
        significance=1 - sh.p_value, effect=share_move,
        practical=share_move >= P.EXPLORE_MIN_SHARE_MOVE,
        stats={"divergence": sh.divergence, "explained": sh.explained if sh.explained is not None else float("nan"),
               "previous_total": sh.previous_total, "current_total": sh.current_total,
               **{f"share.{r.segment}.previous": r.previous_share for r in sh.explaining},
               **{f"share.{r.segment}.current": r.current_share for r in sh.explaining}},
        days=tuple(sorted({*wins[0], *wins[1]})), follow_ups=follow,
    )


def _outstanding(
    inp: ExplorationInput, metric: str, dimension: str, seg: dict[str, Series], window: list[date]
) -> Insight | None:
    out = st.outstanding_top(_segment_totals(seg, window))
    if out is None:
        return None
    label = dx._label(metric, inp.lineage)
    dim = _dim_label(dimension)
    ratio = out.top_value / out.runner_up_value if out.runner_up_value > 0 else float("inf")
    statement = (
        f"{out.top} is by far the largest {dim} for {label}: {_pct(out.share)} of the total over {_period(window)}, "
        f"{ratio:.1f}× the next ({out.runner_up})."
    )
    return Insight(
        kind="outstanding", metric=metric, dimension=dimension, segments=(out.top,), statement=statement,
        p_value=out.p_value, significance=1 - out.p_value, effect=out.share,
        practical=out.share >= P.EXPLORE_MIN_TOP_SHARE and ratio >= 1.5,
        stats={"share": out.share, "top": out.top_value, "runner_up": out.runner_up_value},
        days=tuple(window),
        follow_ups=[_drill(inp, metric, dimension, out.top, f"explore {label} within {out.top} ({dim})")],
    )


def _related(a: str, b: str, inp: ExplorationInput) -> bool:
    """True when the catalogue already explains a correlation: lineage, or the same units."""
    la, lb = inp.lineage.get(a, dx.MetricMeta(a)), inp.lineage.get(b, dx.MetricMeta(b))
    if a in dx.descendants(b, inp.lineage) or b in dx.descendants(a, inp.lineage):
        return True
    if a in lb.depends_on or b in la.depends_on or set(la.depends_on) & set(lb.depends_on):
        return True
    return bool(la.entity_tokens(inp.scope_tokens) & lb.entity_tokens(inp.scope_tokens))


def _co_movement(inp: ExplorationInput, a: str, b: str, span: list[date]) -> Insight | None:
    sa = {d: v for d, v in inp.series[a].items() if span[0] <= d <= span[-1]}
    sb = {d: v for d, v in inp.series[b].items() if span[0] <= d <= span[-1]}
    res = st.co_movement(sa, sb)
    if res is None:
        return None
    r, p, n = res
    la, lb = dx._label(a, inp.lineage), dx._label(b, inp.lineage)
    statement = (
        f"{la} and {lb} move {'together' if r > 0 else 'in opposite directions'} day to day "
        f"over {_period(sorted(set(sa) & set(sb)))}: on a day one is unusually high, the other is usually "
        f"{'high' if r > 0 else 'low'} too. This is an association, not evidence that one drives the other."
    )
    return Insight(
        kind="co_movement", metric=a, related_metric=b, statement=statement, p_value=p,
        significance=(1 - p) * r * r, effect=abs(r), practical=abs(r) >= P.EXPLORE_MIN_ABS_CORRELATION,
        classification="ASSOCIATION", stats={"r": r, "n": float(n)},
        days=tuple(sorted(set(sa) & set(sb))),
    )


# --------------------------------------------------------------------------- the run
def _dedupe(ranked: list[Insight], window: list[date]) -> list[Insight]:
    """One insight per movement. A trend and a step change on the same series
    usually describe one movement (keep whichever ranks higher), and a step that
    starts inside the current window is the window change already reported."""
    shaped: set[str] = set()
    changed = {i.metric for i in ranked if i.kind == "period_change"}
    out: list[Insight] = []
    for i in ranked:
        if i.kind in ("trend", "change_point"):
            if i.metric in shaped:
                continue
            if i.kind == "change_point" and i.metric in changed and i.at is not None and i.at >= window[0]:
                continue
            shaped.add(i.metric)
        out.append(i)
    return out


def explore(inp: ExplorationInput) -> ExplorationReport:
    notes: list[str] = []
    if not inp.window:
        return ExplorationReport(window=[], insights=[], tested=0, survived=0, data_quality=["no complete days to explore"])
    wins = windows(inp.window, inp.history_windows)
    span = sorted({d for w in wins for d in w})
    candidates: list[Insight] = []
    metrics = [m for m in inp.series if inp.series[m]]
    for m in metrics:
        if (i := _period_change(inp, m, wins)) is not None:
            candidates.append(i)
        for shape in (_trend, _change_point):
            if (i := shape(inp, m, span)) is not None:
                candidates.append(i)
        if not inp.lineage.get(m, dx.MetricMeta(m)).additive:
            if inp.segments.get(m):
                notes.append(f"{dx._label(m, inp.lineage)} is a ratio; its segments were not decomposed")
            continue
        for dim, seg in (inp.segments.get(m) or {}).items():
            if len(seg) < 2:
                continue
            if (i := _distribution_shift(inp, m, dim, seg, wins)) is not None:
                candidates.append(i)
            if (i := _outstanding(inp, m, dim, seg, wins[0])) is not None:
                candidates.append(i)
    for x in range(len(metrics)):
        for y in range(x + 1, len(metrics)):
            a, b = metrics[x], metrics[y]
            if not _related(a, b, inp) and (i := _co_movement(inp, a, b, span)) is not None:
                candidates.append(i)

    for i, q in zip(candidates, st.benjamini_hochberg([c.p_value for c in candidates]), strict=True):
        i.q_value = q
        i.impact = inp.subspace_share.get(i.metric, 1.0)
        i.score = P.EXPLORE_KIND_PRIOR.get(i.kind, 0.5) * i.impact * i.significance
    survivors = [i for i in candidates if i.practical and i.q_value is not None and i.q_value <= P.EXPLORE_FDR]
    survivors.sort(key=lambda i: (-i.score, -i.effect))
    ranked = _dedupe(survivors, inp.window)
    return ExplorationReport(
        window=list(inp.window), insights=ranked[: inp.top_k], tested=len(candidates),
        survived=len(survivors), data_quality=notes,
    )
