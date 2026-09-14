from unittest.mock import MagicMock

from seleric_swarm.agents.strategy.reasoning import LLMPortReasoningModel, NullReasoningModel
from seleric_swarm.agents.strategy.swarm_bridge import SwarmStrategySpecialist


def test_strategy_uses_primary_model_when_azure_openai_model_empty():
    runtime = MagicMock()
    runtime.settings.azure_openai_model = ""
    runtime.settings.primary_model = lambda: "DeepSeek-V4-Flash"
    runtime.llm = MagicMock()
    deps = SwarmStrategySpecialist(runtime=runtime)._deps_for_run()
    assert isinstance(deps.reasoning, LLMPortReasoningModel)

    empty = SwarmStrategySpecialist(runtime=None)._deps_for_run()
    assert isinstance(empty.reasoning, NullReasoningModel)
