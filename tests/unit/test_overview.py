"""Sprint 5 checklist: an overview-shaped query is answered from stored
DomainStateSnapshots without a full live mission fan-out, and a missing/
stale snapshot surfaces as a limitation rather than being silently dropped.
See docs/features/business-state-service/05_SPRINT_PLAN.md Sprint 5.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from seleric_swarm.coordinator.contracts import NormalizedQuery
from seleric_swarm.coordinator.overview import (
    MAX_SNAPSHOT_AGE_HOURS,
    OVERVIEW_DOMAINS,
    build_overview_result,
    is_overview_query,
    narrate_overview,
    overview_domains_for_query,
    read_overview_snapshots,
)
from seleric_swarm.services.domain_health.models import DomainStateSnapshot, ResolvedMetric
from seleric_swarm.services.domain_health.scheduler import ALL_DOMAINS
from seleric_swarm.services.domain_health.snapshot_store import SnapshotStore


def _normalized(intents: list[str]) -> NormalizedQuery:
    return NormalizedQuery(original_query="how are we doing today?", intents=intents)


@pytest.mark.parametrize(
    "intents, expected",
    [
        (["executive_health"], True),
        (["executive_health", "diagnostic"], False),  # user asked for more than an overview
        (["lookup"], False),
    ],
)
def test_is_overview_query(intents, expected):
    assert is_overview_query(_normalized(intents)) is expected


def test_is_overview_query_ignores_full_flag_defaults():
    """Regression: main.py's MissionRequest defaults full_diagnostic/
    full_prediction/full_skeptic/full_strategy all to True for every
    request, so those flags must never gate the overview shortcut -- a
    pure executive_health ask takes the fast path regardless of them.
    """
    assert is_overview_query(_normalized(["executive_health"])) is True


@pytest.mark.parametrize(
    "query, expected",
    [
        ("how is attribution doing", ["attribution"]),
        ("how is attribution doing today?", ["attribution"]),
        ("How are we doing today?", OVERVIEW_DOMAINS),
        ("what needs attention", OVERVIEW_DOMAINS),
        ("how is customer retention doing", ["customer"]),
    ],
)
def test_overview_domains_for_query_scopes_to_named_domain(query, expected):
    """Regression: "how is attribution doing" used to hit the shortcut and
    answer with the fixed OVERVIEW_DOMAINS 5-domain dump, never mentioning
    attribution at all (it isn't even in that list)."""
    assert overview_domains_for_query(query) == expected


def test_overview_domains_for_query_covers_all_domain_health_domains():
    # attribution/product/customer aren't in the 5-branch executive_health
    # template but ARE valid domain_health domains -- must still resolve.
    for domain in ALL_DOMAINS:
        assert overview_domains_for_query(f"how is {domain} doing") == [domain]


def _snapshot(domain: str, *, computed_at: str, headline_signals: list[str] | None = None) -> DomainStateSnapshot:
    return DomainStateSnapshot(
        domain=domain,
        brand_id="20",
        as_of="2026-09-15",
        computed_at=computed_at,
        status="OK",
        metrics=[ResolvedMetric(metric_id=f"metric.{domain}_x", value=1.0)],
        headline_signals=headline_signals or [],
    )


def test_read_overview_snapshots_flags_missing_and_stale(tmp_path):
    store = SnapshotStore(base_dir=tmp_path)
    now = datetime.now(UTC)
    fresh = _snapshot("commerce", computed_at=now.isoformat())
    stale = _snapshot("finance", computed_at=(now - timedelta(hours=MAX_SNAPSHOT_AGE_HOURS + 1)).isoformat())
    store.save(fresh)
    store.save(stale)
    # "performance" is never saved -- no snapshot at all.

    snapshots, unavailable = read_overview_snapshots(store, ["commerce", "finance", "performance"])

    assert [s.domain for s in snapshots] == ["commerce"]
    assert dict(unavailable)["finance"].startswith("snapshot stale")
    assert dict(unavailable)["performance"] == "no snapshot available"


def test_narrate_overview_includes_headline_signals_and_gaps():
    snapshots = [
        _snapshot("commerce", computed_at="2026-09-15T00:00:00+00:00", headline_signals=["net_sales_drop: -20%"]),
        _snapshot("funnel", computed_at="2026-09-15T00:00:00+00:00"),
    ]
    text, limitations = narrate_overview(snapshots, [("finance", "no snapshot available")])

    assert "commerce: net_sales_drop: -20%" in text
    assert "funnel: no threshold breaches" in text
    assert limitations == ["finance: no snapshot available -- run the domain_health scheduler."]


def test_build_overview_result_partial_when_any_domain_unavailable():
    snapshots = [_snapshot("commerce", computed_at="2026-09-15T00:00:00+00:00")]
    result = build_overview_result(
        mission_id="M1", query="how are we doing?", snapshots=snapshots, unavailable=[("finance", "no snapshot available")]
    )

    assert result.status == "partial"
    assert result.team == []
    assert all(v == [] for v in result.artifacts.values())
    assert "finance: no snapshot available -- run the domain_health scheduler." in result.limitations


def test_build_overview_result_completed_when_all_domains_fresh():
    snapshots = [_snapshot("commerce", computed_at="2026-09-15T00:00:00+00:00")]
    result = build_overview_result(mission_id="M1", query="how are we doing?", snapshots=snapshots, unavailable=[])

    assert result.status == "completed"
    assert result.limitations == []
