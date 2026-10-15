"""Offline runner (plan P0.7): the real diagnosis tool runs on a planted world with no network."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

DIAG = Path(__file__).resolve().parents[2] / "eval" / "diag"
sys.path.insert(0, str(DIAG))
import case_format as C
import inject as I
import runner as R
import world_fixture as W

pytestmark = pytest.mark.filterwarnings("ignore")
FIXTURE = "brand20_daily_2026-10"


def _case(**over):
    fx = W.load_fixture(FIXTURE)
    onset = fx.end - timedelta(days=1)
    raw = {"id": "probe", "kind": "null", "question": "why?", "request": {"outcome": "orders", "event_days": [str(fx.end)], "claimed_direction": "down"},
           "world": {"fixture": FIXTURE}, "truth": {"cause_class": "none", "expected": "no_change"}}
    raw.update(over)
    return C.parse_case(raw), onset


def test_world_mcp_serves_series_segments_and_equals_filters():
    fx = W.load_fixture(FIXTURE)
    world = I.InjectedWorld(fx.series, fx.segments)
    mcp = R.WorldMcp(world, fx.definitions)
    day = str(fx.end)
    base = {"measures": ["orders"], "time_range": {"start": day, "end": day}, "granularity": "day"}
    total = mcp._query(base)["rows"][0]["orders"]
    seg = mcp._query({**base, "dimensions": ["channel"]})["rows"]
    assert sum(r["orders"] for r in seg) == pytest.approx(total, rel=0.2)  # segments cover most of the total
    one = mcp._query({**base, "filters": [{"dimension": "channel", "operator": "equals", "values": [seg[0]["channel"]]}]})["rows"][0]["orders"]
    assert one == pytest.approx(seg[0]["orders"])
    assert "error" in mcp._query({**base, "dimensions": ["no_such_dimension"]})


def test_null_case_runs_the_real_tool_offline():
    case, _ = _case()
    res = R.run_case(case)
    assert res.success and not res.error and res.queries > 5
    assert res.report["outcome"] == "orders" and res.report["verdict"]


def test_injection_that_never_reaches_the_outcome_is_refused():
    fx = W.load_fixture(FIXTURE)
    on = fx.end - timedelta(days=1)
    case, _ = _case(kind="injection",
                    injections=[{"type": "scale", "onset": str(on), "params": {"metric": "ad_spend", "factor": 0.5}}],
                    truth={"cause_class": "findable", "expected": "cause_found", "causes": [{"metric": "ad_spend"}]})
    with pytest.raises(I.InjectionError, match="never reaches"):
        R.run_case(case)


def test_tree_state_reports_the_engine_git_state():
    st = R.tree_state()
    assert len(st["head"]) == 40 and isinstance(st["engine_dirty"], list)


def test_perturbations_shift_the_window_and_subsample_history_without_touching_event_days():
    case, _ = _case()
    shifted = R.perturb(case, -2)
    assert shifted.request.event_days == tuple(d - timedelta(days=2) for d in case.request.event_days)
    assert R.perturb(case, 0) is case
    fx = W.load_fixture(FIXTURE)
    world = I.InjectedWorld({m: dict(s) for m, s in fx.series.items()}, {})
    keep = set(case.request.event_days)
    n = R.subsample(world, keep, 0.2, seed=1)
    assert n > 10 and all(d in world.series["orders"] for d in keep)
    assert len(world.series["orders"]) == len(fx.series["orders"]) - sum(1 for d in fx.series["orders"] if d not in world.series["orders"])
    world2 = I.InjectedWorld({m: dict(s) for m, s in fx.series.items()}, {})
    R.subsample(world2, keep, 0.2, seed=1)
    assert world.series["orders"].keys() == world2.series["orders"].keys()  # seeded: same days dropped
