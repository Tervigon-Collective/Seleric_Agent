from seleric_swarm.services.metrics import MetricDefinition, MetricRegistry


def _registry(live_defs=None, yaml_metrics=None):
    r = MetricRegistry.__new__(MetricRegistry)
    r._metrics = {m.id: m for m in (yaml_metrics or [])}
    r._by_catalogue = {m.catalogue_metric: m.id for m in r._metrics.values() if m.catalogue_metric}
    r._live = None
    r._live_defs = live_defs or {}
    return r


def test_resolve_hint_matches_live_catalogue_by_substring():
    live = {
        "net_roas_all_channels": MetricDefinition(
            {"id": "net_roas_all_channels", "description": "All-channels Net ROAS", "domain": "performance"}
        ),
    }
    r = _registry(live_defs=live)
    assert r.resolve_hint("metric.roas") == "net_roas_all_channels"


def test_resolve_hint_matches_yaml_alias():
    yaml_metric = MetricDefinition(
        {"id": "metric.net_sales", "aliases": ["sales", "revenue"], "domain": "commerce"}
    )
    r = _registry(yaml_metrics=[yaml_metric])
    assert r.resolve_hint("metric.sales") == "metric.net_sales"


def test_resolve_hint_returns_none_when_nothing_matches():
    r = _registry()
    assert r.resolve_hint("metric.does_not_exist") is None


def test_resolve_hint_passthrough_for_already_known_id():
    yaml_metric = MetricDefinition({"id": "metric.net_sales", "domain": "commerce"})
    r = _registry(yaml_metrics=[yaml_metric])
    assert r.resolve_hint("metric.net_sales") == "metric.net_sales"
