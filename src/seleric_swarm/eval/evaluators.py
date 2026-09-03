from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from seleric_swarm.contracts.lookup import CoordinatorClassificationV1, MissionResult
from seleric_swarm.paths import repo_root
from seleric_swarm.services.claim_gate import validate_claim


def load_jsonl(relpath: str) -> list[dict[str, Any]]:
    path = repo_root() / relpath
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def schema_valid(result: MissionResult) -> bool:
    try:
        MissionResult.model_validate(result.model_dump())
    except ValidationError:
        return False
    return True


def routing_exact_match(actual_class: str | None, actual_lead: str | None, expected: dict[str, Any]) -> bool:
    if expected.get("query_class") is not None and actual_class != expected["query_class"]:
        return False
    if expected.get("mission_lead") is not None and actual_lead != expected["mission_lead"]:
        return False
    return True


def numeric_exact_match(result: MissionResult, expected: dict[str, Any]) -> bool:
    if expected.get("status") in {"failed", "partial"} and expected.get("error_code") == "INSUFFICIENT_EVIDENCE":
        if result.error is None or result.error.code != "INSUFFICIENT_EVIDENCE":
            return False
        values = [row.value for row in result.evidence]
        return 0 not in values and 0.0 not in values
    if expected.get("value") is None:
        return True
    metric_id = expected.get("metric_id")
    target = expected["value"]
    for row in result.evidence:
        if metric_id and row.metric_or_fact != metric_id:
            continue
        if row.value == target:
            return True
        try:
            if float(row.value) == float(target):
                return True
        except (TypeError, ValueError):
            continue
    return False


def evidence_on_numeric_claims(result: MissionResult) -> bool:
    for claim in result.claims:
        if claim.claim_type == "numeric" and not claim.support_refs:
            return False
        if claim.claim_type == "numeric" and claim.gate_status != "passed":
            return False
    return True


def mcp_not_called_for_unsupported(raw: dict[str, Any], expected: dict[str, Any]) -> bool:
    if expected.get("query_class") == "unsupported" or expected.get("error_code") == "ROUTING_UNSUPPORTED":
        return not raw.get("mcp_called")
    return True


def classify_exact_match(actual: CoordinatorClassificationV1, expected: dict[str, Any]) -> bool:
    if actual.query_class != expected["query_class"]:
        return False
    if expected.get("domain_lead") and actual.domain_lead != expected["domain_lead"]:
        return False
    return True


def claim_gate_case(case: dict[str, Any]) -> bool:
    ok, problems = validate_claim(case["claim"])
    expected_ok = bool(case["expected"]["ok"])
    return ok == expected_ok and (not expected_ok or not problems)
