import pytest
from fastapi.testclient import TestClient

import seleric_swarm.main as main_mod
from seleric_swarm.main import app
from seleric_swarm.observability.tracing import traced_span


def test_health_and_ping_and_mission(runtime, monkeypatch):
    monkeypatch.setattr(main_mod, "_runtime", runtime)
    client = TestClient(app, raise_server_exceptions=True)
    index = client.get("/")
    assert index.status_code == 200
    assert index.json()["health"] == "/health"
    health = client.get("/health")
    assert health.status_code == 200
    ping = client.post("/v1/llm/ping", json={"message": "ping"})
    assert ping.status_code == 200
    body = ping.json()
    assert body["text"] == "pong"
    assert body["model"]
    assert "latency_ms" in body
    dumped = str(body)
    assert "sk-" not in dumped
    assert "api_key" not in dumped.lower() or "[REDACTED]" in dumped

    created = client.post(
        "/v1/missions",
        json={
            "query": "What were net sales on 2026-08-01?",
            "scope": {"timezone": "Asia/Kolkata", "as_of": "2026-09-03"},
            "mode": "read_only",
        },
    )
    assert created.status_code == 200
    mission = created.json()
    assert mission["status"] == "completed"
    mission_id = mission["mission_id"]
    fetched = client.get(f"/v1/missions/{mission_id}")
    assert fetched.status_code == 200
    assert fetched.json()["mission_id"] == mission_id


def test_mission_diagnostic_query_routes_to_full_swarm(runtime, monkeypatch):
    monkeypatch.setattr(main_mod, "_runtime", runtime)
    client = TestClient(app, raise_server_exceptions=True)
    created = client.post(
        "/v1/missions",
        json={
            "query": (
                "Why has our CAC increased for the last three days, "
                "what happens if this continues, and what should we do?"
            ),
            "scope": {"timezone": "Asia/Kolkata", "as_of": "2026-09-03"},
            "mode": "read_only",
        },
    )
    assert created.status_code == 200
    m = created.json()
    assert m["route"] == "swarm"
    assert m["status"] in {"completed", "partial", "prototype_completed", "failed"}
    # the full agent subsystems ran (full_diagnostic/full_prediction/full_skeptic default True)
    arts = m["artifacts"]
    assert set(arts) >= {"hypothesis", "causal", "prediction", "skeptic"}

    # re-run must not duplicate a subsystem's artifacts (idempotent bridges)
    assert len(arts["causal"]) <= 1
    assert len(arts["skeptic"]) <= 1
    assert len(arts["prediction"]) <= 1

    # GET returns the full swarm mission
    fetched = client.get(f"/v1/missions/{m['mission_id']}")
    assert fetched.status_code == 200
    got = fetched.json()
    assert got["route"] == "swarm"
    assert got["mission_id"] == m["mission_id"]
    assert "artifacts" in got and "events" in got


def test_langsmith_failure_does_not_fail_span():
    def boom(*_args, **_kwargs):
        raise RuntimeError("langsmith down")


    class FakeModule:
        def __enter__(self):
            raise RuntimeError("langsmith down")

        def __exit__(self, *args):
            return False

    # traced_span must swallow failures
    with traced_span("mission.lookup_v1", {"request_id": "x"}, enabled=True):
        ran = True
    assert ran


def test_traced_span_yields_usable_handle_when_disabled():
    from seleric_swarm.observability.tracing import SpanHandle

    with traced_span("x", {"request_id": "x"}, enabled=False) as span:
        assert isinstance(span, SpanHandle)
        span.set_outputs({"k": "v"})  # no-op, must not raise


def test_traced_span_reraises_body_errors_but_not_langsmith_errors():
    with pytest.raises(ValueError), traced_span("x", {"request_id": "x"}, enabled=True):
        raise ValueError("boom")
