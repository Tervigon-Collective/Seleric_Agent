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
