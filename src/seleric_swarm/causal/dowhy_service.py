"""DoWhy boundary of the diagnosis engine: the same-day total effect of one candidate driver on the outcome.

``estimate_time_series_effect`` builds an explicit time-series DAG (calendar and lagged pre-treatment variables only),
identifies the back-door estimand with DoWhy, estimates it by OLS with a conservative interval, and runs refuters and
temporal-precedence tests. It runs in a worker process; whatever fails inside it is returned in the result's
``failures`` rather than lost. (The earlier ``DoWhyService``, which adjusted for every co-measured metric, had no
caller and was removed in Phase 2.)
"""

from __future__ import annotations

import functools
import logging
import math
import traceback
from collections.abc import Callable
from contextvars import ContextVar
from datetime import timedelta
from typing import Any

from seleric_swarm.diagnosis.domain.estimates import TimeSeriesEffect

_log = logging.getLogger(__name__)

# Failures noted while one estimate runs; the public entry point installs a fresh list and hands it back in the result.
_failures: ContextVar[list[dict[str, str]] | None] = ContextVar("time_series_effect_failures", default=None)
_TRACEBACK_CHARS = 1500


def _note_failure(site: str, exc: BaseException) -> None:
    sink = _failures.get()
    if sink is not None:
        sink.append({
            "site": site, "error_type": type(exc).__name__, "message": str(exc)[:300],
            "traceback": "".join(traceback.format_exception(exc))[-_TRACEBACK_CHARS:],
        })


def _collecting_failures[**P](fn: Callable[P, TimeSeriesEffect]) -> Callable[P, TimeSeriesEffect]:
    """Run ``fn`` with a fresh failure list and hand what was noted back in the result's ``failures``."""

    @functools.wraps(fn)
    def run(*args: P.args, **kwargs: P.kwargs) -> TimeSeriesEffect:
        token = _failures.set([])
        try:
            result = fn(*args, **kwargs)
            result.failures = list(_failures.get() or [])
            return result
        finally:
            _failures.reset(token)

    return run


# ---------------------------------------------------------------------------
# Time-series effect estimation with an explicit DAG (diagnosis engine).
#
# The legacy ``DoWhyService.estimate`` above adjusts for *every* other metric
# in the frame with ``proceed_when_unidentifiable=True``: with daily business
# series that turns mediators (spend -> sessions -> sales) and colliders into
# "confounders" and lets weekly seasonality, shared trends and reverse feedback
# masquerade as causal effects. ``estimate_time_series_effect`` instead builds
# the graph it assumes and states it:
#
#   calendar (day-of-week, trend) -> X, Y      exogenous by construction
#   Y[t-1] -> X[t], Y[t]                         past outcome: blocks feedback
#   X[t-1] -> X[t], Y[t]                         carry-over of the treatment
#   Z[t-1] -> X[t], Y[t]                         other candidates, lagged only
#   X[t]   -> Y[t]                               the effect being estimated
#
# Every adjustment variable is pre-treatment, so no mediator or collider can
# enter the backdoor set; contemporaneous other metrics are deliberately left
# out (they may sit on the X -> Y path). DoWhy identifies the backdoor set from
# that graph; the point estimate is DoWhy's; the interval is HAC (Newey-West)
# because daily residuals are autocorrelated and plain OLS intervals are too
# narrow. Refutation adds two time-series checks DoWhy does not ship: a
# circular-shift placebo (keeps autocorrelation + seasonality, breaks timing)
# and a future-treatment (lead) placebo — X[t+1] must not "explain" Y[t].
# ---------------------------------------------------------------------------


def _lag_name(column: str) -> str:
    return f"{column}__lag1"


def _hac_lags(n: int) -> int:
    # Newey-West rule of thumb floor(4 (n/100)^(2/9)), at least 1.
    return max(1, math.floor(4 * (n / 100.0) ** (2.0 / 9.0)))


def _ols(y: Any, X: Any, *, hac: bool = True) -> Any:
    import statsmodels.api as sm

    model = sm.OLS(y, sm.add_constant(X, has_constant="add"))
    if hac:
        return model.fit(cov_type="HAC", cov_kwds={"maxlags": _hac_lags(len(y))})
    return model.fit()


