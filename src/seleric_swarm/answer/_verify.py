"""Structural verification of an AnswerDraft against the spec and the evidence (M3.2).

Every check reads typed objects; none reads prose for meaning. The only text check is
"digits outside placeholders", done by character scan.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from seleric_swarm.answer._evidence import Evidence, Item, resolve_claim
from seleric_swarm.answer._text import placeholders, stray_digits
from seleric_swarm.contracts.pipeline import (
    AnswerDraft,
    ChartBlock,
    Derivation,
    Paragraph,
    QuestionSpec,
    Ref,
    ResultSet,
    TableBlock,
)

# Claim role -> derive ops that may back it (None = a plain ResultSet row is fine too).
_ROLE_OPS: dict[str, frozenset[str]] = {
    "change": frozenset({"delta", "pct_change"}),
    "share": frozenset({"share"}),
}


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


def verify(draft: AnswerDraft, spec: QuestionSpec | None, evidence: Evidence) -> Verdict:
    v = Verdict()
    claim_ids = {c.claim_id for c in draft.claims}
    cited: dict[str, Item] = {}

    for claim in draft.claims:
        resolved = resolve_claim(evidence, claim)
        item = evidence.get(claim.ref) if claim.ref.kind != "evidence" else None
        if resolved is None:
            v.add("unresolved_claim", claim.claim_id)
            continue
        if item is not None:
            _collect(item, evidence, cited)
        ops = _ROLE_OPS.get(claim.role)
        if ops is not None and resolved.op not in ops:
            v.add("role_mismatch", f"{claim.claim_id}:{claim.role}")
        if claim.role == "total" and resolved.op not in (None, "sum") :
            v.add("role_mismatch", f"{claim.claim_id}:total")
        if claim.role == "total" and resolved.op is None and claim.row_key:
            v.add("role_mismatch", f"{claim.claim_id}:total_is_a_slice")

    for block in draft.blocks:
        if isinstance(block, Paragraph):
            if stray_digits(block.text):
                v.add("digits_outside_placeholders")
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

    sets = [i for i in cited.values() if isinstance(i, ResultSet)]
    findings = [i for i in cited.values() if isinstance(i, Derivation) and i.op.startswith("finding:")]
    if spec is not None and draft.status != "failed":
        if findings and not sets:
            # An investigation answer: its findings were computed over the spec's windows and
            # measure, so coverage is checked on the premise finding instead of result sets.
            premise = {str(f.params.get("metric_id")) for f in findings if f.op == "finding:premise"}
            for m in spec.measures:
                if m.metric_id and premise and m.metric_id not in premise:
                    v.add("measure_not_covered", m.metric_id)
        else:
            _coverage(spec, sets, v)
        excluded = {(f.dimension, frozenset(f.values)) for f in spec.filters if f.operator == "not_in"}
        for rs in sets:
            carried = {(f.dimension, frozenset(f.values)) for f in rs.filters if f.operator == "not_in"}
            if not excluded <= carried:
                v.add("exclusion_dropped", rs.ref.id)

    failed = [i for i in cited.values() if i.error is not None]
    if draft.status == "completed" and failed:
        v.add("completed_with_failed_evidence", ",".join(i.ref.id for i in failed))
    if draft.status == "partial" and not draft.limitations:
        v.add("partial_without_limitation")
    if draft.status != "failed" and not draft.claims and not any(isinstance(b, TableBlock) for b in draft.blocks):
        v.add("no_evidence")
    return v


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
        filtered = {x for rs in sets for f in rs.filters if f.dimension == e.dimension and f.operator == "in" for x in f.values}
        missing = [x for x in e.values if x not in seen and x not in filtered]
        if missing:
            v.add("entity_not_covered", f"{e.dimension}={','.join(missing)}")
