"""Eval case format (plan P0.1): valid cases parse, every inconsistency is rejected with the case id."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

DIAG = Path(__file__).resolve().parents[2] / "eval" / "diag"
sys.path.insert(0, str(DIAG))
import case_format as C


def _base() -> dict:
    return json.loads((DIAG / "cases" / "_example_spend_cut.json.example").read_text())


def _bad(mutate, match: str) -> None:
    raw = copy.deepcopy(_base())
    mutate(raw)
    with pytest.raises(C.CaseError, match=match):
        C.parse_case(raw)


def test_example_case_parses():
    c = C.parse_case(_base())
    assert c.truth.causes[0].share_of_change == 0.7
    assert c.request.mode == "event" and len(c.request.event_days) == 2


def test_period_case_needs_equal_length_baseline_without_overlap():
    def one_day(r):
        r["request"]["baseline_days"] = ["2026-08-01"]
    _bad(one_day, "same length")

    def overlap(r):
        r["request"]["baseline_days"] = ["2026-09-30", "2026-10-02"]
    _bad(overlap, "overlap")

    raw = _base()
    raw["request"]["baseline_days"] = ["2026-08-01", "2026-08-02"]
    assert C.parse_case(raw).request.mode == "period"


def test_cause_class_and_expected_outcome_must_agree():
    def wrong(r):
        r["truth"]["expected"] = "measurement_artifact"
    _bad(wrong, "cannot expect")


def test_only_findable_cases_list_causes_and_findable_needs_one():
    def none_class(r):
        r["truth"].update(cause_class="none", expected="no_change")
    _bad(none_class, "only findable")

    def empty(r):
        r["truth"]["causes"] = []
    _bad(empty, "at least one cause")


def test_planted_shares_cannot_exceed_one():
    def over(r):
        r["truth"]["causes"] = [{"metric": "a", "share_of_change": 0.7}, {"metric": "b", "share_of_change": 0.5}]
    _bad(over, "add up")


def test_kind_requirements():
    def no_inj(r):
        r["injections"] = []
    _bad(no_inj, "needs injections")

    def record_no_source(r):
        r["kind"] = "record"
    _bad(record_no_source, "independent source")

    def stability_with_cause(r):
        r["kind"] = "stability"
    _bad(stability_with_cause, "unknown cause")

    def null_with_cause(r):
        r["kind"] = "null"
    _bad(null_with_cause, "cause_class none")


def test_dates_ranges_and_schema_version_are_checked():
    def bad_date(r):
        r["request"]["event_days"] = ["2026-13-01"]
    _bad(bad_date, "ISO date")

    def bad_range(r):
        r["truth"]["unexplained_share"] = [0.6, 0.2]
    _bad(bad_range, "unexplained_share")

    def version(r):
        r["schema_version"] = 2
    _bad(version, "schema_version")


def test_loader_requires_file_name_to_equal_id_and_unique_ids(tmp_path):
    raw = _base()
    (tmp_path / "other_name.json").write_text(json.dumps(raw))
    with pytest.raises(C.CaseError, match="file name"):
        C.load_cases(tmp_path)
    (tmp_path / "other_name.json").unlink()
    (tmp_path / "spend_cut_one_campaign.json").write_text(json.dumps(raw))
    assert [c.id for c in C.load_cases(tmp_path)] == ["spend_cut_one_campaign"]


def test_committed_cases_all_load():
    C.load_cases(DIAG / "cases")