def conservative_interval(y: Any, X: Any, column: str, alpha: float) -> tuple[float, float, float, float]:
    """(beta, se, lo, hi) for one coefficient with a deliberately conservative SE.

    Monte Carlo at this sample size (n~55, ~10 controls, 200 reps): plain
    Newey-West 95% intervals covered the true effect only 87-90% of the time;
    HC3 covered 95-97%, the small-sample-corrected HAC ~92%. Taking the larger
    of HC3 and corrected HAC keeps coverage at or above nominal, so noise is
    not promoted into an "effect".
    """
    import statsmodels.api as sm
    from scipy import stats

    model = sm.OLS(y, sm.add_constant(X, has_constant="add"))
    hc3 = model.fit(cov_type="HC3")
    hac = model.fit(cov_type="HAC", cov_kwds={"maxlags": _hac_lags(len(y)), "use_correction": True})
    beta = float(hc3.params[column])
    se = max(float(hc3.bse[column]), float(hac.bse[column]))
    t = float(stats.t.ppf(1 - alpha / 2, df=max(1, int(hc3.df_resid))))
    return beta, se, beta - t * se, beta + t * se


def build_time_series_frame(
    outcome: dict[Any, float],
    treatment: dict[Any, float],
    others: dict[str, dict[Any, float]],
    days: list[Any],
    *,
    treatment_name: str = "treatment",
    outcome_name: str = "outcome",
    calendar: bool = True,
) -> Any:
    """Daily frame with calendar terms and one-day lags on consecutive days.

    A lag is only taken from the literal previous calendar day: a missing day
    yields NaN rather than silently pairing a value with a stale one.
    """
    import pandas as pd

    rows: list[dict[str, Any]] = []
    t0 = min(days) if days else None
    for d in sorted(days):
        prev = d - timedelta(days=1)
        row: dict[str, Any] = {
            "__day": d,
            outcome_name: outcome.get(d, math.nan),
            treatment_name: treatment.get(d, math.nan),
            _lag_name(outcome_name): outcome.get(prev, math.nan),
            _lag_name(treatment_name): treatment.get(prev, math.nan),
            "__lead": treatment.get(d + timedelta(days=1), math.nan),
        }
        for name, series in others.items():
            row[_lag_name(name)] = series.get(prev, math.nan)
        if calendar:
            row["trend"] = float((d - t0).days) / 7.0
            for k in range(1, 7):
                row[f"dow_{k}"] = 1.0 if d.weekday() == k else 0.0
        rows.append(row)
    return pd.DataFrame(rows)


def _granger_p(frame: Any, cause: str, effect: str, calendar_cols: list[str]) -> float | None:
    """F-test p-value that cause[t-1] adds to effect[t] beyond effect[t-1] + calendar."""
    cols = [_lag_name(effect), _lag_name(cause), *calendar_cols]
    sub = frame[[effect, *cols]].dropna()
    if len(sub) < len(cols) + 6:
        return None
    try:
        fit = _ols(sub[effect], sub[cols], hac=False)
        return float(fit.pvalues[_lag_name(cause)])
    except Exception as exc:
        _note_failure("temporal_precedence_test", exc)
        return None


