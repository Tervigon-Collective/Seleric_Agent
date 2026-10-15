from pydantic_ai.tools import ToolDefinition

from seleric_swarm.agent.prompt import metric_index
from seleric_swarm.agent.prompt_slim import slim_defs, slim_guide
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot

GUIDE = """Intro.

USER-FACING RESPONSE FORMAT
Write short answers. Show tables.

NON-NEGOTIABLE RULES

1. Never invent numbers.

5. Never calculate derived results yourself.
   - Do not compute deltas.

6. Ground every number in provenance.

9. Answer reconciliation questions directly.
   - Long text.

10. Do not speculate.
"""


def test_guide_sections_that_repeat_the_answer_rules_are_cut_and_the_rules_stay():
    out = slim_guide(GUIDE)
    assert "Show tables" not in out and "Do not compute deltas" not in out and "Long text" not in out
    for kept in ("Intro.", "NON-NEGOTIABLE RULES", "1. Never invent numbers.", "6. Ground every number", "10. Do not speculate."):
        assert kept in out


def test_a_guide_without_the_markers_is_returned_unchanged():
    assert slim_guide("plain rules only") == "plain rules only"


def test_nullable_wrappers_are_unwrapped_in_tool_schemas():
    schema = {"type": "object", "properties": {
        "a": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
        "b": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
        "c": {"type": "integer", "default": 3}}}
    out = slim_defs([ToolDefinition(name="t", description="d", parameters_json_schema=schema)])[0].parameters_json_schema
    assert out["properties"]["a"] == {"type": "string"}
    assert out["properties"]["b"] == {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    assert out["properties"]["c"] == {"type": "integer", "default": 3}


def test_the_metric_index_lists_metrics_per_view_without_dimensions():
    class M:
        def __init__(self, id, view, label=""):
            self.id, self.view, self.label = id, view, label
            self.supported_dimensions = ["campaign_name", "ad_platform"]
            self.raw = {"description": "Metric. Date axis: order_date; more"}

    out = metric_index(CatalogueSnapshot(metrics=[M("net_sales", "commerce"), M("orders", "commerce", "Orders (all)")]))  # type: ignore[arg-type]
    assert "## commerce [date axis: order_date]" in out and "net_sales" in out and "orders (Orders (all))" in out
    assert "campaign_name" not in out
