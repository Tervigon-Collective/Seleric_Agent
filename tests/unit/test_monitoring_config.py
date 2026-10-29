from datetime import UTC, datetime, timedelta

import pytest

from seleric_swarm.services.domain_health.models import DomainStateSnapshot
from seleric_swarm.services.domain_health.monitor_config import (
    MAX_METRICS_PER_DOMAIN,
    ConfigError,
    OverrideStore,
    VersionConflict,
    candidates,
    merge_domain,
    validate_domain,
)
from seleric_swarm.services.domain_health.resolver import DomainHealthProfiles
from seleric_swarm.services.domain_health.scheduler import domains_due
from seleric_swarm.services.metrics import MetricRegistry

REGISTRY = MetricRegistry.from_definitions([
    {"id": "orders", "domain": "commerce", "unit": "count", "grain": "day", "direction_bad": "down"},
    {"id": "net_sales", "domain": "commerce", "unit": "INR", "grain": "day", "direction_bad": "down"},
    {"id": "cpm", "domain": "performance", "unit": "INR", "grain": "day"},
    {"id": "orders_hourly", "domain": "commerce", "unit": "count", "grain": "hour"},
])
DEFAULTS = [{"metric_id": "orders", "features": ["period_delta_pct"]}, {"metric_id": "net_sales", "features": []}]


def _ok(metrics, **extra):
    return validate_domain("commerce", {"metrics": metrics, **extra}, registry=REGISTRY, defaults=DEFAULTS)


def test_valid_selection_is_normalised():
    out = _ok([{"metric_id": "orders", "alert": {"on": "worse_by", "threshold": 12}}, {"metric_id": "net_sales", "label": " Sales "}])
    assert out["metrics"][0]["alert"] == {"on": "worse_by", "threshold": 12.0}
    assert out["metrics"][1]["label"] == "Sales"
    assert out["enabled"] is True


@pytest.mark.parametrize("bad", [
    [{"metric_id": "nope"}],                                              # not in the catalogue
    [{"metric_id": "orders_hourly"}],                                     # not a daily metric
    [{"metric_id": "orders"}, {"metric_id": "orders"}],                   # duplicate
    [{"metric_id": "Orders; DROP"}],                                      # malformed id
    [{"metric_id": "orders", "filters": {"x": "y"}}],                     # filters are not user-settable
    [{"metric_id": "orders", "alert": {"on": "shell"}}],                  # unknown rule kind
    [{"metric_id": "orders", "alert": {"on": "worse_by", "threshold": -5}}],
    [{"metric_id": "orders", "alert": {"on": "worse_by", "threshold": float("nan")}}],
    [{"metric_id": "orders", "alert": {"on": "worse_by", "threshold": True}}],
    [{"metric_id": "orders", "direction_bad": "sideways"}],
    [{"metric_id": "orders", "label": "x" * 200}],
])
def test_bad_selection_is_rejected(bad):
    with pytest.raises(ConfigError):
        _ok(bad)


def test_metric_count_is_capped():
    many = [{"metric_id": f"m{i}"} for i in range(MAX_METRICS_PER_DOMAIN + 1)]
    with pytest.raises(ConfigError, match="at most"):
        _ok(many)


def test_cold_catalogue_is_not_mistaken_for_an_empty_one():
    with pytest.raises(ConfigError, match="not loaded"):
        validate_domain("commerce", {"metrics": [{"metric_id": "orders"}]}, registry=MetricRegistry(), defaults=DEFAULTS)


def test_store_round_trip_version_and_reset(tmp_path):
    store = OverrideStore(tmp_path)
    assert store.get("commerce") is None
    saved = store.save("commerce", {"enabled": True, "metrics": []}, expected_version=0, updated_by="u1")
    assert saved["version"] == 1
    with pytest.raises(VersionConflict):
        store.save("commerce", {"enabled": True, "metrics": []}, expected_version=0, updated_by="u2")
    assert store.save("commerce", {"enabled": True, "metrics": []}, expected_version=1, updated_by="u2")["version"] == 2
    assert store.reset("commerce") is True and store.reset("commerce") is False


def test_corrupt_override_file_falls_back_to_defaults(tmp_path):
    (tmp_path / "_config").mkdir()
    (tmp_path / "_config" / "overrides.json").write_text("{not json")
    assert OverrideStore(tmp_path).load() == {"version": 0, "domains": {}}