def _intervention_shifts(frame: Any, T: str, Y: str, calendar_cols: list[str], beta: float) -> dict[str, Any]:
    """Direction evidence from abrupt, intervention-like shifts in the treatment.

    A same-day effect leaves no lead/lag signature, so Granger tests cannot
    orient it. What can: days where X jumps far outside its normal day-to-day
    change (a budget switched off/on, a site outage) while Y had NOT already
    moved the day before, and Y then moves the way the estimated effect says.
    Two or more such shifts, none contradicting, is evidence that X moves Y
    rather than Y moving X. Calendar effects are removed first so a weekly
    pattern is never read as a shift.
    """
    import numpy as np

    cols = [c for c in calendar_cols if c in frame.columns]
    sub = frame[["__day", T, Y, *cols]].dropna().reset_index(drop=True)
    if len(sub) < 14:
        return {"shifts": 0, "consistent": False}
    resid: dict[str, Any] = {}
    for c in (T, Y):
        if cols:
            fit = _ols(sub[c], sub[cols], hac=False)
            resid[c] = (sub[c] - fit.fittedvalues).to_numpy()
        else:
            resid[c] = (sub[c] - sub[c].mean()).to_numpy()
    days = list(sub["__day"])
    consecutive = [i for i in range(2, len(days)) if (days[i] - days[i - 1]).days == 1 and (days[i - 1] - days[i - 2]).days == 1]
    if not consecutive:
        return {"shifts": 0, "consistent": False}
    dx = np.array([resid[T][i] - resid[T][i - 1] for i in consecutive])
    dy = np.array([resid[Y][i] - resid[Y][i - 1] for i in consecutive])
    pre = np.array([resid[Y][i - 1] - resid[Y][i - 2] for i in consecutive])

    def robust_sd(v: Any) -> float:
        med = float(np.median(v))
        mad = float(np.median(np.abs(v - med))) * 1.4826
        return mad if mad > 0 else float(np.std(v)) or 1e-12

    sx, sy = robust_sd(dx), robust_sd(dy)
    jumps = [k for k in range(len(consecutive)) if abs(dx[k]) >= 3 * sx]
    agree = disagree = anticipated = 0
    for k in jumps:
        if abs(pre[k]) >= 2 * sy and np.sign(pre[k]) == np.sign(beta * dx[k]):
            anticipated += 1  # Y was already moving that way: X may be reacting to Y
        elif np.sign(dy[k]) == np.sign(beta * dx[k]) and abs(dy[k]) >= 1.5 * sy:
            agree += 1
        elif abs(dy[k]) >= 1.5 * sy:
            disagree += 1
    return {
        "shifts": len(jumps),
        "followed_by_outcome": agree,
        "contradicted": disagree,
        "outcome_moved_first": anticipated,
        "consistent": agree >= 2 and disagree == 0 and anticipated == 0,
    }


