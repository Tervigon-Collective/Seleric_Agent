from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from seleric_swarm.bootstrap import build_runtime
from seleric_swarm.config.settings import Settings
from seleric_swarm.eval.evaluators import (
    classify_exact_match,
    evidence_on_numeric_claims,
    load_jsonl,
    mcp_not_called_for_unsupported,
    numeric_exact_match,
    routing_exact_match,
    schema_valid,
)
from seleric_swarm.observability.langsmith_eval import maybe_create_experiment
from seleric_swarm.orchestration.runner import run_mission
from seleric_swarm.paths import repo_root


async def _run_lookup(live_llm: bool, judge: bool = False) -> dict:
    settings = Settings(
        llm_provider="azure_openai_compatible" if live_llm else "fake",
        langsmith_tracing=False,
        persistence_backend="memory",
        app_env="test",
    )
    runtime = build_runtime(settings)
    rows = load_jsonl("eval/datasets/lookup_commerce.jsonl")
    stats: Counter[str] = Counter()
    failures: list[dict] = []
    judge_notes: list[dict] = []
    for row in rows:
        expected = row["expected"]
        result = await run_mission(
            runtime,
            query=row["query"],
            timezone=row.get("scope", {}).get("timezone", "Asia/Kolkata"),
            as_of=row.get("scope", {}).get("as_of"),
        )
        raw = runtime.store.get_raw(result.mission_id) if hasattr(runtime.store, "get_raw") else {}
        checks = {
            "schema": schema_valid(result),
            "routing": routing_exact_match(result.query_class, result.mission_lead, expected),
            "numeric": numeric_exact_match(result, expected),
            "evidence_refs": evidence_on_numeric_claims(result),
            "mcp_policy": mcp_not_called_for_unsupported(raw or {}, expected),
        }
        stats["cases"] += 1
        for name, ok in checks.items():
            stats[name] += int(ok)
            if not ok:
                failures.append({"id": row.get("id"), "check": name, "status": result.status, "error": result.error})

        if judge and result.status == "completed" and result.claims and result.final_response:
            from seleric_swarm.eval.llm_judge import judge_synthesis

            verdict = await judge_synthesis(
                runtime.llm,
                query=row["query"],
                answer=result.final_response,
                claims=[c.model_dump() for c in result.claims],
                judge_model=settings.azure_openai_model,
            )
            stats["judge_cases"] += 1
            stats["judge_faithful"] += int(verdict.passed)
            if not verdict.passed:
                judge_notes.append({"id": row.get("id"), "rationale": verdict.rationale})

    report = {
        "stats": dict(stats),
        "failures": failures,
        "experiment_id": maybe_create_experiment("lookup_v1", dict(stats), settings),
    }
    if judge:
        report["judge_failures"] = judge_notes
    return report


async def _run_classify() -> dict:
    from seleric_swarm.agents.coordinator import Agent as CoordinatorAgent

    settings = Settings(llm_provider="fake", langsmith_tracing=False, persistence_backend="memory", app_env="test")
    runtime = build_runtime(settings)
    agent = CoordinatorAgent(runtime)
    rows = load_jsonl("eval/datasets/coordinator_classify.jsonl")
    matched = 0
    failures = []
    for row in rows:
        payload = await agent.classify(
            query=row["query"],
            timezone=row.get("scope", {}).get("timezone", "Asia/Kolkata"),
            as_of=row.get("scope", {}).get("as_of"),
            mission_id="eval",
            request_id="eval",
            session_id="eval",
            task_id="eval",
        )
        from seleric_swarm.contracts.lookup import CoordinatorClassificationV1, TimeRangeV1

        actual = CoordinatorClassificationV1(
            query_class=payload["query_class"],
            domain_lead=payload.get("mission_lead") or "coordinator_agent",
            time_range=TimeRangeV1.model_validate(payload.get("time_range") or {"kind": "none"}),
            metric_hints=payload.get("metric_hints") or [],
            unsupported_reason=payload.get("unsupported_reason"),
        )
        ok = classify_exact_match(actual, row["expected"])
        matched += int(ok)
        if not ok:
            failures.append({"id": row.get("id"), "actual": payload, "expected": row["expected"]})
    return {"cases": len(rows), "exact_match": matched, "failures": failures}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seleric deterministic eval (FakeLLM by default)")
    parser.add_argument("suite", nargs="?", default="lookup_v1")
    parser.add_argument("--live-llm", action="store_true", help="Use Azure adapter (opt-in, not CI)")
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Run the LLM-as-judge faithfulness check (requires --live-llm; never in CI)",
    )
    args = parser.parse_args(argv)
    if args.judge and not args.live_llm:
        parser.error("--judge requires --live-llm (the judge must not run against the fake adapter)")
    import asyncio

    if args.suite in {"lookup_v1", "lookup"}:
        report = asyncio.run(_run_lookup(live_llm=args.live_llm, judge=args.judge))
        classify = asyncio.run(_run_classify())
        payload = {"lookup": report, "classify": classify}
    else:
        payload = {"error": f"unknown suite {args.suite}"}
        print(json.dumps(payload, indent=2, default=str))
        return 2
    print(json.dumps(payload, indent=2, default=str))
    lookup_stats = report["stats"]
    cases = lookup_stats.get("cases") or 1
    numeric_ok = lookup_stats.get("numeric", 0) == cases
    schema_ok = lookup_stats.get("schema", 0) == cases
    classify_rate = classify["exact_match"] / max(classify["cases"], 1)
    judge_cases = lookup_stats.get("judge_cases", 0)
    baseline_path = repo_root() / "eval" / "baselines" / "lookup_v1.json"
    baseline_payload = {
        "numeric_exact_match": lookup_stats.get("numeric", 0) / cases,
        "schema_valid": lookup_stats.get("schema", 0) / cases,
        "routing_exact_match": lookup_stats.get("routing", 0) / cases,
        "classify_exact_match": classify_rate,
        "cases": cases,
        "notes": (
            "Regenerate with `make eval`. Do not start Anomaly/DoWhy "
            "(docs/22 Phase 2+) until numeric_exact_match remains 1.0."
        ),
    }
    if judge_cases:
        baseline_payload["synthesis_faithfulness"] = lookup_stats.get("judge_faithful", 0) / judge_cases
        baseline_payload["judge_cases"] = judge_cases
    baseline_path.write_text(json.dumps(baseline_payload, indent=2) + "\n", encoding="utf-8")

    faithfulness_ok = judge_cases == 0 or (lookup_stats.get("judge_faithful", 0) / judge_cases) >= 0.80
    if not numeric_ok or not schema_ok or classify_rate < 0.95 or not faithfulness_ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
