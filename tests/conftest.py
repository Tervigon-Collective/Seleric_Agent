from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load live Seleric MCP creds before blanking other test-only secrets.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

os.environ["LLM_PROVIDER"] = "fake"
os.environ["APP_ENV"] = "test"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["PERSISTENCE_BACKEND"] = "memory"
os.environ["AZURE_OPENAI_API_KEY"] = ""
os.environ["LANGSMITH_API_KEY"] = ""

import pytest

from seleric_swarm.bootstrap import build_runtime
from seleric_swarm.config.settings import Settings, get_settings


@pytest.fixture
def settings() -> Settings:
    get_settings.cache_clear()
    return Settings(
        llm_provider="fake",
        langsmith_tracing=False,
        persistence_backend="memory",
        app_env="test",
        azure_openai_api_key="",
        langsmith_api_key="",
    )


@pytest.fixture
def runtime(settings: Settings):
    rt = build_runtime(settings)
    if "seleric.metrics_query" not in rt.mcp.capabilities:
        reason = "SELERIC_MCP_URL/TOKEN not configured — live MCP required"
        if os.environ.get("CI"):
            pytest.fail(reason)
        pytest.skip(reason)
    return rt
