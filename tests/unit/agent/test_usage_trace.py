from types import SimpleNamespace

from seleric_swarm.agent.usage_trace import load_prices, usage_trace


def _usage(i=1000, o=500):
    return SimpleNamespace(input_tokens=i, output_tokens=o, requests=3, tool_calls=2)


def test_no_usage_is_none():
    assert usage_trace(None) is None


def test_tokens_exported_and_cost_null_without_price():
    out = usage_trace(_usage(), models=["m"], prices={})
    assert out["total_tokens"] == 1500 and out["requests"] == 3 and out["tool_calls"] == 2
    assert out["cost_usd"] is None


def test_cost_from_configured_price_only():
    prices = {"m": {"input_per_mtok": 2.0, "output_per_mtok": 8.0}}
    assert usage_trace(_usage(1_000_000, 500_000), models=["m"], prices=prices)["cost_usd"] == 6.0
    assert usage_trace(_usage(), models=["other"], prices=prices)["cost_usd"] is None


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


def test_cost_null_when_several_models_serve_the_mission():
    prices = {"a": {"input_per_mtok": 1.0, "output_per_mtok": 1.0}, "b": {"input_per_mtok": 1.0, "output_per_mtok": 1.0}}
    out = usage_trace(_usage(), models=["a", "b"], prices=prices)
    assert out["cost_usd"] is None and out["models"] == ["a", "b"]


def test_models_in_play_reports_split_and_fallbacks():
    from seleric_swarm.agent.usage_trace import models_in_play

    one = SimpleNamespace(azure_openai_model="m1")
    assert models_in_play(one) == ["m1"]
    split = SimpleNamespace(azure_openai_model="m1", azure_openai_tool_model="t", azure_openai_answer_model="a")
    assert models_in_play(split) == ["t", "a"]
    chain = SimpleNamespace(azure_openai_model="m1", llm_fallback_models="m2, m3")
    assert models_in_play(chain) == ["m1", "m2", "m3"]
