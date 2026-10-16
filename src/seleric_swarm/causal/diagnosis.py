"""Deprecated import path of the diagnosis engine (plan P-1, D-27).

The engine lives in ``seleric_swarm/diagnosis/`` (domain, detect, operators, report). This module keeps every name the
application, the exploration engine and the tests imported from here, so callers do not change until Gate 5.
``diagnose`` runs the stages with the production collaborators (worker-process estimator, application value
formatting, the default configuration). Functions that now take the configuration, a formatter or a clock as an
argument are wrapped here to supply those defaults.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from seleric_swarm.diagnosis.adapters.legacy_estimator import PoolEstimator
from seleric_swarm.diagnosis.adapters.service_formatting import ServiceFormatting
from seleric_swarm.diagnosis.detect.calendar import (
    CalendarFit,
)
from seleric_swarm.diagnosis.detect.calendar import (
    fit_calendar as _impl_fit_calendar,
)
from seleric_swarm.diagnosis.detect.detector import robust_center_scale
from seleric_swarm.diagnosis.detect.reference import Referencer
from seleric_swarm.diagnosis.detect.reference import prior_days as _prior_days
from seleric_swarm.diagnosis.detect.seasonal import (
    _PERIOD_KIND,
    SeasonalProfile,
    _err,
    _period_shift_z,
    _reference_kind,
    _robust_center_scale,
    _transform,
)
from seleric_swarm.diagnosis.detect.seasonal import (
    _abs_noise as _impl__abs_noise,
)
from seleric_swarm.diagnosis.detect.seasonal import (
    _has_weekly_pattern as _impl__has_weekly_pattern,
)
from seleric_swarm.diagnosis.detect.seasonal import (
    _lockstep_correlation as _impl__lockstep_correlation,
)
from seleric_swarm.diagnosis.detect.seasonal import (
    _seasonal_profile as _impl__seasonal_profile,
)
from seleric_swarm.diagnosis.detect.seasonal import (
    seasonal_profile as _impl_seasonal_profile,
)
from seleric_swarm.diagnosis.detect.window import (
    _additive_window,
    _daily_window,
    _ref_value,
    reference_days,
)
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.estimates import TimeSeriesEffect
from seleric_swarm.diagnosis.domain.identity import (
    Identity,
)
from seleric_swarm.diagnosis.domain.inputs import (
    DiagnosisInput,
)
from seleric_swarm.diagnosis.domain.lineage import (
    MetricMeta,
    descendants,
    lineage_from_definitions,
    lineage_in_degree,
    unit_count_metric,
)
from seleric_swarm.diagnosis.domain.numeric import (
    Series,
    _finite,
    _mean,
    _median,
    _rel,
    _sign,
)
from seleric_swarm.diagnosis.domain.report import (
    BridgeTerm,
    Classification,
    DecompositionTerm,
    DiagnosisReport,
    DimensionFinding,
    Direction,
    DriverFinding,
    EventSummary,
    SegmentMove,
)
from seleric_swarm.diagnosis.domain.window import (
    EventWindow,
)
from seleric_swarm.diagnosis.operators.bridge_search import (
    _BridgeSpace,
    _sign_patterns,
    shapley,
)
from seleric_swarm.diagnosis.operators.bridge_search import (
    discover_bridge_tree as _impl_discover_bridge_tree,
)
from seleric_swarm.diagnosis.operators.identities import (
    _verify as _impl__verify,
)
from seleric_swarm.diagnosis.operators.identities import (
    discover_identities as _impl_discover_identities,
)
from seleric_swarm.diagnosis.operators.identities import (
    discover_rate_chains as _impl_discover_rate_chains,
)
from seleric_swarm.diagnosis.operators.identities import (
    rate_chain_proposals,
)
from seleric_swarm.diagnosis.operators.is_real_terms import (
    _event_value as _impl__event_value,
)
from seleric_swarm.diagnosis.operators.pipeline import EngineEnv, ScopeFactory
from seleric_swarm.diagnosis.operators.pipeline import diagnose as _run_stages
from seleric_swarm.diagnosis.operators.what_terms import (
    _additive_factor_values as _impl__additive_factor_values,
)
from seleric_swarm.diagnosis.operators.what_terms import (
    _bridge as _impl__bridge,
)
from seleric_swarm.diagnosis.operators.what_terms import (
    _chain_step as _impl__chain_step,
)
from seleric_swarm.diagnosis.operators.what_terms import (
    _decompose as _impl__decompose,
)
from seleric_swarm.diagnosis.operators.what_terms import (
    _linked_outliers as _impl__linked_outliers,
)
from seleric_swarm.diagnosis.operators.what_terms import (
    _own_window as _impl__own_window,
)
from seleric_swarm.diagnosis.operators.where_dimensions import (
    _analyse_additive_dimension as _impl__analyse_additive_dimension,
)
from seleric_swarm.diagnosis.operators.where_dimensions import (
    _covers_whole as _impl__covers_whole,
)
from seleric_swarm.diagnosis.operators.where_dimensions import (
    _is_identifier_like,
    _rate_coverage,
    _segment_ref,
    _segment_values,
    _specificity,
    _typical_specificity,
)
from seleric_swarm.diagnosis.operators.where_rate import (
    _analyse_rate_dimension as _impl__analyse_rate_dimension,
)
from seleric_swarm.diagnosis.operators.where_rate import (
    _analyse_ratio_dimension as _impl__analyse_ratio_dimension,
)
from seleric_swarm.diagnosis.operators.why_terms import (
    _classify_driver as _impl__classify_driver,
)
from seleric_swarm.diagnosis.operators.why_terms import (
    _PendingDriver,
)
from seleric_swarm.diagnosis.operators.why_terms import (
    _residual_correlation as _impl__residual_correlation,
)
from seleric_swarm.diagnosis.report.format import (
    _MULTIPLE_UNIT,
    _SHARE_UNITS,
    _chg,
    _display_lineage,
    _fmt,
    _label,
    _offsetting,
    _pct,
    _share,
)
from seleric_swarm.diagnosis.report.format import (
    _effect as _impl__effect,
)
from seleric_swarm.diagnosis.report.format import (
    _val as _impl__val,
)
from seleric_swarm.diagnosis.report.headline import (
    _bridge_lines as _impl__bridge_lines,
)
from seleric_swarm.diagnosis.report.headline import (
    _headline as _impl__headline,
)
from seleric_swarm.diagnosis.report.headline import (
    _opposite,
    _previous_label,
    _reference_words,
    _same_weekday,
    _usual_blend,
    _usual_for,
)
from seleric_swarm.diagnosis.report.narrative import (
    _narrative as _impl__narrative,
)
from seleric_swarm.diagnosis.report.narrative import (
    _rate_segment_text as _impl__rate_segment_text,
)
from seleric_swarm.diagnosis.report.narrative import (
    _ratio_segment_text as _impl__ratio_segment_text,
)

_CONFIG = DiagnosisConfig()
_FORMATTING = ServiceFormatting()


def _with_defaults(fn: Callable[..., Any], **defaults: Any) -> Callable[..., Any]:
    """``fn`` with the production defaults filled in for the keyword-only collaborators it now takes."""

    @functools.wraps(fn)
    def call(*args: Any, **kwargs: Any) -> Any:
        for key, value in defaults.items():
            kwargs.setdefault(key, value)
        return fn(*args, **kwargs)

    return call


fit_calendar = _with_defaults(_impl_fit_calendar, cfg=_CONFIG)
_abs_noise = _with_defaults(_impl__abs_noise, cfg=_CONFIG)
_has_weekly_pattern = _with_defaults(_impl__has_weekly_pattern, cfg=_CONFIG)
_lockstep_correlation = _with_defaults(_impl__lockstep_correlation, cfg=_CONFIG)
_seasonal_profile = _with_defaults(_impl__seasonal_profile, cfg=_CONFIG)
seasonal_profile = _with_defaults(_impl_seasonal_profile, cfg=_CONFIG)
discover_bridge_tree = _with_defaults(_impl_discover_bridge_tree, cfg=_CONFIG, clock=time.monotonic)
_verify = _with_defaults(_impl__verify, cfg=_CONFIG)
discover_identities = _with_defaults(_impl_discover_identities, cfg=_CONFIG)
discover_rate_chains = _with_defaults(_impl_discover_rate_chains, cfg=_CONFIG)
_event_value = _with_defaults(_impl__event_value, cfg=_CONFIG)
_additive_factor_values = _with_defaults(_impl__additive_factor_values, cfg=_CONFIG)
_bridge = _with_defaults(_impl__bridge, cfg=_CONFIG, clock=time.monotonic)
_chain_step = _with_defaults(_impl__chain_step, cfg=_CONFIG)
_decompose = _with_defaults(_impl__decompose, cfg=_CONFIG)
_linked_outliers = _with_defaults(_impl__linked_outliers, cfg=_CONFIG)
_own_window = _with_defaults(_impl__own_window, cfg=_CONFIG)
_analyse_additive_dimension = _with_defaults(_impl__analyse_additive_dimension, cfg=_CONFIG)
_covers_whole = _with_defaults(_impl__covers_whole, cfg=_CONFIG)
_analyse_rate_dimension = _with_defaults(_impl__analyse_rate_dimension, cfg=_CONFIG)
_analyse_ratio_dimension = _with_defaults(_impl__analyse_ratio_dimension, cfg=_CONFIG)
_classify_driver = _with_defaults(_impl__classify_driver, cfg=_CONFIG)
_residual_correlation = _with_defaults(_impl__residual_correlation, cfg=_CONFIG)
_effect = _with_defaults(_impl__effect, fmt=_FORMATTING)
def _val(v: float | None, metric: str, lineage: dict[str, MetricMeta]) -> str:
    return _impl__val(v, metric, lineage, _FORMATTING)

_bridge_lines = _with_defaults(_impl__bridge_lines, fmt=_FORMATTING)
_headline = _with_defaults(_impl__headline, cfg=_CONFIG)
_narrative = _with_defaults(_impl__narrative, cfg=_CONFIG, fmt=_FORMATTING)
_rate_segment_text = _with_defaults(_impl__rate_segment_text, fmt=_FORMATTING)
_ratio_segment_text = _with_defaults(_impl__ratio_segment_text, fmt=_FORMATTING)
__all__ = [
    "_MULTIPLE_UNIT",
    "_PERIOD_KIND",
    "_SHARE_UNITS",
    "BridgeTerm",
    "CalendarFit",
    "Classification",
    "DecompositionTerm",
    "DiagnosisInput",
    "DiagnosisReport",
    "DimensionFinding",
    "Direction",
    "DriverFinding",
    "EventSummary",
    "EventWindow",
    "Identity",
    "MetricMeta",
    "Referencer",
    "SeasonalProfile",
    "SegmentMove",
    "Series",
    "TimeSeriesEffect",
    "_BridgeSpace",
    "_PendingDriver",
    "_abs_noise",
    "_additive_factor_values",
    "_additive_window",
    "_analyse_additive_dimension",
    "_analyse_rate_dimension",
    "_analyse_ratio_dimension",
    "_bridge",
    "_bridge_lines",
    "_chain_step",
    "_chg",
    "_classify_driver",
    "_covers_whole",
    "_daily_window",
    "_decompose",
    "_display_lineage",
    "_effect",
    "_err",
    "_event_value",
    "_finite",
    "_fmt",
    "_has_weekly_pattern",
    "_headline",
    "_is_identifier_like",
    "_label",
    "_linked_outliers",
    "_lockstep_correlation",
    "_mean",
    "_median",
    "_narrative",
    "_offsetting",
    "_opposite",
    "_own_window",
    "_pct",
    "_period_shift_z",
    "_previous_label",
    "_prior_days",
    "_rate_coverage",
    "_rate_segment_text",
    "_ratio_segment_text",
    "_ref_value",
    "_reference_kind",
    "_reference_words",
    "_rel",
    "_residual_correlation",
    "_robust_center_scale",
    "_same_weekday",
    "_seasonal_profile",
    "_segment_ref",
    "_segment_values",
    "_share",
    "_sign",
    "_sign_patterns",
    "_specificity",
    "_transform",
    "_typical_specificity",
    "_usual_blend",
    "_usual_for",
    "_val",
    "_verify",
    "default_env",
    "descendants",
    "diagnose",
    "discover_bridge_tree",
    "discover_identities",
    "discover_rate_chains",
    "fit_calendar",
    "lineage_from_definitions",
    "lineage_in_degree",
    "rate_chain_proposals",
    "reference_days",
    "robust_center_scale",
    "seasonal_profile",
    "shapley",
    "unit_count_metric",
]


def default_env(*, scope: ScopeFactory | None = None) -> EngineEnv:
    """The production collaborators; ``scope`` opens a trace scope around each stage."""
    env = EngineEnv(config=_CONFIG, estimator=PoolEstimator(), formatting=_FORMATTING, clock=time.monotonic)
    return replace(env, scope=scope) if scope is not None else env


def diagnose(inp: DiagnosisInput, env: EngineEnv | None = None) -> DiagnosisReport:
    """The report for ``inp``, with the production collaborators unless ``env`` supplies others."""
    return _run_stages(inp, env or default_env())

