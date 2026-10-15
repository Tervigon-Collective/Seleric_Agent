"""Cube series adapter (plan P1.2): through the real fetch path it reads the same numbers as the file adapter.

The semantic layer is the eval world (``WorldMcp``): the fixture's series served in the shape the live layer returns."""

from __future__ import annotations

from datetime import date

import pytest

from seleric_swarm.diagnosis.domain.types import Scope, SeriesRequest, Window

WINDOW = Window(date(2026, 9, 1), date(2026, 9, 25))


async def test_cube_and_file_adapters_read_the_same_numbers(cube_adapter, cube_world, file_adapter):
    requests = [
        SeriesRequest("ad_spend", WINDOW),
        SeriesRequest("orders", WINDOW),
        SeriesRequest("ad_spend", WINDOW, dimension="ad_platform"),
        SeriesRequest("ad_spend", Window(date(2026, 9, 10), date(2026, 9, 20)), Scope.of({"ad_platform": "meta"})),
    ]
    got, want = await cube_adapter.fetch(requests), await file_adapter.fetch(requests)
    assert [r.error for r in got] == [None] * 4
    for a, b in zip(got, want, strict=True):
        assert dict(a.values) == pytest.approx(dict(b.values)) and set(a.segments) == set(b.segments)
        for seg in a.segments:
            assert dict(a.segments[seg]) == pytest.approx(dict(b.segments[seg]))
    assert cube_world[1].queries, "the adapter must have gone through the semantic layer"
    assert got[0].source_query != want[0].source_query and got[0].fingerprint