@_collecting_failures
def estimate_time_series_effect(
    *,
    outcome: dict[Any, float],
    treatment: dict[Any, float],
    others: dict[str, dict[Any, float]],
    days: list[Any],
    treatment_name: str,
    outcome_name: str,
    alpha: float = 0.05,
    refuter_tolerance: float = 0.25,
    min_rows_per_covariate: int = 3,
    min_rows: int = 8,
    placebo_shifts: int = 60,
    run_dowhy_refuters: bool = True,
) -> TimeSeriesEffect:
    """Estimate the same-day total effect of ``treatment`` on ``outcome``.

    ``others`` are the competing candidate drivers; they enter only as one-day
    lags (pre-treatment). Returns ``status="insufficient"`` with a reason
    instead of raising whenever the data cannot support an estimate.
    """
    import networkx as nx
    import numpy as np

    T, Y = treatment_name, outcome_name
    result = TimeSeriesEffect(treatment=T, outcome=Y, status="insufficient")
    if T == Y:
        result.reason = "treatment and outcome are the same series"
        return result

    span_days = (max(days) - min(days)).days + 1 if days else 0
    calendar = span_days >= 21  # three weeks before day-of-week terms are estimable
    frame = build_time_series_frame(
        outcome, treatment, others, days,
        treatment_name=T, outcome_name=Y, calendar=calendar,
    )
    calendar_cols = (["trend", *[f"dow_{k}" for k in range(1, 7)]] if calendar else [])
    base_adjust = [*calendar_cols, _lag_name(Y), _lag_name(T)]
    other_lags = [_lag_name(n) for n in others]

    def usable(adjust: list[str]) -> Any:
        sub = frame[[Y, T, *adjust]].dropna()
        needed = max(min_rows, min_rows_per_covariate * (len(adjust) + 2))
        return sub if len(sub) >= needed else None

    adjust = [*base_adjust, *other_lags]
    data = usable(adjust)
    # Shed the least essential covariates first: competing-candidate lags, then
    # the calendar, never the outcome/treatment lags that block feedback.
    while data is None and other_lags:
        other_lags = other_lags[:-1]
        adjust = [*base_adjust, *other_lags]
        data = usable(adjust)
    if data is None and calendar_cols:
        trimmed = [_lag_name(Y), _lag_name(T)]
        if (d2 := usable(trimmed)) is not None:
            adjust, data, calendar_cols = trimmed, d2, []
    if data is None:
        complete = len(frame[[Y, T]].dropna())
        result.reason = f"only {complete} complete daily rows; too few to adjust for calendar and lags"
        result.n_rows = complete
        return result

    x = data[T].astype(float)
    if float(x.std(ddof=0)) <= 1e-12 or x.nunique() < 3:
        result.reason = f"{T} barely varies over the estimation window; its effect is not identifiable"
        result.n_rows = len(data)
        return result

    # Naive association (what a correlation-only engine would report).
    try:
        naive = _ols(data[Y], data[[T]], hac=True)
        result.naive_association = {
            "slope": float(naive.params[T]),
            "p_value": float(naive.pvalues[T]),
            "correlation": float(np.corrcoef(data[T], data[Y])[0, 1]),
        }
    except Exception as exc:
        _note_failure("naive_association", exc)
        result.naive_association = {}

    # Explicit DAG: every adjustment variable is a pre-treatment parent of both.
    edges: list[tuple[str, str]] = [(c, T) for c in adjust] + [(c, Y) for c in adjust] + [(T, Y)]
    graph = nx.DiGraph(edges)
    try:
        from dowhy import CausalModel

        model = CausalModel(data=data[[Y, T, *adjust]], treatment=T, outcome=Y, graph=graph)
        estimand = model.identify_effect()
        backdoor = sorted(estimand.get_backdoor_variables() or [])
        if not backdoor and adjust:
            result.reason = "DoWhy could not identify a backdoor adjustment set for this graph"
            return result
        estimate = model.estimate_effect(estimand, method_name="backdoor.linear_regression")
        dowhy_effect = float(estimate.value)
    except Exception as exc:
        _note_failure("dowhy_estimation", exc)
        _log.warning("time-series dowhy estimation failed for %s -> %s", T, Y, exc_info=True)
        result.reason = f"DoWhy estimation failed: {type(exc).__name__}: {exc}"
        return result

    beta, se, lo, hi = conservative_interval(data[Y], data[[T, *backdoor]], T, alpha)
    if not math.isclose(beta, dowhy_effect, rel_tol=1e-6, abs_tol=1e-9):
        _log.warning("dowhy effect %s != OLS effect %s for %s -> %s", dowhy_effect, beta, T, Y)

    result.status = "estimated"
    result.effect = dowhy_effect
    result.std_error = se
    from scipy import stats

    result.p_value = float(2 * stats.t.sf(abs(beta / se), df=max(1, len(data) - len(backdoor) - 2))) if se > 0 else 0.0
    result.confidence_interval = [lo, hi]
    result.n_rows = len(data)
    result.adjustment_set = backdoor
    result.graph_edges = edges
    result.identified_estimand = "backdoor: E[Y | do(X)] via adjustment for " + (", ".join(backdoor) or "nothing")

    # ---- temporal precedence (Granger-style, calendar-controlled) ---------
    p_xy = _granger_p(frame, T, Y, calendar_cols)
    p_yx = _granger_p(frame, Y, T, calendar_cols)
    shifts_evidence = _intervention_shifts(frame, T, Y, calendar_cols, beta)
    if p_yx is not None and p_yx < alpha:
        direction = "feedback"  # past outcome predicts the treatment
    elif p_xy is not None and p_xy < alpha:
        direction = "temporal"  # past treatment predicts outcome, not vice versa
    elif shifts_evidence.get("consistent"):
        direction = "intervention"  # repeated abrupt shifts in X, Y follows without moving first
    else:
        direction = "assumed"   # same-day association only; direction is an assumption
    result.temporal = {
        "p_treatment_leads_outcome": p_xy,
        "p_outcome_leads_treatment": p_yx,
        "treatment_shifts": shifts_evidence,
        "direction": direction,
    }

    # ---- refutation ---------------------------------------------------------
    refs: list[dict[str, Any]] = []
    denom = abs(beta) if abs(beta) > 1e-12 else 1.0

    if run_dowhy_refuters:
        for name, kwargs in (
            ("random_common_cause", {"num_simulations": 20}),
            ("data_subset_refuter", {"subset_fraction": 0.8, "num_simulations": 20}),
        ):
            try:
                ref = model.refute_estimate(estimand, estimate, method_name=name, random_seed=7, **kwargs)
                new = float(ref.new_effect)
                drift = abs(new - dowhy_effect) / denom
                refs.append({
                    "name": name,
                    "new_effect": new,
                    "relative_drift": drift,
                    "passed": drift <= refuter_tolerance and (new * dowhy_effect > 0 or abs(dowhy_effect) < 1e-12),
                })
            except Exception as exc:
                _note_failure(f"refuter:{name}", exc)
                refs.append({"name": name, "passed": False, "error": str(exc)})

    # Circular-shift placebo: same series, broken timing.
    n = len(frame)
    shifts = [s for s in range(7, n - 6)]
    if len(shifts) > placebo_shifts:
        step = len(shifts) / placebo_shifts
        shifts = [shifts[int(i * step)] for i in range(placebo_shifts)]
    placebo_betas: list[float] = []
    if shifts:
        xs = frame[T].to_numpy()
        for s in shifts:
            shifted = frame.copy()
            rolled = np.roll(xs, s)
            shifted[T] = rolled
            shifted[_lag_name(T)] = np.roll(rolled, 1)
            sub = shifted[[Y, T, *backdoor]].dropna()
            if len(sub) < len(backdoor) + 4 or sub[T].std(ddof=0) <= 1e-12:
                continue
            try:
                placebo_betas.append(float(_ols(sub[Y], sub[[T, *backdoor]], hac=False).params[T]))
            except Exception:  # noqa: S112 - a singular shifted design is just one fewer placebo draw
                continue
    if placebo_betas:
        exceed = sum(1 for b in placebo_betas if abs(b) >= abs(beta))
        p_placebo = (1 + exceed) / (1 + len(placebo_betas))
        refs.append({
            "name": "time_shift_placebo",
            "p_value": p_placebo,
            "placebo_mean": float(np.mean(placebo_betas)),
            "draws": len(placebo_betas),
            "passed": p_placebo < alpha,  # effect must stand out from timing-scrambled copies
        })
    else:
        refs.append({"name": "time_shift_placebo", "passed": False, "error": "series too short for a shift placebo"})

    # Future-treatment placebo: X[t+1] must not explain Y[t] given X[t].
    lead = frame[[Y, T, "__lead", *backdoor]].dropna()
    if len(lead) >= len(backdoor) + 6 and lead["__lead"].std(ddof=0) > 1e-12:
        try:
            from scipy import stats

            coef_lead, se_lead, _, _ = conservative_interval(lead[Y], lead[[T, "__lead", *backdoor]], "__lead", alpha)
            df_lead = max(1, len(lead) - len(backdoor) - 3)
            p_lead = float(2 * stats.t.sf(abs(coef_lead / se_lead), df=df_lead)) if se_lead > 0 else 0.0
            refs.append({
                "name": "future_treatment_placebo",
                "lead_effect": coef_lead,
                "p_value": p_lead,
                "passed": p_lead >= alpha or abs(coef_lead) < 0.5 * denom,
            })
        except Exception as exc:
            _note_failure("refuter:future_treatment_placebo", exc)
            refs.append({"name": "future_treatment_placebo", "passed": False, "error": str(exc)})
    else:
        refs.append({"name": "future_treatment_placebo", "passed": False, "error": "not enough rows with a next-day treatment"})

    # Influence: the estimate must not hinge on a few extreme days.
    try:
        plain = _ols(data[Y], data[[T, *backdoor]], hac=False)
        cooks = plain.get_influence().cooks_distance[0]
        keep = cooks <= 4.0 / len(data)
        dropped = int((~keep).sum())
        if dropped and keep.sum() >= len(backdoor) + 6:
            kept = data[keep]
            nb = float(_ols(kept[Y], kept[[T, *backdoor]], hac=False).params[T])
            drift = abs(nb - beta) / denom
            refs.append({
                "name": "influential_days_removed",
                "dropped_days": dropped,
                "new_effect": nb,
                "relative_drift": drift,
                "passed": nb * beta > 0 and drift <= 2 * refuter_tolerance,
            })
        else:
            refs.append({"name": "influential_days_removed", "dropped_days": dropped, "passed": True})
    except Exception as exc:
        _note_failure("refuter:influential_days_removed", exc)
        refs.append({"name": "influential_days_removed", "passed": False, "error": str(exc)})

    result.refutations = refs
    return result
