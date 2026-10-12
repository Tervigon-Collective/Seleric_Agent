"""AG-04: Core canonical semantic contract integration tests."""

from __future__ import annotations

from seleric_swarm.semantic import SemanticReadModel
from seleric_swarm.graph import build_graph
from seleric_swarm.services.catalogue_bootstrap import CatalogueBootstrap, CatalogueMetricMeta

# Core v2 catalogue payload (subset of Core's certified metrics)
CORE_PAYLOAD = {
    "catalogue_version": "2.0.0",
    "semantic_version": "2",
    "metrics": [
        {
            "id": "cac",
            "label": "CAC",
            "display_name": "CAC",
            "aggregation": "ratio",
            "unit": "INR",
            "supported_dimensions": [],
            "ratio_components": {"numerator": "ad_spend", "denominator": "new_customers"},
            "cube_mapping": {"view": "unit_economics", "measure": "unit_economics.cac"},
            "formula": {
                "human_readable": "Customer acquisition cost = ad spend ÷ new customers.",
                "authoritative_source": "cube",
                "depends_on": ["ad_spend", "new_customers"],
            },
            "status": "certified",
            "category": "customer",
            "version": "1.0.0",
            "grain": "brand_day",
            "data_owner": "Growth / Finance",
            "access_policy": {"roles_allowed": ["analyst", "exec", "finance", "growth_lead"]},
        },
        {
            "id": "be_roas",
            "label": "Break-even ROAS",
            "display_name": "Break-even ROAS",
            "aggregation": "ratio",
            "unit": "ratio",
            "supported_dimensions": [],
            "ratio_components": {"numerator": "order_pnl_net_sales", "denominator": "contribution_margin"},
            "cube_mapping": {"view": "order_pnl", "measure": "order_pnl.be_roas"},
            "formula": {
                "human_readable": "Break-even ROAS = net sales ÷ (net sales − net COGS) (order date).",
                "authoritative_source": "cube",
                "depends_on": ["order_pnl_net_sales", "contribution_margin"],
            },
            "status": "certified",
            "category": "finance",
            "version": "2.0.0",
            "grain": "brand_order_day_channel_ad",
        },
        {
            "id": "net_sales",
            "label": "Net Sales",
            "display_name": "Net Sales",
            "aggregation": "additive",
            "unit": "INR",
            "supported_dimensions": ["channel"],
            "formula": {
                "human_readable": "Net sales of the orders placed in the period (order date).",
                "authoritative_source": "cube",
                "depends_on": ["order_pnl_net_sales", "returns", "cancels"],
            },
            "status": "certified",
            "category": "commerce",
            "version": "1.0.0",
            "grain": "brand_order_day",
        },
        {
            "id": "ad_spend",
            "label": "Ad Spend",
            "display_name": "Ad Spend",
            "aggregation": "additive",
            "unit": "INR",
            "supported_dimensions": ["channel", "campaign_name"],
            "status": "certified",
            "category": "advertising",
            "version": "1.0.0",
            "grain": "brand_day",
        },
        {
            "id": "new_customers",
            "label": "New Customers",
            "display_name": "New Customers",
            "aggregation": "additive",
            "unit": "count",
            "status": "certified",
            "category": "commerce",
            "version": "1.0.0",
            "grain": "brand_day",
        },
        {
            "id": "order_pnl_net_sales",
            "label": "P&L Net Sales",
            "display_name": "P&L Net Sales",
            "aggregation": "additive",
            "unit": "INR",
            "status": "certified",
            "category": "finance",
            "version": "1.0.0",
            "grain": "brand_order_day",
        },
        {
            "id": "contribution_margin",
            "label": "Contribution Margin",
            "display_name": "Contribution Margin",
            "aggregation": "additive",
            "unit": "INR",
            "status": "certified",
            "category": "finance",
            "version": "1.0.0",
            "grain": "brand_order_day",
        },
        {
            "id": "channel_cac",
            "label": "Channel CAC",
            "display_name": "Channel CAC",
            "aggregation": "ratio",
            "unit": "INR",
            "ratio_components": {"numerator": "ad_spend", "denominator": "new_customers"},
            "cube_mapping": {"view": "unit_economics", "measure": "unit_economics.channel_cac"},
            "formula": {
                "human_readable": "Channel CAC = ad spend ÷ new customers (by channel).",
                "authoritative_source": "cube",
                "depends_on": ["ad_spend", "new_customers"],
            },
            "status": "certified",
            "category": "customer",
            "version": "1.0.0",
            "grain": "brand_day_channel",
        },
    ],
    "dimensions": [
        {"id": "channel", "allowed_values": ["meta", "google"]},
        {"id": "campaign_name", "allowed_values": []},
    ],
    "hierarchies": [{"id": "traffic", "levels": ["channel", "campaign_name"]}],
    "views": [],
    "brands": [],
    "scope_dimensions": ["brand_id"],
}


def test_ag04_t01_core_ratio_components_preserved() -> None:
    """Core's canonical ratio_components (numerator/denominator) are preserved in MetricRecord."""
    model = SemanticReadModel.from_payload(CORE_PAYLOAD)
    cac = model._metric("cac")
    assert cac.ratio_components == {"numerator": "ad_spend", "denominator": "new_customers"}
    be_roas = model._metric("be_roas")
    assert be_roas.ratio_components == {"numerator": "order_pnl_net_sales", "denominator": "contribution_margin"}


def test_ag04_t02_core_cube_mapping_preserved() -> None:
    """Core's canonical cube_mapping (view/measure) is preserved."""
    model = SemanticReadModel.from_payload(CORE_PAYLOAD)
    cac = model._metric("cac")
    assert cac.cube_mapping == {"view": "unit_economics", "measure": "unit_economics.cac"}
    be_roas = model._metric("be_roas")
    assert be_roas.cube_mapping == {"view": "order_pnl", "measure": "order_pnl.be_roas"}


