"""Offline cases for the diagnosis causal-graph chart builder."""

from __future__ import annotations

from seleric_swarm.causal.diagnosis import (
    BridgeTerm,
    DecompositionTerm,
    DiagnosisReport,
    DriverFinding,
)
from seleric_swarm.causal.graph_chart import build_causal_graph_spec


def _report(*drivers: DriverFinding) -> DiagnosisReport:
    return DiagnosisReport(
        outcome="net_sales",
        event_window=["2026-10-12", "2026-10-12"],
        verdict="partially_explained",
        headline="net_sales moved",
        event=None,
        drivers=list(drivers),
    )


def test_build_unions_driver_dags_and_marks_treatment_edges() -> None:
    report = _report(
        DriverFinding(
            driver="ad_spend", classification="supported_cause", status="implicated",
            reason="estimated", effect_per_unit=0.5, share_of_change=0.4,
            graph_edges=[["seasonality", "ad_spend"], ["seasonality", "net_sales"], ["ad_spend", "net_sales"]],
        ),
        DriverFinding(
            driver="returns", classification="correlation", status="implicated",
            reason="estimated", effect_per_unit=-0.2, share_of_change=0.15,
            graph_edges=[["returns", "net_sales"]],
        ),
    )
    spec = build_causal_graph_spec(report)
    assert spec is not None
    assert spec["chart_type"] == "causal_graph"
    ids = {n["id"] for n in spec["nodes"]}
    assert ids >= {"net_sales", "ad_spend", "returns", "seasonality"}
    outcome = next(n for n in spec["nodes"] if n["id"] == "net_sales")
    assert outcome["role"] == "outcome"
    treatment = [e for e in spec["links"] if e["kind"] == "treatment"]
    assert {"ad_spend", "returns"} <= {e["source"] for e in treatment}
    assert all(e["target"] == "net_sales" for e in treatment)


def test_build_returns_none_without_drivers_or_identity() -> None:
    report = _report(
        DriverFinding(driver="noise", classification=None, status="excluded", reason="no path"),
    )
    assert build_causal_graph_spec(report) is None


def test_build_draws_the_accounting_bridge_when_no_dowhy_dag_was_estimated() -> None:
    report = _report(DriverFinding(driver="noise", classification=None, status="excluded", reason="no path"))
    report.outcome = "net_profit"
    report.bridge = [
        BridgeTerm("contribution_margin", "net_profit", 1.0, 0, 1.0, 2.0, -1.0, 0.78),
        BridgeTerm("order_pnl_ad_spend", "net_profit", -1.0, 0, 3.0, 2.0, -1.0, 0.22),
        BridgeTerm("product_net_profit", "contribution_margin", 1.0, 1, 1.0, 2.0, -1.0, 0.78),
    ]
    spec = build_causal_graph_spec(report)
    assert spec is not None
    assert {(e["source"], e["target"]) for e in spec["links"]} == {
        ("contribution_margin", "net_profit"), ("order_pnl_ad_spend", "net_profit"),
        ("product_net_profit", "contribution_margin"),
    }
    assert all(e["kind"] == "identity" for e in spec["links"])
    assert next(n for n in spec["nodes"] if n["id"] == "net_profit")["role"] == "outcome"
    assert "not estimated causal effects" in spec["warnings"][0]


def test_build_falls_back_to_decomposition_terms_without_a_bridge() -> None:
    report = _report()
    report.decomposition = [DecompositionTerm("orders", 1.0, 5.0, 4.0, 1.0, 0.6),
                            DecompositionTerm("aov", 1.0, 5.0, 4.0, 1.0, 0.4)]
    spec = build_causal_graph_spec(report)
    assert spec is not None and {e["source"] for e in spec["links"]} == {"orders", "aov"}


def test_dow_nodes_get_weekday_labels() -> None:
    report = _report(
        DriverFinding(
            driver="ad_spend", classification="correlation", status="implicated",
            reason="estimated", effect_per_unit=0.1, share_of_change=0.2,
            graph_edges=[
                ["dow_1", "ad_spend"], ["dow_1", "net_sales"],
                ["ad_spend", "net_sales"],
            ],
        ),
    )
    spec = build_causal_graph_spec(report)
    assert spec is not None
    dow = next(n for n in spec["nodes"] if n["id"] == "dow_1")
    assert dow["name"] == "Tue (day of week)"
    assert dow["role"] == "confounder"
