"""A piece of a hyphenated name is not a value the user named (live 2026-10-10 thread_d1844eb0: "…-26SEP-ADV+" gave
an exact match for an ad set called "Adv+", and the scope gate demanded the answer be filtered to it)."""

from __future__ import annotations

from seleric_swarm.agent.runner import _fragment_terms, _without_terms


def _res(*terms: str) -> dict:
    return {"status": "ok", "terms": [{"term": t, "best_match": "exact", "dimensions": []} for t in terms]}


def test_a_word_written_only_inside_a_name_is_a_fragment():
    q = "TH-383-SUSPENDER-29SEP , TH-383-SUSPENDER-26SEP-ADV+ , check all the metrics for the last 3 days"
    assert _fragment_terms(q, _res("adv", "suspender", "check", "all", "suspender 26sep adv")) == {"adv", "suspender"}


def test_a_word_the_user_wrote_on_its_own_stays():
    assert _fragment_terms("show ADV+ campaigns and TH-383-SUSPENDER-29SEP", _res("adv", "campaigns", "suspender")) == {"suspender"}
    assert _fragment_terms("Meta campaigns, not the META-ADV+ ones", _res("meta", "adv")) == {"adv"}


def test_a_term_the_question_does_not_contain_is_kept():
    # the gateway may stem or case-fold a word; only a provable fragment is dropped
    assert _fragment_terms("how are the campaigns doing", _res("campaign")) == frozenset()


def test_the_fragment_leaves_the_resolution_without_its_filter():
    res = _res("adv", "check")
    kept = _without_terms(res, _fragment_terms("TH-383-SUSPENDER-26SEP-ADV+ check", res))
    assert [t["term"] for t in kept["terms"]] == ["check"]
