"""Follow-ups keep the prior period; a bare "yes" does what the prior answer offered.

Live thread_e75c2615: "yes" to "dig into channel or landing-page behaviour?" re-ran the
same diagnosis on a different window and never drilled by channel."""

from __future__ import annotations

from types import SimpleNamespace

from seleric_swarm.agent.runner import (
    _answer_period,
    _closing_offer,
    _followup_hint,
    _latest_turn_record,
    _names_a_period,
    _prior_window,
    _render_turn_record,
    _write_turn_record,
)
from seleric_swarm.state.artifacts import InMemoryArtifactStore

ANSWER = (
    "Conversion rate declined modestly to 0.99%.\n\n"
    "Period: 2026-09-28 to 2026-10-03 · Data as of 2026-10-04\n\n"
    "Would you like me to dig into channel or landing-page-level behaviour next?"
)


def test_answer_period_and_offer_are_read_from_the_answer():
    assert _answer_period(ANSWER) == ("2026-09-28", "2026-10-03")
    assert _answer_period("Period: 2026-09-21..2026-09-27 · Currency: INR") == ("2026-09-21", "2026-09-27")
    assert _answer_period("Period: 2026-09-24 · Currency: INR") == ("2026-09-24", "2026-09-24")
    assert _answer_period("no footer") is None
    assert _closing_offer(ANSWER).startswith("Would you like me to dig into channel")
    assert _closing_offer("Done.\n\nTotal was 5.") == ""


def test_turn_record_carries_period_and_offer():
    store = InMemoryArtifactStore()
    result = SimpleNamespace(status="completed", final_response=ANSWER, evidence_ids=[], query="why")
    _write_turn_record(
        result=result,
        classification=SimpleNamespace(intent="diagnosis", period="none", grain="none"),
        artifact_store=store,
        thread_id="t1",
        workspace_id="ws1",
        mission_id="m1",
        as_of_dt=__import__("datetime").datetime(2026, 10, 4),
    )
    record = next(a.payload for a in store.list_for_mission("m1") if a.artifact_type == "turn_record")
    assert (record["period_start"], record["period_end"]) == ("2026-09-28", "2026-10-03")
    assert record["offer"].startswith("Would you like")
    assert "period_used=2026-09-28..2026-10-03" in _render_turn_record(record)
    # The next turn finds it by thread id. The V3 store had no list_for_context, so
    # this lookup returned None in every live mission and follow-ups lost the period.
    found = _latest_turn_record(store, thread_id="t1")
    assert found is not None and found["offer"].startswith("Would you like")
    assert _latest_turn_record(store, thread_id="other-thread") is None


def test_yes_carries_the_offer_and_the_period():
    record = {"period_start": "2026-09-28", "period_end": "2026-10-03", "offer": "Would you like a channel drill?"}
    assert not _names_a_period("yes", "Asia/Kolkata", "2026-10-04")
    hint = _followup_hint(record, affirmation=True, window=_prior_window(record))
    assert "2026-09-28..2026-10-03" in hint and "Would you like a channel drill?" in hint
    # Not an acceptance: the period default stays, the offer is not imposed.
    hint = _followup_hint(record, affirmation=False, window=_prior_window(record))
    assert "2026-09-28..2026-10-03" in hint and "offer" not in hint
    assert _followup_hint(None, affirmation=True, window=None) == ""


def test_a_follow_up_naming_its_own_period_gets_no_prior_period():
    assert _names_a_period("and yesterday?", "Asia/Kolkata", "2026-10-04")
    assert _names_a_period("same for last 30 days", "Asia/Kolkata", "2026-10-04")