def test_ag04_t03_core_formula_preserved() -> None:
    """Core's canonical formula (human_readable, authoritative_source, depends_on) is preserved."""
    model = SemanticReadModel.from_payload(CORE_PAYLOAD)
    cac = model._metric("cac")
    assert cac.formula is not None
    assert cac.formula["human_readable"] == "Customer acquisition cost = ad spend ÷ new customers."
    assert cac.formula["authoritative_source"] == "cube"
    assert cac.formula["depends_on"] == ["ad_spend", "new_customers"]


def test_ag04_t04_core_metadata_preserved() -> None:
    """Core's canonical metadata (status, category, version, grain, data_owner, access_policy) preserved."""
    model = SemanticReadModel.from_payload(CORE_PAYLOAD)
    cac = model._metric("cac")
    assert cac.status == "certified"
    assert cac.category == "customer"
    assert cac.version == "1.0.0"
    assert cac.grain == "brand_day"
    assert cac.data_owner == "Growth / Finance"
    assert cac.access_policy == {"roles_allowed": ["analyst", "exec", "finance", "growth_lead"]}


def test_ag04_t05_ratio_graph_from_core_canonical() -> None:
    """Identity graph uses Core's canonical ratio_components (SEM-14)."""
    model = SemanticReadModel.from_payload(CORE_PAYLOAD)
    graph = build_graph(model, None)
    # CAC: ad_spend (numerator) -> cac, new_customers (denominator) -> cac
    cac_parents = {e.source for e in graph.parents("cac", "identity")}
    assert "ad_spend" in cac_parents
    assert "new_customers" in cac_parents
    # Verify roles
    num_edge = next(e for e in graph.parents("cac", "identity") if e.role == "numerator")
    den_edge = next(e for e in graph.parents("cac", "identity") if e.role == "denominator")
    assert num_edge.source == "ad_spend"
    assert den_edge.source == "new_customers"
    # BE ROAS: order_pnl_net_sales (num), contribution_margin (den)
    roas_parents = {e.source for e in graph.parents("be_roas", "identity")}
    assert "order_pnl_net_sales" in roas_parents
    assert "contribution_margin" in roas_parents


def test_ag04_t06_additive_graph_from_core_formula_depends_on() -> None:
    """Additive/composite graph uses Core's formula.depends_on with coefficients."""
    model = SemanticReadModel.from_payload(CORE_PAYLOAD)
    graph = build_graph(model, None)
    # net_sales depends on order_pnl_net_sales, returns, cancels
    ns_parents = {e.source for e in graph.parents("net_sales", "identity")}
    assert "order_pnl_net_sales" in ns_parents
    assert "returns" in ns_parents
    assert "cancels" in ns_parents


def test_ag04_t07_catalogue_bootstrap_preserves_core_fields() -> None:
    """CatalogueBootstrap preserves Core SEM-14 fields in raw payload."""
    bootstrap = CatalogueBootstrap.__new__(CatalogueBootstrap)
    bootstrap._cache = {}
    bootstrap._load_payload(CORE_PAYLOAD)
    cac_meta = bootstrap.get("cac")
    assert cac_meta is not None
    assert cac_meta.ratio_components == {"numerator": "ad_spend", "denominator": "new_customers"}
    assert cac_meta.cube_mapping == {"view": "unit_economics", "measure": "unit_economics.cac"}
    assert cac_meta.formula is not None
    assert cac_meta.formula["depends_on"] == ["ad_spend", "new_customers"]


def test_ag04_t08_catalogue_snapshot_preserves_core_fields() -> None:
    """CatalogueSnapshot preserves Core SEM-14 fields via .raw."""
    bootstrap = CatalogueBootstrap.__new__(CatalogueBootstrap)
    bootstrap._cache = {}
    bootstrap._load_payload(CORE_PAYLOAD)
    snapshot = bootstrap.snapshot()
    cac_meta = next(m for m in snapshot.metrics if m.id == "cac")
    assert cac_meta.ratio_components == {"numerator": "ad_spend", "denominator": "new_customers"}
    assert cac_meta.cube_mapping == {"view": "unit_economics", "measure": "unit_economics.cac"}
    assert cac_meta.formula is not None


def test_ag04_t09_ratio_components_for_method() -> None:
    """CatalogueBootstrap.ratio_components_for returns parsed ratio components."""
    bootstrap = CatalogueBootstrap.__new__(CatalogueBootstrap)
    bootstrap._cache = {}
    bootstrap._load_payload(CORE_PAYLOAD)
    rc = bootstrap.ratio_components_for("cac")
    assert rc == {"numerator": "ad_spend", "denominator": "new_customers"}
    # Additive metric returns None
    assert bootstrap.ratio_components_for("net_sales") is None


def test_ag04_t10_core_semantic_port_enforce_mode() -> None:
    """SemanticReadModel can be built from Core's canonical payload (enforce mode)."""
    # The semantic port (enforce mode) should produce a SemanticReadModel
    # with Core's canonical fields when given the Core v2 catalogue.
    model = SemanticReadModel.from_payload(CORE_PAYLOAD)
    assert model.version == "2.0.0"
    # Verify all certified metrics have Core canonical fields
    for metric in model.metrics:
        if metric.status == "certified":
            if metric.aggregation == "ratio":
                assert metric.ratio_components is not None
                assert "numerator" in metric.ratio_components
                assert "denominator" in metric.ratio_components
            assert metric.status == "certified"
            assert metric.category != ""
            assert metric.version != ""
            assert metric.grain != ""


from seleric_swarm.graph import build_graph