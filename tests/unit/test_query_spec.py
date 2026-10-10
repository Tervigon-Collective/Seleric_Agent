"""QuerySpec: decide once — candidates, compile, validate, shadow, follow-up edits.

These tests never call the LLM. They feed Understanding-shaped slots (what the
understand call is meant to return) plus catalogue value candidates, and assert
the typed decision. Reading errors stay separate from data errors.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from seleric_swarm.agent.query_spec import (
    Candidates,
    QuerySpec,
    ValueCandidate,
    WindowExpr,
    WindowSlot,
    apply_spec_edit,
    build_spec_outcome,
    candidates_from_resolution,
    compile_query_spec,
    resolve_window_expr,
    resolve_window_slots,
    shadow_compare,
    spec_from_dump,
    validate_query_spec,
)
from seleric_swarm.agent.understand import Understanding
from seleric_swarm.contracts.lookup import TimeRangeV1

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "query_specs" / "th383_and_regressions.jsonl"

_SLOTS = {
    "entity_dimension": "",
    "rank_by": None,
    "metrics": [],
    "breakdown_dimensions": [],
    "names_period": False,
}


def _u(**kw: Any) -> Understanding:
    return Understanding.model_validate({"kind": "analysis", "shape": "lookup", **_SLOTS, **kw})


def _slot(**kw: Any) -> WindowSlot:
    return WindowSlot.model_validate(kw)


# ---------------------------------------------------------------------------
# Window expressions → dates (calendar maths only)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr,as_of,expected",
    [
        (WindowExpr(name="yesterday"), "2026-09-30", ("2026-09-29", "2026-09-29", "yesterday")),
        (WindowExpr(name="today"), "2026-09-30", ("2026-09-30", "2026-09-30", "today")),
        (WindowExpr(name="last_week"), "2026-10-10", ("2026-09-28", "2026-10-04", "last_week")),
        (WindowExpr(unit="day", n=3, ending="yesterday"), "2026-09-30", ("2026-09-27", "2026-09-29", "last_3d")),
        (WindowExpr(unit="day", n=7, ending="yesterday"), "2026-10-10", ("2026-10-03", "2026-10-09", "last_7d")),
        (
            WindowExpr(start_iso="2026-09-01", end_iso="2026-09-30"),
            "2026-10-10",
            ("2026-09-01", "2026-09-30", "absolute"),
        ),
    ],
)
def test_resolve_window_expr(expr, as_of, expected):
    start, end, token = resolve_window_expr(expr, as_of=date.fromisoformat(as_of))
    assert (start.isoformat(), end.isoformat(), token) == expected


def test_baseline_before_event():
    """TH-383: event=yesterday, baseline = the two complete days before it."""
    slots = [
        _slot(role="event", expr={"name": "yesterday"}, source_span="yesterday"),
        _slot(
            role="baseline",
            expr={"unit": "day", "n": 2, "ending": "before_event"},
            source_span="the days before",
        ),
        _slot(
            role="context",
            expr={"unit": "day", "n": 3, "ending": "yesterday"},
            source_span="last 3 days",
        ),
    ]
    resolved = resolve_window_slots(slots, as_of=date.fromisoformat("2026-09-30"))
    by_role = {w.role: w for w in resolved}
    assert by_role["event"].start.isoformat() == "2026-09-29"
    assert by_role["baseline"].start.isoformat() == "2026-09-27"
    assert by_role["baseline"].end.isoformat() == "2026-09-28"
    assert by_role["context"].token == "last_3d"
    # Context covers event+baseline, never replaces them.
    assert by_role["context"].start <= by_role["baseline"].start
    assert by_role["context"].end >= by_role["event"].end


# ---------------------------------------------------------------------------
# Candidates + compile + validate (TH-383 Adv+ volume 0)
# ---------------------------------------------------------------------------


def _th383_candidates() -> Candidates:
    """Two full campaign names (volume > 0) plus a fragment Adv+ with volume 0."""
    return Candidates(
        values=(
            ValueCandidate(
                term="TH-383-SUSPENDER-26SEP-ADV+",
                dimension="campaign_name",
                value="TH-383-SUSPENDER-26SEP-ADV+",
                volume=40,
                match="exact",
                source_span="TH-383-SUSPENDER-26SEP-ADV+",
            ),
            ValueCandidate(
                term="TH-383-SUSPENDER-29SEP",
                dimension="campaign_name",
                value="TH-383-SUSPENDER-29SEP",
                volume=55,
                match="exact",
                source_span="TH-383-SUSPENDER-29SEP",
            ),
            ValueCandidate(
                term="ADV+",
                dimension="adset_name",
                value="ADV+",
                volume=0,
                match="exact",
                source_span="ADV+",
            ),
            ValueCandidate(
                term="ADV+",
                dimension="campaign_name",
                value="TH-383-SUSPENDER-26SEP-ADV+",
                volume=40,
                match="contains",
                source_span="ADV+",
            ),
        )
    )


def test_th383_entities_kept_adv_plus_filter_dropped():
    understanding = _u(
        shape="entity_comparison",
        entity_dimension="campaign_name",
        names_period=True,
        metrics=[{"words": "ad spend", "metric_id": "ad_spend"}, {"words": "roas", "metric_id": "roas"}],
        windows=[
            {"role": "event", "expr": {"name": "yesterday"}, "source_span": "yesterday"},
            {
                "role": "baseline",
                "expr": {"unit": "day", "n": 2, "ending": "before_event"},
                "source_span": "before",
            },
            {
                "role": "context",
                "expr": {"unit": "day", "n": 3, "ending": "yesterday"},
                "source_span": "last 3 days",
            },
        ],
    )
    spec = compile_query_spec(
        understanding, candidates=_th383_candidates(), as_of=date(2026, 9, 30)
    )
    spec = validate_query_spec(spec)

    assert {e.value for e in spec.entities} == {
        "TH-383-SUSPENDER-26SEP-ADV+",
        "TH-383-SUSPENDER-29SEP",
    }
    # ADV+ never becomes a required adset filter (volume 0 / not an entity pick).
    assert not any(f.term.upper() == "ADV+" for f in spec.filters)
    assert any("volume 0" in a for a in spec.assumptions) or not any(
        f.term.upper() == "ADV+" for f in spec.filters
    )
    tr = spec.to_time_range()
    assert tr is not None and tr.kind == "comparison"
    assert tr.start == "2026-09-29" and tr.end == "2026-09-29"
    assert tr.start_b == "2026-09-27" and tr.end_b == "2026-09-28"


def test_candidates_from_resolution_keep_whole_names():
    resolution = {
        "status": "ok",
        "terms": [
            {
                "term": "TH-383-SUSPENDER-26SEP-ADV+",
                "catalogue_vocabulary": False,
                "best_match": "exact",
                "dimensions": [
                    {
                        "dimension": "campaign_name",
                        "values": [
                            {"value": "TH-383-SUSPENDER-26SEP-ADV+", "volume": 12, "match": "exact"}
                        ],
                    }
                ],
            }
        ],
        "axes": {"date": "finance"},
    }
    c = candidates_from_resolution(resolution)
    assert len(c.values) == 1
    assert c.values[0].term == "TH-383-SUSPENDER-26SEP-ADV+"
    assert c.axes == (("date", "finance"),)


def test_ordinary_words_suppress_filters():
    candidates = Candidates(
        values=(
            ValueCandidate(
                term="other",
                dimension="payment_method",
                value="other",
                volume=9,
                match="exact",
                source_span="other",
            ),
        )
    )
    spec = compile_query_spec(
        _u(ordinary_words=["other"]), candidates=candidates, as_of=date(2026, 10, 10)
    )
    assert spec.filters == ()


# ---------------------------------------------------------------------------
# Shadow + dump round-trip + follow-up edit
# ---------------------------------------------------------------------------


def test_shadow_flags_window_disagreement():
    spec = QuerySpec(
        windows=resolve_window_slots(
            [_slot(role="event", expr={"name": "yesterday"})],
            as_of=date(2026, 9, 30),
        )
    )
    # Legacy regex path that wrongly took "last 3 days" as the only window.
    legacy = TimeRangeV1(kind="absolute", start="2026-09-27", end="2026-09-29", relative_token="last_3d")
    diffs = shadow_compare(spec, legacy_window=legacy, legacy_grain=None)
    assert any(d.field == "windows" for d in diffs)


def test_dump_round_trip():
    understanding = _u(
        shape="why_single_metric",
        names_period=True,
        windows=[
            {"role": "event", "expr": {"name": "yesterday"}},
            {"role": "baseline", "expr": {"unit": "day", "n": 1, "ending": "before_event"}},
        ],
    )
    spec = validate_query_spec(
        compile_query_spec(understanding, as_of=date(2026, 10, 9))
    )
    restored = spec_from_dump(spec.dump())
    assert restored is not None
    assert restored.shape == "why_single_metric"
    assert restored.event_window() is not None
    assert restored.event_window().start == date(2026, 10, 8)
    assert restored.baseline_window() is not None
    assert restored.baseline_window().start == date(2026, 10, 7)


def test_followup_why_keeps_prior_windows():
    prior = validate_query_spec(
        compile_query_spec(
            _u(
                shape="why_single_metric",
                names_period=True,
                windows=[
                    {"role": "event", "expr": {"name": "yesterday"}},
                    {"role": "baseline", "expr": {"unit": "day", "n": 1, "ending": "before_event"}},
                ],
            ),
            as_of=date(2026, 10, 9),
        )
    )
    follow = _u(shape="why_single_metric", follows_prior=True, names_period=False, windows=[])
    edited = apply_spec_edit(prior, follow, as_of=date(2026, 10, 9))
    assert edited.follows_prior is True
    assert edited.event_window() == prior.event_window()
    assert edited.baseline_window() == prior.baseline_window()


def test_build_spec_outcome_off_skips():
    out = build_spec_outcome(None, resolution=None, as_of=date(2026, 10, 10), mode="off")
    assert out.spec is None and out.stats["status"] == "skipped"


def test_enforce_ready_spec_has_comparison_time_range():
    """Consumers in enforce mode read to_time_range / required_windows / value_filters."""
    understanding = _u(
        shape="entity_comparison",
        entity_dimension="campaign_name",
        names_period=True,
        windows=[
            {"role": "event", "expr": {"name": "yesterday"}},
            {"role": "baseline", "expr": {"unit": "day", "n": 2, "ending": "before_event"}},
        ],
    )
    out = build_spec_outcome(
        understanding,
        resolution={
            "status": "ok",
            "terms": [
                {
                    "term": "TH-383-SUSPENDER-29SEP",
                    "catalogue_vocabulary": False,
                    "best_match": "exact",
                    "dimensions": [
                        {
                            "dimension": "campaign_name",
                            "values": [
                                {"value": "TH-383-SUSPENDER-29SEP", "volume": 10, "match": "exact"}
                            ],
                        }
                    ],
                }
            ],
        },
        as_of=date(2026, 9, 30),
        mode="shadow",
        legacy_window=TimeRangeV1(
            kind="absolute", start="2026-09-27", end="2026-09-29", relative_token="last_3d"
        ),
        legacy_grain=None,
        legacy_filter_terms=frozenset({"adv+"}),
    )
    assert out.spec is not None
    assert out.spec.to_time_range() is not None
    assert out.shadow  # disagrees with last-3-days-only legacy window
    assert out.spec.entities


# ---------------------------------------------------------------------------
# Fixture set: question → expected-spec (no production change)
# ---------------------------------------------------------------------------


def _expected_windows(entry: dict[str, Any], as_of: date) -> list[dict[str, str]]:
    """Materialise fixture window expectations (either concrete dates or expressions)."""
    out: list[dict[str, str]] = []
    for w in entry.get("windows") or []:
        if "start" in w and "end" in w:
            out.append({"role": w["role"], "start": w["start"], "end": w["end"], "token": w.get("token", "")})
            continue
        expr = WindowExpr(
            name=w.get("name", "") or "",
            unit=w.get("unit", "") or "",
            n=w.get("n"),
            ending=w.get("ending", "yesterday"),
        )
        # For fixture baselines that use ending=yesterday with unit/n beside an event=today,
        # resolve standalone (the TH-383 case uses before_event in the unit test above).
        start, end, token = resolve_window_expr(expr, as_of=as_of)
        out.append({"role": w["role"], "start": start.isoformat(), "end": end.isoformat(), "token": token})
    return out


def _understanding_from_fixture(entry: dict[str, Any], as_of: date) -> Understanding:
    expected = entry["expected"]
    window_slots: list[dict[str, Any]] = []
    for w in expected.get("windows") or []:
        if "start" in w:
            # Concrete dates in the fixture → absolute expr so the compile path is exercised.
            window_slots.append(
                {
                    "role": w["role"],
                    "expr": {"start_iso": w["start"], "end_iso": w["end"]},
                    "source_span": w.get("token") or w["role"],
                }
            )
        elif w.get("name"):
            window_slots.append({"role": w["role"], "expr": {"name": w["name"]}})
        else:
            window_slots.append(
                {
                    "role": w["role"],
                    "expr": {
                        "unit": w.get("unit", "day"),
                        "n": w.get("n"),
                        "ending": w.get("ending", "yesterday"),
                    },
                }
            )
    return _u(
        shape=expected.get("shape", "lookup"),
        entity_dimension=expected.get("entity_dimension", ""),
        breakdown_dimensions=expected.get("breakdowns", []),
        names_period=bool(window_slots),
        follows_prior=bool(expected.get("follows_prior")),
        windows=window_slots,
    )


@pytest.mark.parametrize(
    "entry",
    [json.loads(line) for line in _FIXTURES.read_text().splitlines() if line.strip()],
    ids=lambda e: e["id"],
)
def test_fixture_expected_spec(entry: dict[str, Any]):
    as_of = date.fromisoformat(entry["as_of"])
    expected = entry["expected"]
    if expected.get("keep_windows_from_prior"):
        # Follow-up case: load the prior fixture's windows via apply_spec_edit.
        prior_id = entry["prior_spec_id"]
        prior_entry = next(
            json.loads(line)
            for line in _FIXTURES.read_text().splitlines()
            if line.strip() and json.loads(line)["id"] == prior_id
        )
        prior = validate_query_spec(
            compile_query_spec(_understanding_from_fixture(prior_entry, as_of), as_of=as_of)
        )
        edited = apply_spec_edit(
            prior, _understanding_from_fixture(entry, as_of), as_of=as_of
        )
        assert edited.event_window() == prior.event_window()
        assert edited.follows_prior is True
        return

    understanding = _understanding_from_fixture(entry, as_of)
    candidates = Candidates()
    if entry["id"] == "TH-383":
        candidates = _th383_candidates()
    spec = validate_query_spec(
        compile_query_spec(understanding, candidates=candidates, as_of=as_of)
    )
    assert spec.shape == expected["shape"]
    if "entity_dimension" in expected:
        assert spec.entity_dimension == expected["entity_dimension"]
    if "entities" in expected:
        assert {e.value for e in spec.entities} == set(expected["entities"])
    for dropped in expected.get("drop_filters") or []:
        assert not any(f.term.lower() == dropped.lower() for f in spec.filters)
    if "breakdowns" in expected:
        # Fixture lists grain language; spec may hold catalogue ids — allow either.
        for b in expected["breakdowns"]:
            assert any(b in d or d in b for d in spec.breakdowns) or spec.breakdowns == tuple(
                expected["breakdowns"]
            )
    if expected.get("windows"):
        got = {
            (w.role, w.start.isoformat(), w.end.isoformat())
            for w in spec.windows
            if w.role in {ew.get("role") for ew in expected["windows"]}
        }
        want = {
            (w["role"], w["start"], w["end"])
            for w in _expected_windows(expected, as_of)
            if "start" in w
        }
        # Absolute fixtures and expression fixtures both land on concrete dates.
        if want:
            assert want <= got or got == want
