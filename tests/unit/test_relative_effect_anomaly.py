"""Contract tests for RelativeEffectAnomalyDetector.

Rules enforced:
* No hardcoded threshold: score is a smooth monotone function of |change|.
* Score is invariant to which direction (up/down) the deviation moves.
* Readings without a baseline are skipped and reported via context, not
  fabricated into "anomaly at zero".
* Findings preserve provenance from their input MetricReadings.
* Findings are ranked by adversity_score (descending) then magnitude_score.
* Adversity scoring: adversity_score = magnitude when move is in direction_bad.
* expected_range is empty (no variance info from point baseline).
"""

from __future__ import annotations

import pytest

from seleric_swarm.swarm.providers.base import MetricReading
from seleric_swarm.swarm.providers.template import RelativeEffectAnomalyDetector


def _reading(metric_id: str, value: float, baseline: float | None, **kw) -> MetricReading:
    return MetricReading(
        metric_id=metric_id,
        value=value,
        baseline=baseline,
        unit="ratio",
        dimensions=kw.pop("dimensions", {}),
        direction_bad=kw.pop("direction_bad", "up"),
        data_origin=kw.pop("data_origin", "MCP"),
        synthetic=kw.pop("synthetic", False),
        source=kw.pop("source", "test"),
    )


@pytest.mark.asyncio
async def test_score_is_monotone_in_absolute_change() -> None:
    detector = RelativeEffectAnomalyDetector()
    readings = [
        _reading("m.small", value=1.05, baseline=1.0),   # +5%
        _reading("m.medium", value=1.50, baseline=1.0),  # +50%
        _reading("m.large", value=3.00, baseline=1.0),   # +200%
    ]
    findings = await detector.detect(readings, context={})
    scores = {f.metric_id: f.score for f in findings}
    assert scores["m.large"] > scores["m.medium"] > scores["m.small"] > 0


@pytest.mark.asyncio
async def test_score_symmetric_under_sign_flip() -> None:
    detector = RelativeEffectAnomalyDetector()
    up = await detector.detect([_reading("m", value=1.30, baseline=1.0)], context={})
    down = await detector.detect([_reading("m", value=0.70, baseline=1.0)], context={})
    assert up[0].score == pytest.approx(down[0].score)
    assert up[0].direction == "up"
    assert down[0].direction == "down"


@pytest.mark.asyncio
async def test_no_baseline_is_reported_not_fabricated() -> None:
    detector = RelativeEffectAnomalyDetector()
    ctx: dict = {}
    findings = await detector.detect(
        [
            _reading("m.has_baseline", value=1.20, baseline=1.0),
            _reading("m.no_baseline", value=2.00, baseline=None),
            _reading("m.zero_baseline", value=2.00, baseline=0.0),
        ],
        context=ctx,
    )
    assert {f.metric_id for f in findings} == {"m.has_baseline"}
    assert set(ctx.get("no_baseline_metrics", [])) == {"m.no_baseline", "m.zero_baseline"}


@pytest.mark.asyncio
async def test_findings_are_ranked_by_adversity_then_magnitude() -> None:
    """Adverse moves (in direction_bad) rank before favorable ones."""
    detector = RelativeEffectAnomalyDetector()
    findings = await detector.detect(
        [
            # direction_bad=up by default, so "up" moves are adverse
            _reading("m.a", value=1.10, baseline=1.0),  # +10% up = adverse
            _reading("m.b", value=2.00, baseline=1.0),  # +100% up = adverse, larger
            _reading("m.c", value=0.50, baseline=1.0),  # -50% down = favorable
        ],
        context={},
    )
    # m.b (adverse +100%) first, then m.c (favorable -50%), then m.a (adverse +10%)
    # Wait, m.a is also adverse. So order should be m.b, m.a, m.c by adversity.
    # m.b: adversity=0.5, m.a: adversity=0.09..., m.c: adversity=0
    ids = [f.metric_id for f in findings]
    # Adverse moves first (m.b > m.a), then favorable (m.c)
    assert ids == ["m.b", "m.a", "m.c"]
    # Verify adversity scores
    assert findings[0].adverse is True
    assert findings[1].adverse is True
    assert findings[2].adverse is False
    assert findings[2].adversity_score == 0.0


