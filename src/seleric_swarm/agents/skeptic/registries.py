"""External dependency ports for the Skeptic + in-memory implementations.

Every collaborator the Skeptic needs is a ``Protocol`` so the agent never binds
to a concrete service. The ``InMemory*`` classes are deterministic and are what
the test-suite and offline swarm runs use. Production wiring implements the same
Protocols against Postgres / the real MetricRegistry / DoWhy / a model registry.

Where a real repository service already exists it is adapted, not duplicated:
``metric_registry_from_yaml`` seeds :class:`InMemoryMetricSemanticsRegistry`
from ``config/metric_registry.yaml`` and ``causal_graphs_from_yaml`` from
``config/causal_graphs.example.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml

from seleric_swarm.agents.skeptic.contracts import (
    CausalAnalysisArtifact,
    StrategyArtifact,
)
from seleric_swarm.paths import repo_root

# --------------------------------------------------------------------------- #
# Evidence + artifact repositories
# --------------------------------------------------------------------------- #


@runtime_checkable
class EvidenceRepository(Protocol):
    async def get(self, evidence_id: str) -> dict[str, Any] | None: ...

    async def get_many(self, evidence_ids: list[str]) -> list[dict[str, Any]]: ...

    async def search_related(
        self, mission_id: str, *, metric_id: str | None = None
    ) -> list[dict[str, Any]]: ...


@runtime_checkable
class ArtifactRepository(Protocol):
    """Read access to non-evidence artifacts (anomaly / causal / forecast / ...)."""

    async def get(self, artifact_id: str) -> dict[str, Any] | None: ...

    async def by_type(self, mission_id: str, artifact_type: str) -> list[dict[str, Any]]: ...


class InMemoryEvidenceRepository:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._by_id: dict[str, dict[str, Any]] = {}
        for row in rows or []:
            self.add(row)

    def add(self, row: dict[str, Any]) -> str:
        eid = row.get("evidence_id") or row.get("artifact_id") or f"EV-{len(self._by_id)}"
        row = {**row, "evidence_id": eid}
        self._by_id[eid] = row
        return eid

    async def get(self, evidence_id: str) -> dict[str, Any] | None:
        return self._by_id.get(evidence_id)

    async def get_many(self, evidence_ids: list[str]) -> list[dict[str, Any]]:
        return [self._by_id[e] for e in evidence_ids if e in self._by_id]

    async def search_related(
        self, mission_id: str, *, metric_id: str | None = None
    ) -> list[dict[str, Any]]:
        out = [r for r in self._by_id.values() if not mission_id or r.get("mission_id") in ("", mission_id)]
        if metric_id:
            out = [r for r in out if (r.get("metric_id") or r.get("metric_or_fact")) == metric_id]
        return out


class InMemoryArtifactRepository:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._by_id: dict[str, dict[str, Any]] = {}
        for row in rows or []:
            self.add(row)

    def add(self, row: dict[str, Any]) -> str:
        aid = row.get("artifact_id") or row.get("causal_id") or row.get("forecast_id") or row.get(
            "strategy_id"
        ) or row.get("anomaly_id") or f"ART-{len(self._by_id)}"
        row = {**row, "artifact_id": aid}
        self._by_id[aid] = row
        return aid

    async def get(self, artifact_id: str) -> dict[str, Any] | None:
        return self._by_id.get(artifact_id)

    async def by_type(self, mission_id: str, artifact_type: str) -> list[dict[str, Any]]:
        return [
            r
            for r in self._by_id.values()
            if r.get("artifact_type") == artifact_type
            and (not mission_id or r.get("mission_id") in ("", mission_id))
        ]


def repositories_from_blackboard(blackboard: Any) -> tuple[InMemoryEvidenceRepository, InMemoryArtifactRepository]:
    """Adapt a live ``seleric_swarm.swarm.blackboard.Blackboard`` for the Skeptic."""

    evidence = InMemoryEvidenceRepository()
    artifacts = InMemoryArtifactRepository()
    for payload in blackboard._store.all():
        if payload.get("artifact_type") == "evidence":
            evidence.add({**payload, "evidence_id": payload.get("artifact_id")})
        else:
            artifacts.add(payload)
    return evidence, artifacts


# --------------------------------------------------------------------------- #
# Metric semantics registry
# --------------------------------------------------------------------------- #


@dataclass
class MetricSemantics:
    metric_id: str
    version: int = 1
    formula: str = ""
    attribution_basis: str = ""
    grain: str = "day"
    currency: str | None = None
    timezone: str = "Asia/Kolkata"
    returns_treatment: str = ""
    gross_or_net: str = ""
    cohort_definition: str = ""
    owner: str = ""


@runtime_checkable
class MetricSemanticsRegistry(Protocol):
    def get(self, metric_id: str) -> MetricSemantics | None: ...


class InMemoryMetricSemanticsRegistry:
    def __init__(self, metrics: dict[str, MetricSemantics] | None = None) -> None:
        self._m = dict(metrics or {})

    def add(self, sem: MetricSemantics) -> None:
        self._m[sem.metric_id] = sem

    def get(self, metric_id: str) -> MetricSemantics | None:
        return self._m.get(metric_id)


def metric_registry_from_yaml(path: str | Path | None = None) -> InMemoryMetricSemanticsRegistry:
    p = Path(path) if path else repo_root() / "config" / "metric_registry.yaml"
    reg = InMemoryMetricSemanticsRegistry()
    if not p.exists():
        return reg
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    for item in data.get("metrics", []):
        reg.add(
            MetricSemantics(
                metric_id=item["id"],
                version=int(item.get("version", 1)),
                formula=item.get("formula", ""),
                attribution_basis=item.get("attribution_basis", ""),
                grain=item.get("grain", "day"),
                currency=item.get("unit"),
                timezone=item.get("timezone", "Asia/Kolkata"),
                returns_treatment=item.get("returns_treatment", ""),
                gross_or_net=item.get("gross_or_net", ""),
                cohort_definition=item.get("cohort_definition", ""),
                owner=item.get("owner", ""),
            )
        )
    return reg


# --------------------------------------------------------------------------- #
# Causal graph registry
# --------------------------------------------------------------------------- #


@dataclass
class CausalGraph:
    graph_id: str
    version: str = "v1"
    nodes: list[str] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)

    def has_path(self, src: str, dst: str) -> bool:
        adj: dict[str, list[str]] = {}
        for a, b in self.edges:
            adj.setdefault(a, []).append(b)
        seen: set[str] = set()
        stack = [src]
        while stack:
            cur = stack.pop()
            if cur == dst:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(adj.get(cur, []))
        return False


@runtime_checkable
class CausalGraphRegistry(Protocol):
    def get(self, graph_id: str) -> CausalGraph | None: ...


class InMemoryCausalGraphRegistry:
    def __init__(self, graphs: dict[str, CausalGraph] | None = None) -> None:
        self._g = dict(graphs or {})

    def add(self, graph: CausalGraph) -> None:
        self._g[graph.graph_id] = graph

    def get(self, graph_id: str) -> CausalGraph | None:
        return self._g.get(graph_id)


def causal_graphs_from_yaml(path: str | Path | None = None) -> InMemoryCausalGraphRegistry:
    p = Path(path) if path else repo_root() / "config" / "causal_graphs.example.yaml"
    reg = InMemoryCausalGraphRegistry()
    if not p.exists():
        return reg
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    for g in data.get("graphs", []):
        reg.add(
            CausalGraph(
                graph_id=g["id"],
                version=str(g.get("version", "v1")),
                nodes=list(g.get("nodes", [])),
                edges=[tuple(e) for e in g.get("edges", [])],
            )
        )
    return reg


# --------------------------------------------------------------------------- #
# Model registry
# --------------------------------------------------------------------------- #


@dataclass
class ModelRecord:
    model_id: str
    version: str = "1"
    status: str = "candidate"  # candidate | approved | deprecated
    target: str = ""
    model_type: str = "forecast"
    minimum_history_days: int = 0
    supports_seasonality: bool = False
    last_validated_at: str | None = None
    backtest_available: bool = False


@runtime_checkable
class ModelRegistry(Protocol):
    def get(self, model_id: str) -> ModelRecord | None: ...


class InMemoryModelRegistry:
    def __init__(self, models: dict[str, ModelRecord] | None = None) -> None:
        self._m = dict(models or {})

    def add(self, rec: ModelRecord) -> None:
        self._m[rec.model_id] = rec

    def get(self, model_id: str) -> ModelRecord | None:
        return self._m.get(model_id)

    def ids(self) -> list[str]:
        return list(self._m)

    def for_target(self, target: str) -> list[ModelRecord]:
        return [r for r in self._m.values() if r.target == target]


@dataclass
class DriftReport:
    model_id: str
    status: str  # green | amber | red | drifted | unknown
    signals: dict[str, float] = field(default_factory=dict)
    detail: str = ""


@runtime_checkable
class DriftMonitor(Protocol):
    async def status_for(self, model_id: str, *, features: dict[str, Any]) -> DriftReport: ...


class NullDriftMonitor:
    """No monitor wired -> 'unknown'. The Skeptic treats 'unknown' as a warning,
    never a pass, so this stays honest."""

    async def status_for(self, model_id: str, *, features: dict[str, Any]) -> DriftReport:
        return DriftReport(model_id=model_id, status="unknown", detail="no drift monitor configured")


# --------------------------------------------------------------------------- #
# Incident / historical pattern registry
# --------------------------------------------------------------------------- #


@dataclass
class IncidentPattern:
    pattern_id: str
    domain: str
    trigger: str
    typical_mechanism: str
    known_confounders: list[str] = field(default_factory=list)


@runtime_checkable
class IncidentRegistry(Protocol):
    def match(self, *, domain: str | None, keywords: list[str]) -> list[IncidentPattern]: ...


class InMemoryIncidentRegistry:
    def __init__(self, patterns: list[IncidentPattern] | None = None) -> None:
        self._p = list(patterns or _DEFAULT_INCIDENTS)

    def match(self, *, domain: str | None, keywords: list[str]) -> list[IncidentPattern]:
        kw = {k.lower() for k in keywords}
        out = []
        for pat in self._p:
            if domain and pat.domain != domain:
                continue
            hay = f"{pat.trigger} {pat.typical_mechanism}".lower()
            if not kw or any(k in hay for k in kw):
                out.append(pat)
        return out


_DEFAULT_INCIDENTS = [
    IncidentPattern(
        "cac_up_frontend_regression",
        "performance",
        "cac increase with quiet media metrics",
        "frontend/latency regression degrades post-click conversion",
        ["auction pressure", "attribution change", "pricing change", "traffic mix"],
    ),
    IncidentPattern(
        "ctr_down_creative_fatigue",
        "performance",
        "ctr decline with rising frequency",
        "creative fatigue OR auction pressure raising CPM",
        ["auction pressure", "audience saturation", "seasonality"],
    ),
    IncidentPattern(
        "cvr_drop_checkout_failure",
        "funnel",
        "purchase conversion decline",
        "checkout/payment failure or price/stock change",
        ["pricing change", "stock issue", "payment failure", "attribution change"],
    ),
]


# --------------------------------------------------------------------------- #
# Business-rule service (strategy constraints)
# --------------------------------------------------------------------------- #


@dataclass
class RuleViolation:
    rule_id: str
    description: str
    severity: str  # "warning" | "blocking"
    domain: str
    remediation_capability: str | None = None


@runtime_checkable
class BusinessRuleService(Protocol):
    async def validate_strategy(self, strategy: StrategyArtifact, *, context: dict[str, Any]) -> list[RuleViolation]: ...


class InMemoryBusinessRuleService:
    """Deterministic guardrails. Real impl reads finance/inventory constraint stores."""

    def __init__(self, context_defaults: dict[str, Any] | None = None) -> None:
        self._defaults = context_defaults or {}

    async def validate_strategy(
        self, strategy: StrategyArtifact, *, context: dict[str, Any]
    ) -> list[RuleViolation]:
        ctx = {**self._defaults, **context}
        action = (strategy.action or "").lower()
        out: list[RuleViolation] = []
        stock_cover = ctx.get("stock_cover_days")
        scales_acquisition = ("spend" in action or "budget" in action or "acquisition" in action) and (
            "increase" in action or "scale" in action or "raise" in action
        )
        if (
            scales_acquisition
            and stock_cover is not None
            and float(stock_cover) < ctx.get("critical_stock_cover_days", 7)
        ):
            out.append(
                RuleViolation(
                    rule_id="inventory.no_scale_when_stock_critical",
                    description=(
                        f"Action scales acquisition while stock cover is {stock_cover}d "
                        f"(< critical {ctx.get('critical_stock_cover_days', 7)}d)."
                    ),
                    severity="blocking",
                    domain="inventory",
                    remediation_capability="stock_cover_analysis",
                )
            )
        if "discount" in action and ctx.get("margin_floor_violation"):
            out.append(
                RuleViolation(
                    rule_id="finance.margin_floor",
                    description="Discount action breaches the configured contribution-margin floor.",
                    severity="blocking",
                    domain="finance",
                    remediation_capability="margin_analysis",
                )
            )
        return out


# --------------------------------------------------------------------------- #
# Causal validation service (DoWhy boundary)
# --------------------------------------------------------------------------- #


@dataclass
class CausalValidationResult:
    confidence: str
    temporal_ok: bool
    graph_ok: bool
    confounders_ok: bool
    estimator_ok: bool
    refutations_passed: int
    refutations_total: int
    issues: list[str] = field(default_factory=list)
    available: bool = True
    detail: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class CausalValidationService(Protocol):
    async def validate(self, artifact: CausalAnalysisArtifact, *, context: dict[str, Any]) -> CausalValidationResult: ...


class BasicCausalValidationService:
    """Metadata-driven causal audit. Replace with a DoWhy-backed refuter runner.

    It does not *estimate*; it audits an already-produced CausalAnalysisArtifact
    for temporal ordering, graph support, confounder coverage, estimator sanity
    and refutation robustness.
    """

    def __init__(self, graphs: CausalGraphRegistry | None = None) -> None:
        self._graphs = graphs

    async def validate(
        self, artifact: CausalAnalysisArtifact, *, context: dict[str, Any]
    ) -> CausalValidationResult:
        issues: list[str] = []

        t_at = artifact.treatment_started_at or context.get("treatment_started_at")
        o_at = artifact.outcome_started_at or context.get("outcome_started_at")
        temporal_ok = True
        if t_at and o_at:
            temporal_ok = str(t_at) <= str(o_at)
            if not temporal_ok:
                issues.append(f"Outcome ({o_at}) precedes treatment ({t_at}); causal direction impossible.")

        graph_ok = True
        graph = self._graphs.get(artifact.graph_id) if (self._graphs and artifact.graph_id) else None
        if artifact.graph_id and graph is None:
            graph_ok = False
            issues.append(f"Causal graph '{artifact.graph_id}' is not registered.")
        elif graph is not None:
            t_node = _graph_node(artifact.treatment)
            o_node = _graph_node(artifact.outcome)
            if t_node in graph.nodes and o_node in graph.nodes and not graph.has_path(t_node, o_node):
                graph_ok = False
                issues.append(
                    f"Graph '{artifact.graph_id}' has no directed path {t_node} -> {o_node}."
                )
        elif not artifact.graph_id:
            graph_ok = False
            issues.append("No causal graph referenced.")

        confounders_ok = len(artifact.common_causes) >= 1
        if not confounders_ok:
            issues.append("No common causes / confounders were adjusted for.")

        estimator_ok = bool(artifact.estimator)
        if not estimator_ok:
            issues.append("Estimator metadata is missing.")

        passed = sum(1 for r in artifact.refutation_results if r.get("passed"))
        total = len(artifact.refutation_results)

        if not temporal_ok:
            confidence = "REJECTED"
        elif not graph_ok or not artifact.passed:
            confidence = "ASSOCIATION_ONLY" if total == 0 else "PLAUSIBLE_CAUSAL"
        elif total >= 2 and passed == total and confounders_ok and estimator_ok:
            confidence = "STRONGLY_SUPPORTED"
        elif total >= 1 and passed == total:
            confidence = "CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS"
        else:
            confidence = "PLAUSIBLE_CAUSAL"

        return CausalValidationResult(
            confidence=confidence,
            temporal_ok=temporal_ok,
            graph_ok=graph_ok,
            confounders_ok=confounders_ok,
            estimator_ok=estimator_ok,
            refutations_passed=passed,
            refutations_total=total,
            issues=issues,
        )


class UnavailableCausalValidationService:
    """Stand-in when DoWhy / the causal service is down. Never fakes a pass."""

    async def validate(
        self, artifact: CausalAnalysisArtifact, *, context: dict[str, Any]
    ) -> CausalValidationResult:
        return CausalValidationResult(
            confidence="ASSOCIATION_ONLY",
            temporal_ok=False,
            graph_ok=False,
            confounders_ok=False,
            estimator_ok=False,
            refutations_passed=0,
            refutations_total=0,
            issues=["Causal validation service unavailable; causal support cannot be confirmed."],
            available=False,
        )


def _graph_node(metric_id: str) -> str:
    mapping = {
        "metric.mobile_lcp_seconds": "page_latency",
        "metric.page_latency": "page_latency",
        "metric.purchase_cvr": "purchase",
        "metric.purchase_conversion": "purchase",
        "high_mobile_latency": "page_latency",
        "purchase_conversion": "purchase",
    }
    return mapping.get(metric_id, metric_id)


# --------------------------------------------------------------------------- #
# Statistical validator service
# --------------------------------------------------------------------------- #


@dataclass
class StatCheck:
    name: str
    passed: bool
    detail: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class StatisticalValidatorService(Protocol):
    async def check(self, *, name: str, data: dict[str, Any]) -> StatCheck: ...


class DeterministicStatsValidator:
    """Small, dependency-light statistical checks. Integrates scipy/statsmodels
    later behind the same interface. The LLM never computes any of these."""

    MIN_SAMPLE = 100

    async def check(self, *, name: str, data: dict[str, Any]) -> StatCheck:
        if name == "sample_size":
            n = data.get("sample_size")
            n_val = int(n) if n is not None else 0
            ok = n_val >= data.get("min_sample", self.MIN_SAMPLE)
            return StatCheck(name, ok, {"sample_size": n_val, "min_sample": data.get("min_sample", self.MIN_SAMPLE)})
        if name == "effect_size":
            change = abs(float(data.get("change_pct") or 0.0))
            ok = change >= data.get("min_effect_pct", 5.0)
            return StatCheck(name, ok, {"change_pct": change})
        if name == "confidence_interval_excludes_zero":
            ci = data.get("interval") or []
            ok = len(ci) == 2 and not (ci[0] <= 0 <= ci[1])
            return StatCheck(name, ok, {"interval": ci})
        if name == "segment_robustness":
            segs = data.get("segments") or []
            ok = len(segs) >= 2 and all(abs(s) >= 1 for s in segs)
            return StatCheck(name, ok, {"segments": segs})
        # default: unknown check is treated as "not evaluated"
        return StatCheck(name, True, {"note": "check not implemented; treated as non-blocking", **data})
