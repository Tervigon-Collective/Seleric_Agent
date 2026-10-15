"""File-backed series port (plan P1.2): reads the eval fixture, slices windows, never invents a day."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from seleric_swarm.diagnosis.adapters.fixtures import FixtureSeriesAdapter, load_series_data
from seleric_swarm.diagnosis.domain.canonical import canonical_json, series_fingerprint
from seleric_swarm.diagnosis.domain.errors import InvalidRequest
from seleric_swarm.diagnosis.domain.types import Scope, SeriesRequest, Window

FIXTURE = Path(__file__).resolve().parents[3] / "eval" / "diag" / "fixtures" / "brand20_daily_2026-10.json"
WINDOW = Window(date(2026, 9, 10), date(2026, 9, 20))


@pytest.fixture(scope="module")
def adapter() -> FixtureSeriesAdapter:
    return FixtureSeriesAdapter(load_series_data(FIXTURE))


async def test_unsplit_series_is_cut_to_the_window(adapter):
    [result] = await adapter.fetch([SeriesRequest("ad_spend", WINDOW)])
    assert result.error is None and result.segments == {}
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))["series"]["ad_spend"]
    expected = {date.fromisoformat(d): v for d, v in raw.items() if WINDOW.contains(date.fromisoformat(d))}
    assert dict(result.values) == expected and expected  # the fixture has no 2026-09-20 row: it stays absent
    assert len(expected) < WINDOW.length
    assert result.fingerprint == series_fingerprint(result.values)


async def test_split_series_and_equal_filter_agree_on_a_segment(adapter):
    [split] = await adapter.fetch([SeriesRequest("ad_spend", WINDOW, dimension="ad_platform")])
    assert split.error is None and split.values == {} and split.segments
    segment = next(iter(split.segments))
    [filtered] = await adapter.fetch([SeriesRequest("ad_spend", WINDOW, Scope.of({"ad_platform": segment}))])
    assert dict(filtered.values) == dict(split.segments[segment])


async def test_a_day_without_a_row_is_absent_not_zero(adapter):
    wide = Window(date(2026, 1, 1), date(2026, 9, 1))  # mostly before the fixture starts
    [result] = await adapter.fetch([SeriesRequest("ad_spend", wide)])
    assert result.values == {} or min(result.values) >= date(2026, 7, 17)
    assert len(result.values) < wide.length


async def test_unanswerable_requests_come_back_as_errors_with_no_numbers(adapter):
    results = await adapter.fetch([
        SeriesRequest("no_such_metric", WINDOW),
        SeriesRequest("ad_spend", WINDOW, dimension="not_a_dimension"),
        SeriesRequest("ad_spend", WINDOW, Scope.of({"ad_platform": "meta"}), dimension="campaign_name"),
        SeriesRequest("ad_spend", WINDOW, Scope.of({"ad_platform": "meta", "channel": "paid"})),
    ])
    assert [r.error is not None for r in results] == [True, True, True, True]
    assert all(r.values == {} and r.segments == {} for r in results)


def test_a_fixture_whose_data_changed_is_refused(tmp_path):
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    metric = next(iter(raw["series"]))
    day = next(iter(raw["series"][metric]))
    raw["series"][metric][day] += 1.0
    bad = tmp_path / "edited.json"
    bad.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(InvalidRequest):
        load_series_data(bad)


def test_fingerprints_ignore_insertion_order_and_negative_zero():
    a = {date(2026, 1, 2): 2.0, date(2026, 1, 1): -0.0}
    b = {date(2026, 1, 1): 0.0, date(2026, 1, 2): 2.0}
    assert series_fingerprint(a) == series_fingerprint(b)
    assert canonical_json({"b": 1, "a": [date(2026, 1, 1)]}) == '{"a":["2026-01-01"],"b":1}'
    with pytest.raises(ValueError):
        series_fingerprint({date(2026, 1, 1): float("nan")})


def test_window_and_scope_reject_nonsense():
    with pytest.raises(InvalidRequest):
        Window(date(2026, 2, 1), date(2026, 1, 1))
    with pytest.raises(InvalidRequest):
        Scope.of({"ad_platform": []})
    assert Scope.of({"b": ["y", "x"], "a": "1"}) == Scope.of({"a": ("1",), "b": ("x", "y")})


def test_a_fixture_eval_accepts_is_not_refused_for_a_negative_zero(tmp_path):
    import hashlib

    data = {"series": {"m": {"2026-01-01": -0.0, "2026-01-02": 3.0}}, "segments": {}, "definitions": {}}
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    path = tmp_path / "zero.json"
    path.write_text(json.dumps({**data, "sha256": digest}), encoding="utf-8")
    assert load_series_data(path).series["m"][date(2026, 1, 1)] == 0.0
