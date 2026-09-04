"""Conflict detection + deterministic arbitration — LLM never picks winners."""

from __future__ import annotations

import hashlib
from typing import Any, Literal

ConflictType = Literal[
    "DATA_CONTRADICTION",
    "METRIC_SEMANTIC_CONFLICT",
    "TIME_RANGE_CONFLICT",
    "SOURCE_CONFLICT",
    "METHODOLOGY_CONFLICT",
    "CAUSAL_CONFLICT",
    "MODEL_CONFLICT",
    "FACTUAL_CONFLICT",
]

# Conflicts that must be resolved (or explicitly accepted) before completion.
BLOCKING_TYPES: set[str] = {
    "DATA_CONTRADICTION",
    "METRIC_SEMANTIC_CONFLICT",
    "CAUSAL_CONFLICT",
    "MODEL_CONFLICT",
    "FACTUAL_CONFLICT",
}

# Prefer real / higher-trust provenance over synthetic when arbitrating.
_ORIGIN_RANK = {
    "MCP": 100,
    "MODEL": 80,
    "STATS": 70,
    "DERIVED": 50,
    "TEMPLATE": 20,
    "FIXTURE": 10,
}


def _cid(*parts: object) -> str:
    raw = "|".join(str(p) for p in parts)
    return f"CF-{hashlib.sha1(raw.encode()).hexdigest()[:10]}"


def _time_key(tr: Any) -> str:
    if not isinstance(tr, dict):
        return str(tr or "")
    return f"{tr.get('start')}|{tr.get('end')}|{tr.get('timezone') or ''}"


