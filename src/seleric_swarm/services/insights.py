"""Signals the user did not ask about — read from the hourly business-health snapshots.

A metric lookup answers what was asked. The risks and opportunities around it (the
asked metric is far outside its own normal range, a related metric in the same
domain just fell, a cost line jumped) only show up when someone looks across the
business. The domain-health scheduler already computes that every hour for every
domain (``services/domain_health``): each metric's period change, its robust
z-score against 28 days of history, and which direction is bad. Until now only the
whole-business overview read it.

This module reads those snapshots for any analytical question, keeps the signals
that are unusual (an anomaly flag, or a large move in either direction), ranks the
ones in the asked metrics' own domains and views first, then by severity, and
records them as one citable ``signal`` artifact. Nothing is
fetched: a missing or stale snapshot yields no signals. Which metrics are watched,
which direction is bad and the anomaly window are the profiles' YAML, not code.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.services.domain_health.models import DomainStateSnapshot, ResolvedMetric

_log = logging.getLogger("seleric.services.insights")

_MOVE_PCT = 15.0  # a period change this large is worth a look even without an anomaly flag
_MAX_SIGNALS = 3
_MAX_AGE_HOURS = 6.0


@dataclass
class Signal:
    metric_id: str
    domain: str
    kind: str  # "risk" | "opportunity" | "unusual"
    severity: float
    related: bool
    text: str
    metric: ResolvedMetric
    snapshot: DomainStateSnapshot


def enabled() -> bool:
    return os.getenv("INSIGHT_SIGNALS", "1").strip().lower() not in {"0", "false", "no"}


def _bare(metric_id: str) -> str:
    return metric_id.removeprefix("metric.")


def _signal(
    m: ResolvedMetric, snapshot: DomainStateSnapshot, related: bool, metric_id: str = "", label: str = "",
) -> Signal | None:
    anomaly = m.anomaly or {}
    flagged = bool(anomaly.get("is_anomaly"))
    delta = m.period_delta_pct
    big_move = delta is not None and abs(delta) >= _MOVE_PCT
    if not (flagged or big_move):
        return None
    moved = anomaly.get("direction") if flagged else ("up" if (delta or 0) > 0 else "down")
    if m.direction_bad in ("up", "down") and moved in ("up", "down"):
        kind = "risk" if moved == m.direction_bad else "opportunity"
    else:
        kind = "unusual"
    severity = float(anomaly.get("score") or 0.0) if flagged else abs(delta or 0.0) / 10.0
    name = label or _bare(m.metric_id)
    bits = [f"{name} ({snapshot.domain}) = {m.value:g}" if m.value is not None else name]
    if delta is not None:
        bits.append(f"{delta:+.1f}% vs the previous period")
    if flagged:
        expected = anomaly.get("expected")
        bits.append(
            f"outside its normal range ({anomaly.get('score', 0):.1f} robust z"
            + (f", typical ≈ {expected:g}" if isinstance(expected, (int, float)) else "")
            + ")"
        )
    return Signal(
        metric_id=metric_id or _bare(m.metric_id),
        domain=snapshot.domain,
        kind=kind,
        severity=severity,
        related=related,
        text=f"{kind}: " + ", ".join(bits),
        metric=m,
        snapshot=snapshot,
    )


async def gather_signals(
    deps: SelericDeps,
    asked_metric_ids: list[str],
    *,
    store: Any = None,
    limit: int = _MAX_SIGNALS,
) -> list[Signal]:
    """The few most notable snapshot signals for this question, related ones first."""
    from seleric_swarm.services.business_state.formatter import is_stale
    from seleric_swarm.services.domain_health.scheduler import ALL_DOMAINS
    from seleric_swarm.services.domain_health.snapshot_store import SnapshotStore

    store = store or SnapshotStore()
    asked = {deps.canonical_metric_id(_bare(m)) for m in asked_metric_ids}
    views = {meta.view for meta in deps.catalogue.metrics if meta.id in asked and meta.view}
    view_of = {meta.id: meta.view for meta in deps.catalogue.metrics}
    label_of = {meta.id: meta.label for meta in deps.catalogue.metrics if meta.label}

    def canonical(snapshot_metric_id: str) -> str:
        # The registry id as stored ("metric.net_profit_order") resolves to its
        # catalogue id; the bare spelling does not, and leaked into answers as
        # "net_profit_order" (live 2026-10-08, golden Q1/Q2).
        definition = deps.metrics.get(snapshot_metric_id) if deps.metrics is not None else None
        if definition is not None and definition.catalogue_metric:
            return deps.canonical_metric_id(definition.catalogue_metric)
        mid = deps.canonical_metric_id(snapshot_metric_id)
        return mid if mid != snapshot_metric_id else deps.canonical_metric_id(_bare(snapshot_metric_id))

    snapshots: list[DomainStateSnapshot] = []
    for domain in ALL_DOMAINS:
        snap = await store.aget_latest(domain)
        if snap is None or snap.status == "UNAVAILABLE" or is_stale(snap, max_age_hours=_MAX_AGE_HOURS):
            continue
        snapshots.append(snap)
    asked_domains = {
        s.domain for s in snapshots for m in s.metrics if canonical(m.metric_id) in asked
    }
    signals: list[Signal] = []
    for snap in snapshots:
        for m in snap.metrics:
            mid = canonical(m.metric_id)
            related = mid in asked or snap.domain in asked_domains or view_of.get(mid) in views
            if (sig := _signal(m, snap, related, mid, label_of.get(mid, ""))) is not None:
                signals.append(sig)
    signals.sort(key=lambda s: (not s.related, -s.severity))
    return signals[:limit]


def record_signals(deps: SelericDeps, signals: list[Signal]) -> str | None:
    """Store the signals as one citable ``signal`` artifact.

    Not ``evidence``: a snapshot value is hours old and may cover a different
    window than a live query for the same metric, and the claim checks
    (contradiction, scope coverage) must judge the answer on what this mission
    fetched. The validator still counts these numbers as backed, so citing one
    in a "Worth a look" line is grounded."""
    if not signals:
        return None
    rows = []
    for sig in signals:
        anomaly = sig.metric.anomaly or {}
        rows.append(
            {
                "metric": sig.metric_id,
                "domain": sig.domain,
                "kind": sig.kind,
                "as_of": sig.snapshot.as_of,
                "value": sig.metric.value,
                "change_pct": sig.metric.period_delta_pct,
                "robust_z": anomaly.get("score") if anomaly.get("is_anomaly") else None,
                "typical": anomaly.get("expected") if anomaly.get("is_anomaly") else None,
            }
        )
    artifact = deps.artifact_store.put(
        Artifact(
            workspace_id=deps.principal.workspace_id,
            artifact_type="signal",
            payload={
                "statement": "Unusual movements in the hourly business-health snapshots, related ones first.",
                "signals": rows,
            },
            classification="factual",
            evidence_ids=[f"snapshot:{s.domain}:{s.metric_id}:{s.snapshot.as_of}" for s in signals],
            provenance=ArtifactProvenance(
                query_version="domain_health_snapshot",
                source_metadata={s.domain: dict(s.snapshot.provenance or {}) for s in signals},
            ),
            mission_id=deps.mission_id,
        )
    )
    return artifact.id


def signals_block(signals: list[Signal], finding_id: str | None) -> str:
    if not signals or not finding_id:
        return ""
    lines = [
        "[signals the user did not ask about — hourly business-health snapshot; not this question's answer]",
        *[f"- {s.text}" for s in signals],
        (
            "If one of these bears on the question (same metric, a driver or cost of it, or a risk the "
            "user would want to know), add at most two as a short 'Worth a look' line after the answer, "
            f"citing evidence_ids=[{finding_id}]. Ignore any that are unrelated. Never let them replace "
            "the answer to what was asked."
        ),
    ]
    return "\n".join(lines) + "\n\n"


async def insight_block(deps: SelericDeps, asked_metric_ids: list[str]) -> tuple[str, dict[str, Any]]:
    """Fail-open wrapper the runner calls: (prompt block, stats)."""
    if not enabled():
        return "", {"status": "disabled"}
    try:
        signals = await gather_signals(deps, asked_metric_ids)
        finding_id = record_signals(deps, signals)
    except Exception:
        _log.warning("insight_signals_failed", exc_info=True)
        return "", {"status": "failed"}
    return signals_block(signals, finding_id), {
        "status": "ok",
        "signals": [{"metric": s.metric_id, "kind": s.kind, "related": s.related} for s in signals],
    }
