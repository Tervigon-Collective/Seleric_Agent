"""MEASUREMENT: do two systems that count the same events still agree? (plan §5.7, P3.2)

The reviewed graph names pairs of metrics that are the same events counted twice (a co-measure edge). On normal days
their ratio sits at a level of its own with a spread of its own; on the event days it should too. When it does not,
one of the two is wrong, and that comes ahead of any behavioural cause: a "why" for a number that is a tracking loss
sends the owner after the wrong thing. This is the data check the old lockstep heuristics approximated without the
graph; it verifies the reviewed fact on this brand's own history and says which side left its usual level."""

from __future__ import annotations

import math
import statistics

from seleric_swarm.diagnosis.domain.analysis import EventAnalysis
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.lineage import MetricMeta
from seleric_swarm.diagnosis.domain.numeric import _finite, _mean
from seleric_swarm.diagnosis.domain.report import MeasurementFinding
from seleric_swarm.diagnosis.operators.why_movement import Movement, measure_movement


def _usual_ratio_spread(ana: EventAnalysis, metric: str, peer: str, cfg: DiagnosisConfig) -> tuple[float, float, int] | None:
    """Median and spread (log units) of metric/peer over the normal history days; None when there are too few."""
    a, b = ana.inp.series[metric], ana.inp.series[peer]
    logs = [
        math.log(float(a[d]) / float(b[d])) for d in ana.history
        if d not in ana.outliers and _finite(a.get(d)) and _finite(b.get(d)) and float(a[d]) > 0 and float(b[d]) > 0
    ]
    if len(logs) < cfg.min_observation_rows:
        return None
    centre = statistics.median(logs)
    spread = 1.4826 * statistics.median(abs(x - centre) for x in logs)
    return centre, max(spread, cfg.measurement_min_scale), len(logs)


def _event_level(ana: EventAnalysis, metric: str) -> float | None:
    s = ana.inp.series[metric]
    values = [s.get(d) for d in ana.event_days]
    if not all(_finite(v) for v in values):
        return None
    floats = [float(v) for v in values if v is not None]
    return sum(floats) if ana.inp.lineage.get(metric, MetricMeta(metric)).additive else _mean(floats)


def _own_move(ana: EventAnalysis, metric: str, cfg: DiagnosisConfig) -> float | None:
    found = measure_movement(
        ana.inp.series[metric], ana.inp.lineage.get(metric, MetricMeta(metric)), history=ana.history,
        event_days=ana.event_days, start=ana.start, n_ev=ana.n_ev, window=ana.window, ref_kind=ana.ref_kind,
        outcome_additive=ana.meta_y.additive, cfg=cfg,
    )
    if isinstance(found, Movement) and found.x_ev is not None and found.x_rf:
        return found.x_ev / found.x_rf - 1.0
    return None


def check_measurements(ana: EventAnalysis, cfg: DiagnosisConfig) -> list[MeasurementFinding]:
    """One finding per co-measure pair of the outcome (and its parts) that has data on both sides."""
    if ana.baseline:  # a period comparison is explained by its components, not by a daily regression
        return []
    out: list[MeasurementFinding] = []
    for metric, peer, expected in ana.inp.measurement_pairs:
        usual = _usual_ratio_spread(ana, metric, peer, cfg)
        m_ev, p_ev = _event_level(ana, metric), _event_level(ana, peer)
        if usual is None or m_ev is None or p_ev is None or m_ev <= 0 or p_ev <= 0:
            out.append(MeasurementFinding(
                metric, peer, "insufficient_data", f"{metric} and {peer} do not have enough shared history or event-day values to compare",
                expected,
            ))
            continue
        centre, spread, n = usual
        shift = math.log(m_ev / p_ev) - centre
        z, gap = shift / spread, math.exp(shift) - 1.0
        diverged = abs(z) >= cfg.measurement_z and abs(gap) >= cfg.measurement_min_gap
        m_move, p_move = _own_move(ana, metric, cfg), _own_move(ana, peer, cfg)
        reason = (
            f"{metric} and {peer} count the same events; their ratio is {math.exp(shift + centre):.2f} on the event days "
            f"against {math.exp(centre):.2f} usually ({gap:+.0%}, {z:+.1f} usual spreads)"
        )
        if diverged and m_move is not None and p_move is not None:
            reason += f"; {metric} moved {m_move:+.0%} and {peer} {p_move:+.0%} against their own usual levels"
        out.append(MeasurementFinding(
            metric, peer, "diverged" if diverged else "agrees", reason, expected, usual_ratio=math.exp(centre),
            event_ratio=math.exp(shift + centre), gap=gap, z=z, metric_move=m_move, peer_move=p_move, n_history=n,
        ))
    return out
