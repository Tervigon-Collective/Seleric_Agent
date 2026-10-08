"""Fast-path answer formatter for the business-state ready store.

Turns a pre-computed ``DomainStateSnapshot`` + the user's question into a
natural-language operator answer with a single LLM call -- no agent loop, no
MCP, no Cube. Reads only the snapshot the refresher already wrote
(business_state_ready_store.md "Query Fast Path").
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from seleric_swarm.config.settings import helper_chat_model
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata
from seleric_swarm.services.numeric_audit import unaudited_numbers

if TYPE_CHECKING:
    from seleric_swarm.runtime import SwarmRuntime
    from seleric_swarm.services.domain_health.models import DomainStateSnapshot

_SYSTEM_PROMPT = (
    "You are a business analytics assistant for operators and founders. "
    "Answer ONLY from the pre-computed metric snapshot provided as JSON -- never "
    "invent numbers, and if a value is missing say 'No data available'. Write for "
    "a busy operator:\n"
    "1. Lead with the answer in one plain sentence with the key number, rounded "
    "and with the local currency symbol/grouping.\n"
    "2. Show a compact table or a few bullets of only the metrics that matter.\n"
    "3. One footer line: 'Period: <the snapshot's period> · Currency: <the metrics' currency "
    "unit> · Data as of <date>'. The values are one day: never describe them as a range.\n"
    "4. End with one short follow-up question.\n"
    "Use business language (net revenue, MER, ROAS, CAC, contribution margin). "
    "Never expose metric ids, cube/table names, or internal plumbing."
)


def snapshot_age_hours(snapshot: DomainStateSnapshot) -> float | None:
    """Hours since the snapshot was computed, or None if unparseable."""
    try:
        computed = datetime.fromisoformat(snapshot.computed_at)
    except (ValueError, TypeError):
        return None
    if computed.tzinfo is None:
        computed = computed.replace(tzinfo=UTC)
    return (datetime.now(UTC) - computed).total_seconds() / 3600


def is_stale(snapshot: DomainStateSnapshot, *, max_age_hours: float = 2.0) -> bool:
    """A snapshot older than the freshness SLA (or with an unparseable/absent
    computed_at) is stale -- the fast path falls back to the agent loop."""
    age = snapshot_age_hours(snapshot)
    return age is None or age > max_age_hours


def _snapshot_context(snapshot: DomainStateSnapshot, units: dict[str, str] | None = None) -> dict:
    """Compact, LLM-friendly view of the snapshot -- metric ids kept internal-
    only inputs; only the numbers the model needs to phrase the answer.

    The period, the change's baseline and each metric's unit are stated, not left
    to the model: live 2026-10-07 (MS3-e81cbc105a) it wrote "Period: 2026-09-30
    to 2026-10-06" for one day's values (it read the rolling-mean ``window``) and
    "Currency: $" for INR."""
    units = units or {}
    metrics = []
    for m in snapshot.metrics:
        entry: dict = {
            "metric": m.metric_id.removeprefix("metric."),
            "value": m.value,
            "unit": units.get(m.metric_id),
            "change_pct_vs_prior_day": m.period_delta_pct,
            "avg_7d": m.rolling_mean_7d,
            "freshness": m.freshness,
        }
        if m.anomaly:
            entry["anomaly"] = {
                "is_anomaly": m.anomaly.get("is_anomaly"),
                "expected": m.anomaly.get("expected"),
                "direction": m.anomaly.get("direction"),
            }
        metrics.append(entry)
    return {
        "as_of": snapshot.as_of,
        "period": snapshot.as_of,
        "values_are": f"one day ({snapshot.as_of}); change_pct_vs_prior_day compares it with the day before",
        "avg_7d_window": snapshot.window,
        "status": snapshot.status,
        "signals": snapshot.headline_signals,
        "metrics": metrics,
    }


def _units(runtime: SwarmRuntime, snapshot: DomainStateSnapshot) -> dict[str, str]:
    registry = getattr(runtime, "metrics", None)
    out: dict[str, str] = {}
    for m in snapshot.metrics:
        definition = registry.get(m.metric_id) if registry is not None else None
        if definition is not None and definition.unit:
            out[m.metric_id] = definition.unit
    return out


async def format_business_state(
    runtime: SwarmRuntime,
    *,
    question: str,
    snapshot: DomainStateSnapshot,
    request_id: str | None = None,
    session_id: str | None = None,
) -> str:
    """One fast-tier LLM call: snapshot + question -> operator answer.

    Every number the LLM writes is audited against the snapshot's own values
    (``numeric_audit.unaudited_numbers``). If the model fabricates a figure the
    snapshot can't back, we retry once, then fall back to a deterministic
    summary that is auditable by construction -- the fast path never returns an
    unbacked number (same evidence discipline as the agent synthesis path)."""
    settings = runtime.settings
    model = helper_chat_model(settings)
    context = json.dumps(_snapshot_context(snapshot, _units(runtime, snapshot)), default=str, separators=(",", ":"))
    allowed = _allowed_tokens(snapshot)

    async def _ask(extra_instruction: str = "") -> str:
        response = await runtime.llm.complete(
            LLMRequest(
                messages=[
                    ChatMessage(role="system", content=_SYSTEM_PROMPT + extra_instruction),
                    ChatMessage(role="user", content=f"Snapshot:\n{context}\n\nQuestion: {question}"),
                ],
                model=model,
                temperature=0,
                # Room for reasoning models (gpt-5*/o-series) whose hidden
                # reasoning tokens count against this budget — too low returns
                # empty visible content.
                max_tokens=2000,
                timeout_s=settings.llm_timeout_s,
                metadata=LLMRequestMetadata(
                    request_id=request_id,
                    session_id=session_id,
                    agent_id="business_state_formatter",
                    query_class="high_level_lookup",
                ),
                tags=["business_state_fast_path"],
            )
        )
        return response.text

    answer = await _ask()
    if answer.strip() and not unaudited_numbers(answer, [], extra=allowed, query_text=question):
        return answer
    # Empty output (reasoning model exhausted its budget) or a number the
    # snapshot can't back -- retry once, stricter.
    answer = await _ask(
        "\n\nCRITICAL: use ONLY the exact numeric values present in the snapshot. "
        "Do not compute, derive, or round to new figures."
    )
    if answer.strip() and not unaudited_numbers(answer, [], extra=allowed, query_text=question):
        return answer
    return _deterministic_summary(snapshot)


def _rounding_variants(value: float) -> set[str]:
    """A value plus the readable forms an operator answer legitimately uses:
    integer, 1-2 dp, and percent-scaled (for ratios shown as %)."""
    out: set[str] = set()
    for v in (value, value * 100):  # raw and percent-scaled
        for r in (v, round(v), round(v, 1), round(v, 2)):
            out.add(str(r))
            out.add(str(abs(r)))
            if isinstance(r, float) and r.is_integer():
                out.add(str(int(r)))
                out.add(str(abs(int(r))))
        # Fixed decimals as written: "1.30" and "70926.20" are 1.3 and 70926.2 —
        # string-matched, they failed the audit twice and every overview fell back
        # to the raw list (live 2026-10-08).
        for places in (1, 2):
            out.add(f"{v:.{places}f}")
            out.add(f"{abs(v):.{places}f}")
    return out


def _allowed_tokens(snapshot: DomainStateSnapshot) -> list[str]:
    tokens: set[str] = set()
    for m in snapshot.metrics:
        for v in (m.value, m.period_delta_pct, m.rolling_mean_7d, m.rolling_std_7d):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                tokens |= _rounding_variants(float(v))
        if m.anomaly:
            exp = m.anomaly.get("expected")
            if isinstance(exp, (int, float)) and not isinstance(exp, bool):
                tokens |= _rounding_variants(float(exp))
    if snapshot.as_of:
        tokens.update(snapshot.as_of.split("-"))
        tokens.add(snapshot.as_of.replace("-", ""))
    return [t for t in tokens if t]


def _deterministic_summary(snapshot: DomainStateSnapshot) -> str:
    """Auditable-by-construction fallback: exact snapshot values only."""
    lines = ["Here's the latest business snapshot:", ""]
    for m in snapshot.metrics:
        label = m.metric_id.removeprefix("metric.").replace("_", " ")
        if m.value is None:
            lines.append(f"- {label}: No data available")
            continue
        delta = f" ({m.period_delta_pct:+.1f}% vs prior day)" if m.period_delta_pct is not None else ""
        lines.append(f"- {label}: {m.value:,.2f}{delta}")
    lines.append("")
    lines.append(f"Data as of {snapshot.as_of}.")
    return "\n".join(lines)
