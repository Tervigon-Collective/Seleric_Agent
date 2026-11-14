from datetime import datetime
from zoneinfo import ZoneInfo

from seleric_swarm.agent import prompt_modules as pm
from seleric_swarm.agent.prompt import PREAMBLE, system_prompt
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

NOW = datetime(2026, 10, 31, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


def _one_view(view: str) -> CatalogueSnapshot:
    return CatalogueSnapshot(metrics=(CatalogueMetricMeta(id="m", view=view),))


def test_only_the_domain_whose_view_is_in_the_catalogue_is_in_the_prompt() -> None:
    out = system_prompt(_one_view("paid_media"), NOW)
    assert "**Paid media**" in out and "=== Domain notes" in out
    assert all(tag not in out for tag in ("**Commerce**", "**Finance / P&L**", "**Product**"))


def test_no_catalogue_means_no_domain_section() -> None:
    assert "Domain notes" not in system_prompt(CatalogueSnapshot(metrics=()), NOW)


def test_domain_notes_come_after_the_rules_and_before_the_snapshot_and_calendar() -> None:
    out = system_prompt(_one_view("commerce"), NOW)
    assert out.index("Analysis answers.") < out.index("=== Domain notes") < out.index("=== Catalogue snapshot") < out.index("=== Calendar")


def test_every_domain_declares_views_and_every_module_has_a_body() -> None:
    for sub in ("behaviour", "playbooks", "domains"):
        assert pm.folder(sub), sub
        assert all(m.body for m in pm.folder(sub))
    assert all(m.views for m in pm.folder("domains"))
    owners = [v for m in pm.folder("domains") for v in m.views]
    assert len(owners) == len(set(owners)), "a view belongs to one domain"


def test_the_dependency_refusal_and_row_level_rules_are_in_the_working_rules() -> None:
    assert "never in the same batch" in PREAMBLE
    assert "not a limit of the data" in PREAMBLE
    assert "Row-level and pairwise" in PREAMBLE


def test_the_overall_picture_comes_before_the_parts() -> None:
    assert "Whole before parts" in PREAMBLE and "the largest first" in PREAMBLE
    assert "and decisions about them" in PREAMBLE  # a decision about campaigns/products/channels gets the analysis form
