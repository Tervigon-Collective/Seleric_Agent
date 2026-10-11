"""M5.1: role config and versioned prompts."""

from __future__ import annotations

from seleric_swarm.config import llm_roles
from seleric_swarm.understand._prompt import INSTRUCTIONS


def test_roles_load_and_env_wins(monkeypatch) -> None:
    llm_roles.load_roles.cache_clear()
    assert llm_roles.role("understand").prompt == "v1"
    monkeypatch.setenv("AZURE_OPENAI_ANSWER_MODEL", "zz-deploy")
    assert llm_roles.role("answer").deployment == "zz-deploy"


def test_prompt_registry_serves_the_built_in_text() -> None:
    llm_roles.load_prompt.cache_clear()
    text, version = llm_roles.load_prompt("understand")
    assert version == "v1" and text == INSTRUCTIONS.strip()
    assert llm_roles.load_prompt("no_such_role") == (None, "")
