from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from seleric_swarm.api import monitoring
from seleric_swarm.conversations.contracts import Principal, PrincipalAuthMethod
from seleric_swarm.domain.models import MetricState, SeriesPoint
from seleric_swarm.services.metrics import MetricRegistry
from seleric_swarm.services.time_range import resolve_time_range


def _end(request):
    """The last complete day the resolver will expect, exactly as it computes it."""
    return date.fromisoformat(resolve_time_range(request.time_range, "Asia/Kolkata", None).end)


def _series(values, end):
    start = end - timedelta(days=len(values) - 1)
    return [SeriesPoint(ts=(start + timedelta(days=i)).isoformat(), value=v) for i, v in enumerate(values)]


class _BusinessState:
    """Orders fall off a cliff on the last day; net_sales is steady; everything else answers like net_sales."""

    def __init__(self):
        self.runtime = SimpleNamespace(metrics=REGISTRY)
        self._runtime = self.runtime

    async def get_metric_state(self, request):
        values = [100.0 + (i % 5) for i in range(27)] + ([40.0] if request.metric_id == "orders" else [102.0])
        end = _end(request) - (timedelta(days=1) if request.metric_id == "discounts" else timedelta(0))  # no row yesterday
        return MetricState(
            metric_id=request.metric_id, catalogue_metric_id=request.metric_id, as_of=end.isoformat(), actual=values[-1],
            series=_series(values, end), freshness="CURRENT", status="OK", provenance={"query_id": "q"},
        )


REGISTRY = MetricRegistry.from_definitions([
    {"id": mid, "domain": "commerce", "unit": "count", "grain": "day", "direction_bad": "down", "raw": {"direction_bad": "down"}}
    for mid in ("orders", "net_sales", "gross_sales", "returns_cancels", "cancelled_orders", "new_customers", "total_sales", "discounts", "aov")
] + [
    {"id": mid, "domain": "commerce", "unit": "ratio", "grain": "day", "direction_bad": "down", "raw": {"direction_bad": "down"}}
    for mid in ("mer", "conversion_rate")
])


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("SELERIC_DOMAIN_HEALTH_DIR", str(tmp_path))
    monkeypatch.setattr(monitoring, "_REFRESH_COOLDOWN_S", 0.0)
    monitoring._last_refresh.clear()
    runtime = SimpleNamespace(metrics=REGISTRY, bootstrap=None, business_state=_BusinessState())
    app = FastAPI()
    app.state.runtime_provider = lambda: runtime
    app.include_router(monitoring.router)

    @app.middleware("http")
    async def principal(request, call_next):
        if request.headers.get("x-anon") != "1":
            request.state.principal = Principal(
                principal_id="p", workspace_id="w", user_id="alice", authenticated=True, auth_method=PrincipalAuthMethod.SHARED_API_KEY
            )
        return await call_next(request)

    return TestClient(app)


def test_unauthenticated_calls_are_refused(client):
    assert client.get("/v1/monitoring/state", headers={"x-anon": "1"}).status_code == 401
    assert client.put("/v1/monitoring/config/commerce", json={"metrics": []}, headers={"x-anon": "1"}).status_code == 401


def test_state_lists_every_shipped_domain_with_titles(client):
    body = client.get("/v1/monitoring/state").json()
    ids = [d["id"] for d in body["domains"]]
    assert "commerce" in ids and "operations" in ids
    assert all(d["title"] for d in body["domains"])
    assert all(d["snapshot"] is None for d in body["domains"])


def test_save_then_refresh_shows_the_new_selection_with_judgements(client):
    saved = client.put("/v1/monitoring/config/commerce", json={"enabled": True, "metrics": [
        {"metric_id": "orders", "alert": {"on": "anomaly"}}, {"metric_id": "net_sales"}]})
    assert saved.status_code == 200, saved.text
    assert saved.json()["custom"] is True and saved.json()["version"] == 1
    assert saved.json()["pending"] is True  # nothing computed yet for this selection

    fresh = client.post("/v1/monitoring/refresh/commerce")
    assert fresh.status_code == 200, fresh.text
    body = fresh.json()
    assert body["pending"] is False
    by_id = {m["metric_id"]: m for m in body["snapshot"]["metrics"]}
    assert set(by_id) == {"orders", "net_sales"}
    assert by_id["orders"]["status"] == "alert" and by_id["net_sales"]["status"] == "ok"
    assert len(by_id["orders"]["series"]) == 28
    assert body["counts"] == {"alert": 1, "ok": 1}
    assert any("orders" in s for s in body["snapshot"]["signals"])


