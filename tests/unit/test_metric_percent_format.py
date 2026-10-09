"""Share metrics (CTR, conversion rate) display as percent points once.

Cube stores 0–1; some gold extracts already store 0–100. Formatting must not
×100 the latter (live thread_c8b3c93d campaign tables). ROAS stays a multiple.
"""

from __future__ import annotations

import pytest

from seleric_swarm.services.metrics import (
    format_metric_value,
    is_percent_share_metric,
    percent_points,
)


@pytest.mark.parametrize(
    "metric_id,unit,expected",
    [
        ("ctr", "ratio", True),
        ("paid_media.ctr", "ratio", True),
        ("conversion_rate", "ratio", True),
        ("session_conversion_rate", None, True),
        ("bounce_rate", "pct", True),
        ("hook_rate", "ratio", True),
        ("net_roas", "ratio", False),
        ("gross_roas", "ratio", False),
        ("mer", "ratio", False),
        ("ad_spend", "INR", False),
        ("orders", "count", False),
    ],
)
def test_share_vs_multiple_classification(metric_id: str, unit: str | None, expected: bool) -> None:
    assert is_percent_share_metric(metric_id, unit) is expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        (0.0238, 2.38),
        (0.02105, 2.105),
        (2.38, 2.38),  # already percent points — do not ×100 again
        (1.0, 100.0),
        (0.0, 0.0),
    ],
)
def test_percent_points_never_double_scales(raw: float, expected: float) -> None:
    assert percent_points(raw) == pytest.approx(expected)


def test_format_ctr_and_conversion_as_percent() -> None:
    assert format_metric_value(0.0238, metric_id="ctr", unit="ratio") == "2.38%"
    assert format_metric_value(2.38, metric_id="ctr", unit="ratio") == "2.38%"
    assert format_metric_value(0.0154, metric_id="conversion_rate", unit="ratio") == "1.54%"


def test_format_roas_stays_a_multiple() -> None:
    assert format_metric_value(1.32, metric_id="gross_roas", unit="ratio") == "1.32"
    assert format_metric_value(0.8455, metric_id="net_roas", unit="ratio") == "0.8455"
