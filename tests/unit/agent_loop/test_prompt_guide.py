from datetime import datetime
from zoneinfo import ZoneInfo

from seleric_swarm.agent.prompt import system_prompt
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot


def _snapshot() -> CatalogueSnapshot:
    return CatalogueSnapshot(metrics=[])


def test_server_guide_sits_between_working_rules_and_answer_rules() -> None:
    now = datetime(2026, 10, 14, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    out = system_prompt(_snapshot(), now, "GUIDE-BODY")
    assert out.index("How to work") < out.index("GUIDE-BODY") < out.index("The final answer is read by")
    assert out.rstrip().rsplit("=== Calendar ===", 1)[1].strip().startswith("Today is 2026-10-14")


def test_prompt_without_guide_has_no_guide_header() -> None:
    now = datetime(2026, 10, 14, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert "Server guide" not in system_prompt(_snapshot(), now)


def test_analysis_rules_follow_the_answer_rules_once_and_the_prefix_stays_byte_stable() -> None:
    mon = datetime(2026, 10, 12, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    tue = datetime(2026, 10, 13, 15, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    a, b = system_prompt(_snapshot(), mon, "G"), system_prompt(_snapshot(), tue, "G")
    assert a.count("Analysis answers.") == 1
    assert a.index("The final answer is read by") < a.index("Analysis answers.") < a.index("=== Calendar ===")
    # prompt caching: everything before the calendar is identical from one day to the next
    assert a.split("=== Calendar ===")[0] == b.split("=== Calendar ===")[0]


def test_performance_analysis_is_not_routed_to_the_diagnosis_helper_first() -> None:
    from seleric_swarm.agent.prompt import PREAMBLE

    assert "Performance analysis" in PREAMBLE and "do not start with diagnose_metric_change" in PREAMBLE
    assert "audit_entities" in PREAMBLE
    assert "never say it paused" in PREAMBLE  # a campaign at ₹0 is "no spend in the window" until the change logs say otherwise