def test_reset_goes_back_to_shipped_defaults(client):
    client.put("/v1/monitoring/config/commerce", json={"metrics": [{"metric_id": "orders"}]})
    reset = client.delete("/v1/monitoring/config/commerce").json()
    assert reset["custom"] is False and len(reset["metrics"]) > 1


@pytest.mark.parametrize("body,code", [
    ({"metrics": [{"metric_id": "ghost"}]}, 422),
    ({"metrics": "orders"}, 422),
    ({"metrics": [], "surprise": 1}, 422),
    ({"metrics": [], "expected_version": "1"}, 422),
])
def test_bad_saves_are_rejected_without_writing(client, body, code):
    assert client.put("/v1/monitoring/config/commerce", json=body).status_code == code
    assert client.get("/v1/monitoring/config").json()["domains"][0]["custom"] is False


def test_unknown_domain_and_stale_version(client):
    assert client.put("/v1/monitoring/config/ghost", json={"metrics": []}).status_code == 404
    assert client.put("/v1/monitoring/config/commerce", json={"metrics": [], "expected_version": 0}).status_code == 200
    assert client.put("/v1/monitoring/config/commerce", json={"metrics": [], "expected_version": 0}).status_code == 409


def test_refresh_refuses_a_hammered_domain(client, monkeypatch):
    monkeypatch.setattr(monitoring, "_REFRESH_COOLDOWN_S", 60.0)
    client.put("/v1/monitoring/config/commerce", json={"metrics": [{"metric_id": "orders"}]})
    assert client.post("/v1/monitoring/refresh/commerce").status_code == 200
    assert client.post("/v1/monitoring/refresh/commerce").status_code == 429


def test_catalogue_lists_only_domain_metrics(client):
    ids = [m["id"] for m in client.get("/v1/monitoring/catalogue/commerce").json()["metrics"]]
    assert "orders" in ids and "ghost" not in ids


def test_a_multiple_is_not_shown_or_judged_as_a_share(client):
    client.put("/v1/monitoring/config/commerce", json={"metrics": [{"metric_id": "mer"}, {"metric_id": "conversion_rate"}, {"metric_id": "orders"}]})
    by_id = {m["metric_id"]: m for m in client.post("/v1/monitoring/refresh/commerce").json()["snapshot"]["metrics"]}
    assert by_id["mer"]["display"] == "multiple" and by_id["mer"]["shape"] == "additive"
    assert by_id["conversion_rate"]["display"] == "percent" and by_id["conversion_rate"]["shape"] == "ratio"
    assert by_id["orders"]["display"] == "count"


def test_selected_metrics_reach_the_business_snapshot(client):
    client.put("/v1/monitoring/config/commerce", json={"metrics": [
        {"metric_id": "orders", "headline": True}, {"metric_id": "discounts", "headline": True}, {"metric_id": "net_sales", "headline": False}]})
    assert client.post("/v1/monitoring/refresh/commerce").status_code == 200
    from seleric_swarm.services.domain_health.snapshot_store import SnapshotStore

    business = SnapshotStore().get_latest("business")
    ids = [m.metric_id for m in business.metrics]
    assert "orders" in ids and "discounts" in ids and "net_sales" not in ids
    assert business.metrics[ids.index("orders")].status == "alert"


def test_a_metric_with_no_row_for_the_day_is_not_judged_on_an_older_day(client):
    client.put("/v1/monitoring/config/commerce", json={"metrics": [{"metric_id": "discounts"}, {"metric_id": "net_sales"}]})
    by_id = {m["metric_id"]: m for m in client.post("/v1/monitoring/refresh/commerce").json()["snapshot"]["metrics"]}
    assert by_id["discounts"]["status"] == "no_data" and "no value for" in by_id["discounts"]["judgement"]["reason"]
    assert by_id["discounts"]["observed_on"] < by_id["net_sales"]["observed_on"]
    assert by_id["net_sales"]["status"] == "ok"
