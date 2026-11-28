from seleric_swarm.agent.model import is_reasoning_model


def test_gpt5_and_later_and_o_series_are_reasoning():
    for name in ("gpt-5-mini", "gpt-5.6-terra", "gpt-6.2-astra", "gpt-6.2-sol", "GPT-7", "o3-mini", "o4"):
        assert is_reasoning_model(name), name


def test_older_and_other_models_are_not():
    for name in ("gpt-4o", "gpt-4.1-mini", "gpt-4o-code", "Llama-4-Maverick-17B-128E-Instruct-FP8", "sonnet"):
        assert not is_reasoning_model(name), name
