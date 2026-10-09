"""A ratio shown beside its own parts must equal them (live 2026-10-09 MS3-dce7d3104e)."""

from __future__ import annotations

from seleric_swarm.agent.validation.coherence import ratio_incoherence

_AGG = {"roas": "ratio", "margin": "additive", "spend": "additive", "orders": "additive"}
_LABEL = {"roas": "Net ROAS", "margin": "Contribution margin", "spend": "Ad spend", "orders": "Orders"}


class _Catalogue:
    def aggregation_for(self, metric_id: str) -> str | None:
        return _AGG.get(metric_id)

    def label_for(self, metric_id: str) -> str | None:
        return _LABEL.get(metric_id)


def _day(day: str, margin: float, spend: float, scale: float = 1.0) -> list[dict]:
    rows = []
    for metric, value in (("margin", margin), ("spend", spend), ("roas", scale * margin / spend)):
        rows.append(
            {"metric_id": metric, "value": value, "dimensions": {},
             "period_start": f"{day}T00:00:00+05:30", "period_end": f"{day}T00:00:00+05:30"}
        )
    return rows


_EVIDENCE = [
    *_day("2026-09-29", 42783.27, 46669.43),
    *_day("2026-09-30", 36944.60, 36055.94),
    *_day("2026-10-01", 52912.31, 45967.59),
    *_day("2026-10-02", 22615.42, 35504.79),
]

_TABLE = (
    "| Period | Net ROAS (x) | Contribution margin (INR) | Ad spend (INR) |\n"
    "| --- | ---: | ---: | ---: |\n"
    "| Event: 2026-10-05..2026-10-08 | 1.04x | 298,099 | 285,462 |\n"
    "| Comparison: 2026-09-28..2026-10-02 | {roas} | 155,256 | 164,198 |\n"
)


def test_a_row_whose_ratio_its_parts_do_not_give_is_flagged() -> None:
    reason = ratio_incoherence(_TABLE.format(roas="0.894x"), _EVIDENCE, _Catalogue())
    assert reason is not None
    assert "Comparison: 2026-09-28..2026-10-02" in reason
    assert "0.9455" in reason


def test_a_coherent_row_passes_at_its_shown_precision() -> None:
    assert ratio_incoherence(_TABLE.format(roas="0.95x"), _EVIDENCE, _Catalogue()) is None
    assert ratio_incoherence(_TABLE.format(roas="0.9455"), _EVIDENCE, _Catalogue()) is None


def test_no_relationship_is_assumed_from_too_few_slices() -> None:
    # Two slices fit any scale k: nothing is checked.
    assert ratio_incoherence(_TABLE.format(roas="0.894x"), _EVIDENCE[:6], _Catalogue()) is None


def test_the_scale_is_learned_from_the_evidence() -> None:
    evidence = [
        *_day("2026-09-29", 42783.27, 46669.43, scale=100.0),
        *_day("2026-09-30", 36944.60, 36055.94, scale=100.0),
        *_day("2026-10-01", 52912.31, 45967.59, scale=100.0),
    ]
    table = _TABLE.format(roas="94.55").replace("1.04x", "104.4")
    assert ratio_incoherence(table, evidence, _Catalogue()) is None
    assert ratio_incoherence(_TABLE.format(roas="89.4").replace("1.04x", "104.4"), evidence, _Catalogue()) is not None


def test_columns_that_are_not_the_learned_metrics_are_left_alone() -> None:
    table = (
        "| Period | Orders | Contribution margin (INR) | Ad spend (INR) |\n"
        "| --- | ---: | ---: | ---: |\n"
        "| 2026-09-28..2026-10-02 | 12 | 155,256 | 164,198 |\n"
    )
    assert ratio_incoherence(table, _EVIDENCE, _Catalogue()) is None
