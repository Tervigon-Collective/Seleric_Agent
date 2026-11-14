from seleric_swarm.services.metrics import MetricRegistry


def _registry(*payloads):
    return MetricRegistry.from_definitions(list(payloads))


def test_resolve_hint_matches_live_catalogue_by_substring():
    r = _registry({"id": "net_roas_all_channels", "description": "All-channels Net ROAS", "domain": "performance"})
    assert r.resolve_hint("roas") == "net_roas_all_channels"


def test_resolve_hint_returns_none_when_nothing_matches():
    assert _registry().resolve_hint("does_not_exist") is None


def test_resolve_hint_passthrough_for_already_known_id():
    r = _registry({"id": "net_sales", "domain": "commerce"})
    assert r.resolve_hint("net_sales") == "net_sales"


def test_aliases_are_not_a_vocabulary():
    r = _registry({"id": "net_sales", "aliases": ["sales", "revenue"], "domain": "commerce"})
    assert r.resolve_hint("revenue") is None
