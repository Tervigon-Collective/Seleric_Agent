"""Structural verification of an AnswerDraft against the spec and the evidence (M3.2 / AG-08).

Every check reads typed objects; none reads prose for meaning. The only text check is
"digits outside placeholders", done by character scan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from seleric_swarm.answer._evidence import Evidence, Item, resolve_claim
from seleric_swarm.answer._text import placeholders, stray_digits
from seleric_swarm.contracts.pipeline import (
    AnswerDraft,
    ChartBlock,
    CoverageBlock,
    Derivation,
    FunnelChartBlock,
    InsightBlock,
    KpiCardBlock,
    KpiGridBlock,
    Paragraph,
    QuestionSpec,
    RankingChartBlock,
    RankingTableBlock,
    Ref,
    ResultSet,
    TableBlock,
    TimeSeriesChartBlock,
)

if TYPE_CHECKING:
    from seleric_swarm.answer._assemble import ReportSkeleton

# Claim role -> derive ops that may back it (None = a plain ResultSet row is fine too).
_ROLE_OPS: dict[str, frozenset[str]] = {
    "change": frozenset({
        "delta", "pct_change",
        "finding:premise", "finding:component", "finding:segment", "finding:driver",
    }),
    "share": frozenset({"share", "funnel_stages"}),
}

_INTERNAL_ID = ("q", "d", "e")


@dataclass(frozen=True)
class Issue:
    code: str
    detail: str = ""


@dataclass
class Verdict:
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def codes(self) -> list[str]:
        return [i.code for i in self.issues]

    def add(self, code: str, detail: str = "") -> None:
        self.issues.append(Issue(code, detail))


def verify(
    draft: AnswerDraft,
    spec: QuestionSpec | None,
    evidence: Evidence,
    *,
    skeleton: ReportSkeleton | None = None,
) -> Verdict:
    v = Verdict()
    claim_ids = {c.claim_id for c in draft.claims}
    cited: dict[str, Item] = {}

    for claim in draft.claims:
        resolved = resolve_claim(evidence, claim)
        item = evidence.get(claim.ref)
        if resolved is None:
            v.add("unresolved_claim", claim.claim_id)
            continue
        if item is not None:
            _collect(item, evidence, cited)
        ops = _ROLE_OPS.get(claim.role)
        if ops is not None and resolved.op not in ops:
            v.add("role_mismatch", f"{claim.claim_id}:{claim.role}")
        if claim.role == "total" and resolved.op not in (None, "sum"):
            v.add("role_mismatch", f"{claim.claim_id}:total")
        if claim.role == "total" and resolved.op is None and claim.row_key:
            v.add("role_mismatch", f"{claim.claim_id}:total_is_a_slice")

    for block in draft.blocks:
        if isinstance(block, Paragraph):
            if stray_digits(block.text):
                v.add("digits_outside_placeholders")
            if _has_internal_ids(block.text):
                v.add("internal_ids_in_text")
            for ph in placeholders(block.text):
                if ph not in claim_ids:
                    v.add("unknown_placeholder", ph)
        elif isinstance(block, TableBlock):
            item = evidence.get(block.result_set)
            if not isinstance(item, ResultSet):
                v.add("unresolved_table", block.result_set.id)
            else:
                _collect(item, evidence, cited)
        elif isinstance(block, ChartBlock):
            for ref in block.refs:
                item = evidence.get(ref)
                if item is None:
                    v.add("unresolved_chart", ref.id)
                else:
                    _collect(item, evidence, cited)
        elif isinstance(block, KpiGridBlock):
            for card in block.cards:
                _verify_card(card, evidence, cited, v)
        elif isinstance(block, KpiCardBlock):
            _verify_card(block, evidence, cited, v)
        elif isinstance(block, (FunnelChartBlock, TimeSeriesChartBlock)):
            for ref in block.refs:
                item = evidence.get(ref)
                if item is None:
                    v.add("unresolved_chart", ref.id)
                else:
                    _collect(item, evidence, cited)
                    _reconcile_chart_rows(item, v)
        elif isinstance(block, RankingChartBlock):
            item = evidence.get(block.ref)
            if item is None:
                v.add("unresolved_chart", block.ref.id)
            else:
                _collect(item, evidence, cited)
                _reconcile_chart_rows(item, v)
        elif isinstance(block, RankingTableBlock):
            item = evidence.get(block.result_set)
            if not isinstance(item, ResultSet):
                v.add("unresolved_table", block.result_set.id)
            else:
                _collect(item, evidence, cited)
        elif isinstance(block, (InsightBlock, CoverageBlock)):
            pass

    sets = [i for i in cited.values() if isinstance(i, ResultSet)]
    findings = [i for i in cited.values() if isinstance(i, Derivation) and i.op.startswith("finding:")]
    if spec is not None and draft.status != "failed":
        if findings and not sets:
            premise = {str(f.params.get("metric_id")) for f in findings if f.op == "finding:premise"}
            for m in spec.measures:
                if m.metric_id and premise and m.metric_id not in premise:
                    v.add("measure_not_covered", m.metric_id)
        elif skeleton is None:
            # Multi-section reports cover measures across sections; skip strict
            # single-spec coverage when a skeleton already assembled them.
            _coverage(spec, sets, v)
        excluded = {(f.dimension, frozenset(f.values)) for f in spec.filters if f.operator == "not_in"}
        for rs in sets:
            carried = {(f.dimension, frozenset(f.values)) for f in rs.filters if f.operator == "not_in"}
            if not excluded <= carried:
                v.add("exclusion_dropped", rs.ref.id)

    if draft.next_step and _asks_for_uploadable_data(draft.next_step):
        v.add("next_step_asks_for_data")

    failed = [i for i in cited.values() if i.error is not None]
    if draft.status == "completed" and failed:
        v.add("completed_with_failed_evidence", ",".join(i.ref.id for i in failed))
    if draft.status == "partial" and not draft.limitations:
        v.add("partial_without_limitation")
    has_structured = any(
        isinstance(
            b,
            (
                TableBlock,
                ChartBlock,
                KpiGridBlock,
                KpiCardBlock,
                FunnelChartBlock,
                RankingChartBlock,
                RankingTableBlock,
                TimeSeriesChartBlock,
            ),
        )
        for b in draft.blocks
    ) or (skeleton is not None and bool(skeleton.blocks))
    if draft.status != "failed" and not draft.claims and not has_structured:
        v.add("no_evidence")
    return v


def _verify_card(card: KpiCardBlock, evidence: Evidence, cited: dict[str, Item], v: Verdict) -> None:
    resolved = evidence.resolve(card.ref, card.row_key)
    item = evidence.get(card.ref)
    if resolved is None:
        # Missing values are allowed as explanatory states; do not fail the draft.
        return
    if item is not None:
        _collect(item, evidence, cited)
    if card.unit and resolved.unit and card.unit != resolved.unit:
        v.add("unit_mismatch", f"{card.label}:{card.unit}!={resolved.unit}")


def _reconcile_chart_rows(item: Item, v: Verdict) -> None:
    """Every chart-backed row must carry an evidence id (grounding)."""
    for row in item.rows:
        if not row.evidence_id:
            v.add("chart_row_ungrounded", item.ref.id)


def _has_internal_ids(text: str) -> bool:
    for token in text.replace(",", " ").replace(".", " ").split():
        if len(token) <= 4 and token[:1] in _INTERNAL_ID and token[1:].isdigit():
            return True
        if token.startswith("s") and "." in token:
            left, _, right = token.partition(".")
            if left[1:].isdigit() and right[:1] in _INTERNAL_ID and right[1:].isdigit():
                return True
    return False


def _asks_for_uploadable_data(next_step: str) -> bool:
    lowered = next_step.casefold()
    needles = (
        "upload",
        "provide the data",
        "send me the csv",
        "paste the numbers",
        "export from",
        "give me the raw",
    )
    return any(n in lowered for n in needles)


def _collect(item: Item, evidence: Evidence, out: dict[str, Item], depth: int = 0) -> None:
    if item.ref.id in out or depth > 16:
        return
    out[item.ref.id] = item
    if isinstance(item, Derivation):
        for ref in item.inputs:
            child = evidence.get(ref) if isinstance(ref, Ref) else None
            if child is not None:
                _collect(child, evidence, out, depth + 1)


def _coverage(spec: QuestionSpec, sets: list[ResultSet], v: Verdict) -> None:
    metrics = {rs.metric_id for rs in sets}
    for m in spec.measures:
        if m.metric_id and m.metric_id not in metrics:
            v.add("measure_not_covered", m.metric_id)
    roles = {rs.window.role for rs in sets}
    for w in spec.windows:
        if w.role not in roles:
            v.add("window_not_covered", w.role)
    dims = {d for rs in sets for d in rs.breakdowns}
    for b in spec.breakdowns:
        if b not in dims:
            v.add("breakdown_not_covered", b)
    for e in spec.entities:
        seen = {row.dimensions.get(e.dimension) for rs in sets for row in rs.rows}
        filtered = {
            x
            for rs in sets
            for f in rs.filters
            if f.dimension == e.dimension and f.operator == "in"
            for x in f.values
        }
        missing = [x for x in e.values if x not in seen and x not in filtered]
        if missing:
            v.add("entity_not_covered", f"{e.dimension}={','.join(missing)}")
