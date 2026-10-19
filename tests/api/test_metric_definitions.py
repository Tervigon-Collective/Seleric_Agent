"""GET /v1/metrics/definitions — governed definitions for the evidence inspector.

Read-only projection of the runtime MetricRegistry. Pins the registered
net_roas formula so run behaviour can be audited against it.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from seleric_swarm.api.conversations import router as conversations_router
from seleric_swarm.api.security import ApiSecurityMiddleware
from seleric_swarm.services.metrics import MetricRegistry

API_KEY = "test-metrics-key"


class _Runtime:
    def __init__(self, *, with_metrics: bool = True) -> None:
        self.metrics = (
            MetricRegistry("config/metric_registry.yaml") if with_metrics else None
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


def test_lists_governed_definitions() -> None:
    client = _build_client(_Runtime())
    response = client.get("/v1/metrics/definitions", headers=_auth())

    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, list) and body, "catalogue must not be empty"
    by_id = {entry["id"]: entry for entry in body}
    net_roas = by_id["metric.net_roas"]
    assert net_roas["key"] == "net_roas"
    assert net_roas["name"] == "net roas"
    # Pinned: the governed definition. Runs reporting other values deviate.
    assert net_roas["formula"] == "net_sales / total_ad_spend"
    assert all(
        {"id", "key", "name", "formula", "aliases"} <= set(entry) for entry in body
    ), "every entry must carry match keys and its formula"


def test_requires_authentication() -> None:
    client = _build_client(_Runtime())
    assert client.get("/v1/metrics/definitions").status_code == 401


def test_reports_unconfigured_registry() -> None:
    client = _build_client(_Runtime(with_metrics=False))
    response = client.get("/v1/metrics/definitions", headers=_auth())
    assert response.status_code == 503


@pytest.mark.parametrize("alias", ["net roas", "revenue", "cpc"])
def test_aliases_support_evidence_matching(alias: str) -> None:
    client = _build_client(_Runtime())
    body = client.get("/v1/metrics/definitions", headers=_auth()).json()
    assert any(alias in entry["aliases"] for entry in body)