@pytest.mark.asyncio
async def test_provenance_is_preserved() -> None:
    detector = RelativeEffectAnomalyDetector()
    findings = await detector.detect(
        [
            _reading("m.x", value=1.20, baseline=1.0, data_origin="MCP", synthetic=False),
            _reading("m.y", value=1.20, baseline=1.0, data_origin="FIXTURE", synthetic=True),
        ],
        context={},
    )
    origins = {f.metric_id: (f.data_origin, f.synthetic) for f in findings}
    assert origins["m.x"] == ("MCP", False)
    assert origins["m.y"] == ("FIXTURE", True)


@pytest.mark.asyncio
async def test_detector_metadata_is_stamped_on_every_finding() -> None:
    detector = RelativeEffectAnomalyDetector(name="unit_test")
    findings = await detector.detect(
        [_reading("m.x", value=1.10, baseline=1.0)], context={}
    )
    assert findings[0].detector["name"] == "unit_test"
    assert findings[0].detector["method"] == "relative_effect_size"
    assert findings[0].detector["baseline_source"] == "provider_compare_period"


@pytest.mark.asyncio
async def test_zero_change_still_scores_zero_not_none() -> None:
    detector = RelativeEffectAnomalyDetector()
    findings = await detector.detect(
        [_reading("m.flat", value=1.0, baseline=1.0)], context={}
    )
    assert findings and findings[0].score == 0.0
    assert findings[0].direction == "unknown"
    assert findings[0].deviation_pct == 0.0


@pytest.mark.asyncio
async def test_empty_input_returns_empty_findings() -> None:
    detector = RelativeEffectAnomalyDetector()
    assert await detector.detect([], context={}) == []


@pytest.mark.asyncio
async def test_start_time_passes_through_from_context() -> None:
    detector = RelativeEffectAnomalyDetector()
    findings = await detector.detect(
        [_reading("m.x", value=1.10, baseline=1.0)],
        context={"degradation_started_at": "2026-09-01T00:00:00Z"},
    )
    assert findings[0].start_time == "2026-09-01T00:00:00Z"


@pytest.mark.asyncio
async def test_expected_range_is_empty_when_no_variance_info() -> None:
    """Point baseline cannot produce a variance band; expected_range is empty."""
    detector = RelativeEffectAnomalyDetector()
    findings = await detector.detect(
        [_reading("m.x", value=1.20, baseline=1.0)], context={}
    )
    assert findings[0].expected_range == []


@pytest.mark.asyncio
async def test_adversity_score_zero_when_move_is_favorable() -> None:
    """CAC going down is favorable when direction_bad=up."""
    detector = RelativeEffectAnomalyDetector()
    # direction_bad=up (default), so down is favorable
    findings = await detector.detect(
        [_reading("m.cac", value=0.70, baseline=1.0, direction_bad="up")], context={}
    )
    f = findings[0]
    assert f.direction == "down"
    assert f.direction_bad == "up"
    assert f.adverse is False
    assert f.adversity_score == 0.0
    assert f.magnitude_score > 0  # still has magnitude


@pytest.mark.asyncio
async def test_adversity_score_equals_magnitude_when_move_is_adverse() -> None:
    """CAC going up is adverse when direction_bad=up."""
    detector = RelativeEffectAnomalyDetector()
    findings = await detector.detect(
        [_reading("m.cac", value=1.30, baseline=1.0, direction_bad="up")], context={}
    )
    f = findings[0]
    assert f.direction == "up"
    assert f.adverse is True
    assert f.adversity_score == f.magnitude_score


@pytest.mark.asyncio
async def test_direction_bad_down_inverts_adversity() -> None:
    """For metrics where down is bad (e.g. revenue), up is favorable."""
    detector = RelativeEffectAnomalyDetector()
    findings = await detector.detect(
        [
            _reading("m.rev", value=0.70, baseline=1.0, direction_bad="down"),  # down=bad
            _reading("m.rev2", value=1.30, baseline=1.0, direction_bad="down"),  # up=favorable
        ],
        context={},
    )
    by_id = {f.metric_id: f for f in findings}
    # Revenue down 30% is adverse
    assert by_id["m.rev"].direction == "down"
    assert by_id["m.rev"].adverse is True
    assert by_id["m.rev"].adversity_score > 0
    # Revenue up 30% is favorable
    assert by_id["m.rev2"].direction == "up"
    assert by_id["m.rev2"].adverse is False
    assert by_id["m.rev2"].adversity_score == 0.0
