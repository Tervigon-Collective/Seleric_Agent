"""Analysis planner (M2.1) and executor (M2.2).

Public interface:
- ``plan(spec, read_model, *, as_of=None, headline_metric_ids=()) -> AnalysisPlan``
- ``plan_notes(...)``: the corrections the planner made, for spans and the advisory block.
- ``template_ids()``: the shapes the registry plans.
- ``execute(steps, windows, port, read_model, as_of, ...)``: run the query steps.
"""

from seleric_swarm.plan._execute import PERIOD_KEY, execute
from seleric_swarm.plan._registry import AGENT_TEMPLATE_ID, plan, plan_notes, template_ids
from seleric_swarm.plan._report import plan_report, report_as_flat_plan, single_section_report_from_spec

__all__ = [
    "AGENT_TEMPLATE_ID",
    "PERIOD_KEY",
    "execute",
    "plan",
    "plan_notes",
    "plan_report",
    "report_as_flat_plan",
    "single_section_report_from_spec",
    "template_ids",
]