def test_merge_keeps_shipped_filters_and_adds_user_choices():
    default = {"title": "T", "metrics": [{"metric_id": "ctr", "catalogue_metric": "ctr", "filters": {"ad_platform": "meta"}, "features": ["period_delta_pct"]}]}
    out = merge_domain(default, {"enabled": True, "version": 3, "metrics": [{"metric_id": "ctr", "alert": {"on": "anomaly"}}, {"metric_id": "cpm"}]})
    assert out["custom"] and out["version"] == 3
    assert out["metrics"][0]["filters"] == {"ad_platform": "meta"} and out["metrics"][0]["alert"] == {"on": "anomaly"}
    assert out["metrics"][1]["features"]  # a metric added in the UI still gets features
    assert merge_domain(default, None)["custom"] is False


def test_candidates_are_daily_catalogue_metrics_of_the_domain():
    ids = [c["id"] for c in candidates(REGISTRY, ["commerce"])]
    assert ids == ["net_sales", "orders"]
    assert {c["id"]: c["direction_bad"] for c in candidates(REGISTRY, ["performance"])} == {"cpm": None}


def test_shipped_domains_are_derived_from_the_profile_file():
    profiles = DomainHealthProfiles()
    assert "commerce" in profiles.domain_ids() and "operations" in profiles.domain_ids()
    titles = {d: profiles.get(d).get("title") for d in profiles.domain_ids()}
    assert all(titles.values())


def test_shipped_defaults_have_no_duplicate_measures():
    seen = {}
    for domain in DomainHealthProfiles().domain_ids():
        for e in DomainHealthProfiles().get(domain)["metrics"]:
            key = (e.get("catalogue_metric", e["metric_id"]), str(e.get("filters")), e.get("date_axis"))
            assert key not in seen, f"{domain}.{e['metric_id']} repeats {seen[key]}"
            seen[key] = f"{domain}.{e['metric_id']}"


def _snap(domain, minutes_old):
    ts = (datetime.now(UTC) - timedelta(minutes=minutes_old)).isoformat()
    return DomainStateSnapshot(domain=domain, brand_id="b", as_of="2026-10-28", computed_at=ts)


def test_only_stale_or_changed_domains_are_due(tmp_path):
    profiles = DomainHealthProfiles(overrides=OverrideStore(tmp_path))
    latest = {d: _snap(d, 5) for d in profiles.domain_ids()}
    assert domains_due(profiles, latest, min_interval_s=900) == []
    latest["finance"] = _snap("finance", 30)
    latest["product"] = None
    assert set(domains_due(profiles, latest, min_interval_s=900)) == {"finance", "product"}
    OverrideStore(tmp_path).save("commerce", {"enabled": True, "metrics": []}, expected_version=0, updated_by="u")
    assert "commerce" in domains_due(profiles, latest, min_interval_s=900)
    OverrideStore(tmp_path).save("customer", {"enabled": False, "metrics": []}, expected_version=0, updated_by="u")
    assert "customer" not in domains_due(profiles, {d: None for d in profiles.domain_ids()}, min_interval_s=900)


def test_headline_follows_the_users_flags_and_switched_off_domains(tmp_path):
    store = OverrideStore(tmp_path)
    profiles = DomainHealthProfiles(overrides=store)
    shipped = profiles.headline_metrics()
    assert "net_sales" in shipped
    # untick a shipped headline metric, add another
    store.save("commerce", {"enabled": True, "metrics": [
        {"metric_id": "net_sales", "headline": False}, {"metric_id": "orders", "headline": True}, {"metric_id": "discounts", "headline": True}]},
        expected_version=0, updated_by="u")
    now = profiles.headline_metrics()
    assert "net_sales" not in now
    assert "discounts" in now
    assert now.count("orders") == 1
    # switching the whole domain off takes its metrics out of the headline
    store.save("commerce", {"enabled": False, "metrics": [{"metric_id": "orders", "headline": True}]}, expected_version=1, updated_by="u")
    assert "orders" not in profiles.headline_metrics()


def test_headline_flag_must_be_boolean():
    with pytest.raises(ConfigError, match="headline"):
        _ok([{"metric_id": "orders", "headline": "yes"}])


def test_display_kind_separates_shares_from_multiples():
    from seleric_swarm.services.domain_health.resolver import display_kind

    assert display_kind("ctr", "ratio", [0.02]) == "percent"
    assert display_kind("contribution_margin_pct", "ratio", [0.59, -0.2]) == "percent"  # name rule does not know _pct
    assert display_kind("net_margin_pct", "ratio", [-0.2, -0.1, -4.8, -0.15]) == "percent"  # one wild day does not flip it
    assert display_kind("mer", "ratio", [1.5]) == "multiple"
    assert display_kind("net_roas", "ratio", [0.8]) == "multiple"                       # named multiple even below 1
    assert display_kind("some_new_ratio", "ratio", [3.2]) == "multiple"
    assert display_kind("net_sales", "INR", [1.0]) == "currency" and display_kind("orders", "count", []) == "count"
