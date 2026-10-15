"""Eval fixtures (plan P0.2): the committed fixture verifies, and tampering or a stale pin is refused."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

DIAG = Path(__file__).resolve().parents[2] / "eval" / "diag"
sys.path.insert(0, str(DIAG))
import world_fixture as F

FIXTURE_ID = "brand20_daily_2026-10"


def test_committed_fixture_verifies_and_is_complete_enough():
    fx = F.load_fixture(FIXTURE_ID)
    assert fx.brand == 20 and fx.catalogue_version
    assert (fx.end - fx.start).days + 1 >= 85
    assert {"ad_spend", "orders", "net_sales"} <= set(fx.series)
    assert all(len(s) >= 80 for m, s in fx.series.items() if m in ("orders", "net_sales"))
    assert fx.segments["ad_spend"]["campaign_name"]


def test_tampered_fixture_is_refused(tmp_path):
    raw = json.loads((F.FIXTURES / f"{FIXTURE_ID}.json").read_text())
    day = next(iter(raw["series"]["orders"]))
    raw["series"]["orders"][day] += 1
    (tmp_path / "x.json").write_text(json.dumps(raw))
    with pytest.raises(F.FixtureError, match="sha256"):
        F.load_fixture("x", root=tmp_path)


def test_stale_pin_is_refused():
    with pytest.raises(F.FixtureError, match="pins"):
        F.load_fixture(FIXTURE_ID, pinned_sha256="0" * 64)