def detect_conflicts(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Detect material conflicts across evidence, hypotheses, forecasts, strategy."""
    conflicts: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(conflict: dict[str, Any]) -> None:
        cid = str(conflict["conflict_id"])
        if cid in seen:
            return
        seen.add(cid)
        conflict.setdefault("blocking", conflict.get("type") in BLOCKING_TYPES)
        conflict.setdefault("resolved", False)
        conflict.setdefault("resolution", None)
        conflicts.append(conflict)

    evidence = list(state.get("evidence") or [])

    # --- DATA_CONTRADICTION: same metric+dims+window, different values --------
    by_key: dict[str, list[dict[str, Any]]] = {}
    for e in evidence:
        key = (
            f"{e.get('metric_or_fact')}|{sorted((e.get('dimensions') or {}).items())}|"
            f"{_time_key(e.get('time_range'))}"
        )
        by_key.setdefault(key, []).append(e)
    for key, rows in by_key.items():
        values = {str(r.get("value")) for r in rows}
        if len(values) > 1 and len(rows) > 1:
            add(
                {
                    "conflict_id": _cid("data", key),
                    "type": "DATA_CONTRADICTION",
                    "artifact_refs": [r.get("artifact_id") for r in rows if r.get("artifact_id")],
                    "candidates": [
                        {
                            "artifact_id": r.get("artifact_id"),
                            "value": r.get("value"),
                            "source": r.get("source"),
                            "data_origin": r.get("data_origin"),
                            "synthetic": bool(r.get("synthetic")),
                        }
                        for r in rows
                    ],
                    "description": (
                        f"Conflicting values for {rows[0].get('metric_or_fact')}: {sorted(values)}"
                    ),
                }
            )

    # --- METRIC_SEMANTIC_CONFLICT: same alias family, different metric ids ----
    # e.g. metric.cac vs metric.blended_paid_cac both treated as primary CAC
    primary = (state.get("normalized_query") or {}).get("primary_metric")
    metric_ids = {
        str(e.get("metric_or_fact") or e.get("metric_id") or "")
        for e in evidence
        if e.get("metric_or_fact") or e.get("metric_id")
    }
    cac_family = {m for m in metric_ids if "cac" in m.lower()}
    if len(cac_family) > 1:
        add(
            {
                "conflict_id": _cid("semantic", *sorted(cac_family)),
                "type": "METRIC_SEMANTIC_CONFLICT",
                "artifact_refs": [
                    e.get("artifact_id")
                    for e in evidence
                    if str(e.get("metric_or_fact") or "").lower().find("cac") >= 0
                    and e.get("artifact_id")
                ],
                "metric_ids": sorted(cac_family),
                "preferred_metric": primary,
                "description": (
                    f"Multiple CAC metric identities in play: {sorted(cac_family)}"
                    + (f"; preferred={primary}" if primary else "")
                ),
            }
        )

    # --- TIME_RANGE_CONFLICT: same metric, overlapping incompatible windows ---
    by_metric: dict[str, list[dict[str, Any]]] = {}
    for e in evidence:
        mid = str(e.get("metric_or_fact") or "")
        if mid:
            by_metric.setdefault(mid, []).append(e)
    for mid, rows in by_metric.items():
        windows = {_time_key(r.get("time_range")) for r in rows if r.get("time_range")}
        if len(windows) > 1 and len(rows) > 1:
            # Only flag when values are compared as if contemporaneous (change_pct present)
            if any(r.get("change_pct") is not None or r.get("baseline") is not None for r in rows):
                add(
                    {
                        "conflict_id": _cid("time", mid, *sorted(windows)),
                        "type": "TIME_RANGE_CONFLICT",
                        "artifact_refs": [r.get("artifact_id") for r in rows if r.get("artifact_id")],
                        "time_ranges": sorted(windows),
                        "description": f"Incompatible time windows for {mid}: {sorted(windows)}",
                        "blocking": False,  # informational unless values also contradict
                    }
                )

    # --- SOURCE_CONFLICT: same fact from FIXTURE vs MCP with disagreement -----
    for key, rows in by_key.items():
        origins = {str(r.get("data_origin") or r.get("source") or "") for r in rows}
        if len(origins) > 1 and len({str(r.get("value")) for r in rows}) > 1:
            add(
                {
                    "conflict_id": _cid("source", key),
                    "type": "SOURCE_CONFLICT",
                    "artifact_refs": [r.get("artifact_id") for r in rows if r.get("artifact_id")],
                    "sources": sorted(origins),
                    "description": f"Sources disagree for {rows[0].get('metric_or_fact')}: {sorted(origins)}",
                }
            )

    # --- METHODOLOGY_CONFLICT: strategy action mismatches causal mechanism ----
    hyps = [
        h
        for h in (state.get("hypotheses") or [])
        if h.get("status") in {"retained", "proposed", "testing"}
    ]
    strategies = list(state.get("strategies") or [])
    for s in strategies:
        recommended = " ".join(str(x) for x in (s.get("recommended") or [])).lower()
        rationale = str(s.get("rationale") or "").lower()
        blob = f"{recommended} {rationale}"
        for h in hyps:
            statement = str(h.get("statement") or "").lower()
            # checkout/frontend regression vs media budget cut
            tech_cause = any(k in statement for k in ("checkout", "frontend", "latency", "js error", "deploy", "lcp"))
            media_action = any(k in blob for k in ("meta budget", "reduce spend", "cut cpm", "pause campaign", "ad spend"))
            if tech_cause and media_action:
                add(
                    {
                        "conflict_id": _cid("method", h.get("artifact_id"), s.get("artifact_id")),
                        "type": "METHODOLOGY_CONFLICT",
                        "artifact_refs": [
                            x
                            for x in (h.get("artifact_id"), s.get("artifact_id"))
                            if x
                        ],
                        "description": (
                            "Strategy action targets media while diagnosis points to a "
                            "technical/funnel mechanism"
                        ),
                        "blocking": True,
                    }
                )

    # --- CAUSAL_CONFLICT: multiple competing retained/open hypotheses ---------
    retained = [h for h in hyps if h.get("status") == "retained"]
    if len(retained) > 1:
        statements = {str(h.get("statement") or "") for h in retained}
        if len(statements) > 1:
            add(
                {
                    "conflict_id": _cid("causal", *sorted(statements)[:3]),
                    "type": "CAUSAL_CONFLICT",
                    "artifact_refs": [h.get("artifact_id") for h in retained if h.get("artifact_id")],
                    "description": "Multiple retained hypotheses compete as explanations",
                }
            )

    # --- MODEL_CONFLICT: numeric forecast with invalid/missing model ----------
    for pred in state.get("predictions") or state.get("forecasts") or []:
        model = pred.get("model") or {}
        drift = str(pred.get("drift_status") or "").lower()
        source = str(pred.get("source") or model.get("source") or "").lower()
        if pred.get("prediction") is not None and (
            drift in {"invalid", "drifted", "failed"}
            or source in {"insufficient", "unavailable"}
            or (not model and drift == "invalid")
        ):
            add(
                {
                    "conflict_id": _cid("model", pred.get("artifact_id") or pred.get("target")),
                    "type": "MODEL_CONFLICT",
                    "artifact_refs": [pred.get("artifact_id")] if pred.get("artifact_id") else [],
                    "description": (
                        f"Forecast for {pred.get('target')} blocked by model "
                        f"applicability/drift ({drift or source or 'missing metadata'})"
                    ),
                }
            )

    # --- FACTUAL / passthrough contradictions --------------------------------
    for c in state.get("contradictions") or []:
        if isinstance(c, dict) and not c.get("resolved"):
            add(
                {
                    "conflict_id": c.get("conflict_id") or _cid("exist", c.get("description"), len(conflicts)),
                    "type": c.get("type") or "FACTUAL_CONFLICT",
                    "artifact_refs": list(c.get("artifact_refs") or []),
                    "description": c.get("description") or str(c),
                    "blocking": bool(c.get("blocking", True)),
                }
            )

    return conflicts


def _rank_candidate(c: dict[str, Any]) -> tuple[int, int, str]:
    """Higher is better: non-synthetic, stronger origin, stable artifact id."""
    origin = str(c.get("data_origin") or "")
    origin_score = _ORIGIN_RANK.get(origin.upper(), 30)
    if c.get("synthetic"):
        origin_score -= 50
    source = str(c.get("source") or "")
    if "mcp" in source.lower() or source.upper() == "MCP":
        origin_score += 20
    return (origin_score, 0 if c.get("synthetic") else 1, str(c.get("artifact_id") or ""))


def arbitrate_conflict(conflict: dict[str, Any]) -> dict[str, Any]:
    """Deterministically resolve or mark unresolved — never LLM opinion."""
    out = dict(conflict)
    ctype = out.get("type")

    if out.get("resolved"):
        return out

    if ctype == "DATA_CONTRADICTION":
        candidates = list(out.get("candidates") or [])
        if candidates:
            winner = max(candidates, key=_rank_candidate)
            losers = [c for c in candidates if c.get("artifact_id") != winner.get("artifact_id")]
            # Only auto-resolve when winner clearly outranks (real over synthetic)
            if winner.get("synthetic") and any(not c.get("synthetic") for c in losers):
                # should not happen given ranking
                pass
            real = [c for c in candidates if not c.get("synthetic")]
            synth = [c for c in candidates if c.get("synthetic")]
            if real and synth and len({str(c.get("value")) for c in real}) == 1:
                out["resolved"] = True
                out["resolution"] = {
                    "action": "prefer_non_synthetic",
                    "winner": real[0].get("artifact_id"),
                    "discard": [c.get("artifact_id") for c in synth],
                    "reason": "Prefer non-synthetic evidence over fixture/template",
                }
            elif len(candidates) == 2 and _rank_candidate(candidates[0]) != _rank_candidate(candidates[1]):
                ranked = sorted(candidates, key=_rank_candidate, reverse=True)
                if _rank_candidate(ranked[0])[0] - _rank_candidate(ranked[1])[0] >= 40:
                    out["resolved"] = True
                    out["resolution"] = {
                        "action": "prefer_higher_provenance",
                        "winner": ranked[0].get("artifact_id"),
                        "discard": [ranked[1].get("artifact_id")],
                        "reason": "Higher-trust data_origin / non-synthetic wins",
                    }
        return out

    if ctype == "METRIC_SEMANTIC_CONFLICT":
        preferred = out.get("preferred_metric")
        metric_ids = list(out.get("metric_ids") or [])
        if preferred and preferred in metric_ids:
            out["resolved"] = True
            out["resolution"] = {
                "action": "prefer_resolved_metric",
                "winner": preferred,
                "discard": [m for m in metric_ids if m != preferred],
                "reason": "NormalizedQuery primary_metric is authoritative",
            }
        return out

    if ctype == "SOURCE_CONFLICT":
        # Defer to DATA_CONTRADICTION-style ranking if candidates present
        return arbitrate_conflict({**out, "type": "DATA_CONTRADICTION"})

    if ctype == "MODEL_CONFLICT":
        out["resolved"] = True
        out["resolution"] = {
            "action": "reject_forecast",
            "winner": None,
            "reason": "Invalid/drifted model forecasts are not accepted as claims",
        }
        # Still blocking for completion of predictive objectives, but marked handled
        out["blocking"] = True
        out["accepted_as_limitation"] = True
        return out

    if ctype == "METHODOLOGY_CONFLICT":
        out["resolved"] = True
        out["resolution"] = {
            "action": "reject_strategy",
            "winner": None,
            "reason": "Strategy must match diagnosed mechanism; media cut rejected for technical cause",
        }
        out["blocking"] = True
        out["accepted_as_limitation"] = True
        return out

    if ctype == "TIME_RANGE_CONFLICT":
        out["resolved"] = True
        out["resolution"] = {
            "action": "flag_comparability",
            "reason": "Recorded as limitation; does not alone block completion",
        }
        out["blocking"] = False
        return out

    if ctype == "CAUSAL_CONFLICT":
        # Keep unresolved until Skeptic / ranking selects one — do not invent winner
        out["resolution"] = {
            "action": "require_skeptic_or_ranking",
            "reason": "Multiple retained hypotheses need Skeptic/ falsification, not LLM choice",
        }
        return out

    return out


def arbitrate_conflicts(conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [arbitrate_conflict(c) for c in conflicts]


def unresolved_blocking(conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        c
        for c in conflicts
        if c.get("blocking") and not c.get("resolved") and not c.get("accepted_as_limitation")
    ]


def conflict_limitations(conflicts: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for c in conflicts:
        if c.get("accepted_as_limitation") or (c.get("resolved") and c.get("type") == "TIME_RANGE_CONFLICT"):
            res = c.get("resolution") or {}
            lines.append(f"Conflict [{c.get('type')}]: {res.get('reason') or c.get('description')}")
        elif c.get("blocking") and not c.get("resolved"):
            lines.append(f"Unresolved [{c.get('type')}]: {c.get('description')}")
    return lines
