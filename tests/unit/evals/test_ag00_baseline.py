"""AG-00 baseline: routes, fallback reasons, numerical and scope correctness.

These pin the reliability baseline the rest of the program builds on: a
successful mission status is never treated as evidence correctness, a correct
value on the right window passes, and a wrong value or window fails.
"""

from __future__ import annotations

from seleric_swarm.evals import EvalCase, ObservedCase, ObservedEvidence, run_suite
from seleric_swarm.evals._runner import _observed_from_payload
from seleric_swarm.evals._scorer import ResolvedTruth, score_case, value_correctness

# The number that actually failed the suite: same metric, same window, but the
# evidence value is a JSON round-trip of the truth (1 ULP apart).
TRUTH = 97480.96143241807
EVIDENCE = 97480.9614324181
SEP2 = {"start": "2026-09-02T00:00:00+05:30", "end": "2026-09-02T00:00:00+05:30"}
OCT9 = {"start": "2026-10-09T00:00:00+05:30", "end": "2026-10-09T00:00:00+05:30"}

CASE = EvalCase.model_validate(
    {
        "id": "lookup-net-sales-yesterday",
        "turns": ["What were net sales yesterday?"],
        "tags": ["lookup"],
        "expect": {
            "status": "completed",
            "values": [
                {
                    "label": "net sales",
                    "truth": {"metric_id": "net_sales", "start": "2026-09-02", "end": "2026-09-02"},
                    "value": TRUTH,
                }
            ],
        },
    }
)


def _observed(value: float | None, window: dict[str, str] | None = SEP2) -> ObservedCase:
    evidence = () if value is None else (
        ObservedEvidence(metric_id="net_sales", value=value, window=window or {}),
    )
    return ObservedCase(id=CASE.id, status="completed", evidence=evidence)


def _score(observed: ObservedCase) -> float | None:
    truth = ResolvedTruth(
        label="net sales", expected=TRUTH, tolerance=0.0, match=dict(CASE.expect.values[0].truth)
    )
    return value_correctness(observed, [truth])


# ---------------------------------------------------------------- routes (T01-T03)
def test_ag00_t01_v2_answered_route_reported() -> None:
    payload = {"status": "completed", "trace": {"v2_mission": {"status": "answered"}}}
    obs = _observed_from_payload(CASE, "q", payload)
    assert obs.route == "v2"
    assert obs.fallback_reason is None
    assert obs.diagnostics["route"] == "v2"


def test_ag00_t02_unsupported_records_fallback_reason() -> None:
    payload = {"status": "completed", "trace": {"v2_mission": {"status": "fallback", "reason": "no_template"}}}
    obs = _observed_from_payload(CASE, "q", payload)
    assert obs.route == "legacy"
    assert obs.fallback_reason == "no_template"


def test_ag00_t02b_clarification_route() -> None:
    payload = {"status": "completed", "trace": {"v2_mission": {"status": "clarify"}}}
    obs = _observed_from_payload(CASE, "q", payload)
    assert obs.route == "v2_clarify"


def test_ag00_t03_failed_when_no_fallback() -> None:
    payload = {"status": "failed", "trace": {"v2_mission": {"status": "fallback", "reason": "no_data"}}}
    obs = _observed_from_payload(CASE, "q", payload)
    assert obs.route == "failed"
    assert obs.fallback_reason == "no_data"
    # A completed mission with no typed trace is not reported as the typed path.
    shadow_only = _observed_from_payload(CASE, "q", {"status": "completed", "trace": {"v2_shadow": {"status": "ok"}}})
    assert shadow_only.route == "v2_shadow"


def test_ag00_t03b_legacy_route_is_not_v2() -> None:
    obs = _observed_from_payload(CASE, "q", {"status": "completed", "trace": {"steps": []}})
    assert obs.route == "legacy"


# ---------------------------------------------------------------- values (T04-T07)
def test_ag00_t04_correct_value_and_scope_passes() -> None:
    assert _score(_observed(EVIDENCE)) == 1.0


def test_ag00_t04b_identical_value_float_roundtrip_passes() -> None:
    # Regression for the suite-wide value_correctness=0: a 1-ULP representation
    # difference of the same number must not fail.
    assert _score(_observed(TRUTH)) == 1.0
    assert _score(_observed(97480.9614324181)) == 1.0


def test_ag00_t05_incorrect_value_fails() -> None:
    assert _score(_observed(68877.0)) == 0.0


def test_ag00_t06_incorrect_window_fails() -> None:
    assert _score(_observed(TRUTH, window=OCT9)) == 0.0


def test_ag00_t07_missing_evidence_cannot_pass() -> None:
    observed = _observed(None)
    assert value_correctness(
        observed,
        [ResolvedTruth(label="net sales", expected=TRUTH, tolerance=0.0, match={"metric_id": "net_sales"})],
    ) == 0.0
    truth = ResolvedTruth(
        label="net sales", expected=TRUTH, tolerance=0.0, match=dict(CASE.expect.values[0].truth)
    )
    score = score_case(CASE, observed, [truth])
    assert score.metrics["value_correctness"] == 0.0
    assert score.details["truths"][0]["reason"] == "missing"
    assert score.details["truths"][0]["matched"] is False


def test_ag00_t07b_miss_reason_is_recorded() -> None:
    score = score_case(CASE, _observed(68877.0), [
        ResolvedTruth(label="net sales", expected=TRUTH, tolerance=0.0, match=dict(CASE.expect.values[0].truth))
    ])
    assert score.details["truths"][0]["reason"] == "value_mismatch"
    assert score.details["truths"][0]["observed"] == [68877.0]


# ---------------------------------------------------------------- costs (T08-T09)
def test_ag00_t08_missing_latency_is_unavailable_not_zero() -> None:
    from seleric_swarm.evals._runner import _v2_costs

    latency, tokens, calls = _v2_costs({})
    assert latency is None and tokens is None and calls is None
    obs = _observed_from_payload(CASE, "q", {"status": "completed", "trace": {}})
    assert obs.latency_ms is None
    assert obs.llm_calls is None
    assert obs.tokens is None


def test_ag00_t09_complete_route_and_cost_trace() -> None:
    payload = {
        "status": "completed",
        "trace": {
            "v2_mission": {
                "status": "answered",
                "latency_ms": 1234.0,
                "understand": {"attempts": 1, "input_tokens": 100, "output_tokens": 20},
                "answer": {"attempts": 2, "input_tokens": 300, "output_tokens": 80},
            }
        },
    }
    obs = _observed_from_payload(CASE, "q", payload)
    assert obs.route == "v2"
    assert obs.latency_ms == 1234.0
    assert obs.tokens == 500
    assert obs.llm_calls == 3


async def test_ag00_t09b_suite_report_carries_route_and_costs(tmp_path) -> None:
    def runner(case: EvalCase) -> ObservedCase:
        return ObservedCase(
            id=case.id,
            status="completed",
            evidence=(ObservedEvidence(metric_id="net_sales", value=EVIDENCE, window=SEP2),),
            route="v2",
            latency_ms=1000.0,
            tokens=500,
            llm_calls=2,
        )

    report = await run_suite([CASE], "replay", runner=runner, out_dir=tmp_path)
    case = report.score.cases[0]
    assert case.metrics["value_correctness"] == 1.0
    assert case.details["route"] == "v2"
    assert case.metrics["latency_ms"] == 1000.0
    assert case.metrics["tokens"] == 500
    assert case.metrics["llm_calls"] == 2
    assert (tmp_path / "report.md").exists()
