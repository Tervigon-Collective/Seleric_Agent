from types import SimpleNamespace

from seleric_swarm.agent.usage_trace import load_prices, usage_trace


def _usage(i=1000, o=500):
    return SimpleNamespace(input_tokens=i, output_tokens=o, requests=3, tool_calls=2)


def test_no_usage_is_none():
    assert usage_trace(None) is None


def test_tokens_exported_and_cost_null_without_price():
    out = usage_trace(_usage(), model="m", prices={})
    assert out["total_tokens"] == 1500 and out["requests"] == 3 and out["tool_calls"] == 2
    assert out["cost_usd"] is None


def test_cost_from_configured_price_only():
    prices = {"m": {"input_per_mtok": 2.0, "output_per_mtok": 8.0}}
    assert usage_trace(_usage(1_000_000, 500_000), model="m", prices=prices)["cost_usd"] == 6.0
    assert usage_trace(_usage(), model="other", prices=prices)["cost_usd"] is None


def test_shipped_price_file_is_empty_not_guessed(tmp_path):
    assert load_prices() == {}
    f = tmp_path / "p.yaml"
    f.write_text("models:\n  a: {input_per_mtok: 1, output_per_mtok: 2}\n  b: {input_per_mtok: x}\n")
    assert load_prices(f) == {"a": {"input_per_mtok": 1.0, "output_per_mtok": 2.0}}


def test_eval_runner_reads_agent_loop_usage():
    from seleric_swarm.evals._case import EvalCase
    from seleric_swarm.evals._runner import _observed_from_payload

    payload = {"status": "completed", "trace": {"usage": {"total_tokens": 900, "requests": 4, "cost_usd": 0.01}}}
    case = EvalCase.model_validate({"id": "c1", "turns": ["q"]}) if hasattr(EvalCase, "model_validate") else None
    obs = _observed_from_payload(case, "q", payload)
    assert (obs.tokens, obs.llm_calls, obs.cost_usd) == (900, 4, 0.01)
