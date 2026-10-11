"""DoWhy-GCM attribution: the adapter on a planted chain, and the controller's attributor hook."""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from seleric_swarm.adapters.gcm import gcm_distribution_change
from seleric_swarm.investigate import Investigation
from tests.unit.investigate.test_controller import GRAPH, MODEL, Reader, facts, req


def test_gcm_attributes_change_to_the_shifted_parent() -> None:
    pytest.importorskip("dowhy")
    rng = random.Random(7)
    days = [date(2026, 8, 1) + timedelta(days=i) for i in range(40)]
    old, new = days[:33], days[33:]
    a = {d: rng.gauss(100, 3) + (30 if d in new else 0) for d in days}  # a shifts in the event
    b = {d: rng.gauss(50, 3) for d in days}
    y = {d: a[d] + b[d] + rng.gauss(0, 1) for d in days}
    shares = gcm_distribution_change([("a", "y"), ("b", "y")], {"a": a, "b": b, "y": y}, "y", old, new, num_samples=200)
    assert shares and max(shares, key=lambda k: abs(shares[k])) == "a"


def test_too_little_history_is_no_attribution() -> None:
    d = [date(2026, 8, 1) + timedelta(days=i) for i in range(5)]
    series = {"a": dict.fromkeys(d, 1.0), "y": dict.fromkeys(d, 2.0)}
    assert gcm_distribution_change([("a", "y")], series, "y", d[:3], d[3:]) == {}


async def test_controller_records_attribution() -> None:
    async def attributor(*, target, baseline, event, filters):
        return {"zz_visits": 0.7, target: 0.3}

    st = await Investigation(Reader(*facts({("north", "mob"): 10.0})), MODEL, GRAPH, attributor=attributor).run(req())
    assert [(f.subject, f.verdict) for f in st.findings if f.verdict == "gcm_attribution"] == [("zz_visits", "gcm_attribution")]
