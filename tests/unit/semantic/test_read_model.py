"""Read-model ranking and CatalogueSnapshot delegation. Invented zz_* ids."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from seleric_swarm.semantic import SemanticReadModel
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

FIXTURE = Path("tests/fixtures/catalogue/snapshot.json")


def test_payload_keeps_unknown_fields_and_omits_missing_ones() -> None:
    model = SemanticReadModel.from_payload(json.loads(FIXTURE.read_text(encoding="utf-8")))
    sales = next(metric for metric in model.metrics if metric.id == "zz_sales")
    item = next(metric for metric in model.metrics if metric.id == "zz_sales_item")
    assert sales.extra["zz_future"] == "kept"
    assert sales.volume_metric is None
    assert item.date_basis is None
    assert model.extra["future_field"] is True
    assert model.version == "zz-snap-1"
    assert model.hierarchies[0].levels == ("zz_coarse", "zz_fine")
    assert model.views[0].freshness == "ok"
    assert model.scope_dimension_ids == ("zz_tenant",)


def test_read_model_rejects_unknown_constructor_fields() -> None:
    with pytest.raises(ValidationError):
        SemanticReadModel(not_a_field=1)  # type: ignore[call-arg]


def test_carries_time_family_and_grain_twin() -> None:
    model = SemanticReadModel.from_payload(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert model.carries("zz_sales", "zz_day")
    assert model.carries("zz_sales", "zz_coarse")
    assert model.family_head("zz_fine") == "zz_coarse"
    assert model.conformed_sibling("zz_coarse", {"zz_fine"}) == "zz_fine"
    assert model.grain_twins_for("zz_sales") == ["zz_sales_item"]
    assert model.carries("zz_sales", "zz_fine")
    assert model.aggregation_for("zz_rate") == "ratio"
    assert model.volume_metric_for("zz_rate") == "zz_sales"


def test_redirect_prefers_same_unit_over_alphabetical() -> None:
    model = SemanticReadModel.from_payload({
        "metrics": [
            {
                "id": "zz_asked",
                "unit": "ZZU",
                "aggregation": "additive",
                "supported_dimensions": ["zz_other"],
                "grain_twins": ["zz_twin"],
            },
            {"id": "zz_twin", "unit": "count", "supported_dimensions": ["zz_dim", "zz_other"]},
            {
                "id": "zz_match",
                "unit": "ZZU",
                "aggregation": "additive",
                "supported_dimensions": ["zz_dim", "zz_other"],
            },
            {"id": "zz_alpha", "unit": "count", "supported_dimensions": ["zz_dim"]},
        ]
    })
    ranked = model.metrics_supporting_dimension("zz_dim", like="zz_asked")
    assert ranked[0] == "zz_twin"
    assert ranked.index("zz_match") < ranked.index("zz_alpha")


def test_snapshot_delegates_rank_and_keeps_scope() -> None:
    snap = CatalogueSnapshot(
        metrics=(
            CatalogueMetricMeta(id="zz_a", supported_dimensions=["zz_dim"], raw={"unit": "ZZU"}),
            CatalogueMetricMeta(id="zz_b", supported_dimensions=["zz_dim"], raw={"unit": "other"}),
        ),
        scope_dimension_ids=("zz_dim",),
    )
    assert snap.metrics_supporting_dimension("zz_dim", like="zz_a")[0] == "zz_a"
    assert snap.scope_dimension_ids == ("zz_dim",)
