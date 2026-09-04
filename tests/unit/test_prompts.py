import pytest

from seleric_swarm.paths import repo_root
from seleric_swarm.prompts.registry import PromptRegistry


def test_prompt_registry_loads_pinned_versions():
    registry = PromptRegistry("prompts", "config/prompt_versions.yaml")
    spec = registry.load("coordinator.classify")
    assert spec.version == "1"
    assert spec.agent_id == "coordinator_agent"
    rendered = spec.render_user(
        {"query": "q", "timezone": "Asia/Kolkata", "as_of": "2026-09-03", "registry_catalog": "- metric.net_sales"}
    )
    assert "q" in rendered


def test_prompt_registry_rejects_unknown_variables():
    registry = PromptRegistry(repo_root() / "prompts", repo_root() / "config" / "prompt_versions.yaml")
    spec = registry.load("observer.metric_map")
    with pytest.raises(ValueError):
        spec.render_user(
            {
                "query": "q",
                "allowed_metric_ids": "metric.net_sales",
                "metric_hints": "",
                "extra": "nope",
            }
        )
