"""POST /v1/query/enhance — fast composer rewrite."""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from seleric_swarm.api.conversations import router as conversations_router
from seleric_swarm.api.security import ApiSecurityMiddleware
from seleric_swarm.llm.port import LLMResponse, TokenUsage

API_KEY = "test-enhance-key"


class _Settings:
    azure_openai_helper_model = "helper"
    azure_openai_fast_model = ""
    llm_timeout_s = 30.0

    def primary_model(self) -> str:
        return "primary"


class _Runtime:
    def __init__(self) -> None:
        self.settings = _Settings()
        self.metrics = None
        self.bootstrap = None
        self.llm = AsyncMock()
        self.llm.complete.return_value = LLMResponse(
            text="Which product variants had the highest returned units in the last 7 days?",
            model="helper",
            usage=TokenUsage(),
        )


def _build_client(runtime: _Runtime) -> TestClient:
    app = FastAPI()
    app.state.runtime_provider = lambda: runtime
    app.include_router(conversations_router)
    app.add_middleware(
        ApiSecurityMiddleware,
        api_key=API_KEY,
        rate_limit_enabled=False,
        default_workspace_id="default",
        default_user_id="default",
    )
    return TestClient(app)


def _auth() -> dict[str, str]:
    return {"x-api-key": API_KEY}


def test_enhances_ambiguous_query() -> None:
    runtime = _Runtime()
    client = _build_client(runtime)
    response = client.post(
        "/v1/query/enhance",
        headers=_auth(),
        json={"query": "which are the products with are highest return which variants"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["original"].startswith("which are the products")
    assert "returned units" in body["enhanced"].lower()
    assert runtime.llm.complete.await_count == 1


def test_requires_authentication() -> None:
    client = _build_client(_Runtime())
    assert client.post("/v1/query/enhance", json={"query": "sales"}).status_code == 401


def test_rejects_empty_query() -> None:
    client = _build_client(_Runtime())
    response = client.post("/v1/query/enhance", headers=_auth(), json={"query": "   "})
    assert response.status_code == 422


def test_reports_model_failure() -> None:
    runtime = _Runtime()
    runtime.llm.complete.side_effect = RuntimeError("boom")
    client = _build_client(runtime)
    response = client.post(
        "/v1/query/enhance",
        headers=_auth(),
        json={"query": "give me ad performance"},
    )
    assert response.status_code == 502
    assert "failed" in response.json()["detail"].lower()
