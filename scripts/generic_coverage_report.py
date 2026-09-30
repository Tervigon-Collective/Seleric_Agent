r"""Which metrics can the generic (``auto``) forecaster serve, and how well?

    .venv\Scripts\python -m scripts.generic_coverage_report --csv history_all.csv [--horizon 7]

Per metric: eligibility (guards in models.service.forecast_series), then a
holdout on the last ``--holdout`` days -- weekly origins, training only on data
before each origin -- comparing ``auto`` with plain ``holt`` and
``seasonal_naive`` on MAE (scaled by the seasonal-naive-7 in-sample MAE, i.e.
MASE) and on the coverage of auto's 95% interval.
"""

from __future__ import annotations

import argparse
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.backtest_forecasters import _mase_scale, load_csv
from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.models.service import ForecastUnavailable, forecast_series


def _evidence(metric: str, values: list[float]) -> list[EvidenceArtifact]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    out = []
    for i, v in enumerate(values):
        t = start + timedelta(days=i)
        out.append(
            EvidenceArtifact(
                metric_id=metric, grain="day", as_of=start + timedelta(days=len(values) + 1), period_start=t, period_end=t,
                value=v, source_query={"measure": metric},
            )
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("--horizon", type=int, default=7)
    ap.add_argument("--holdout", type=int, default=28)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()

    series = load_csv(a.csv)
    h = a.horizon
    lines = [
        f"# Generic forecaster coverage (h={h}, holdout {a.holdout}d)",
        "",
        "| metric | n | status | picked | auto MASE | holt MASE | naive MASE | auto cover |",
        "|---|---|---|---|---|---|---|---|",
    ]
    ok = refused = 0
    for metric, values in sorted(series.items()):
        try:
            forecast_series(_evidence(metric, values), horizon_days=h, model_id="g", backend="auto")
        except ForecastUnavailable as exc:
            refused += 1
            lines.append(f"| {metric} | {len(values)} | refused: {str(exc)[:60]} | | | | | |")
            continue
        ok += 1
        errs: dict[str, list[float]] = {"auto": [], "holt": [], "seasonal_naive": []}
        hits: list[bool] = []
        picks: list[str] = []
        for cut in range(len(values) - a.holdout, len(values) - h + 1, 7):
            train, actual = values[:cut], values[cut + h - 1]
            for b, bucket in errs.items():
                try:
                    r = forecast_series(
                        _evidence(metric, train), horizon_days=h, model_id="g", backend=b
                    )
                except ForecastUnavailable:
                    break
                bucket.append(abs(r.value - actual))
                if b == "auto":
                    hits.append(r.interval[0] <= actual <= r.interval[1])
                    picks.append(str(r.diagnostics.get("selected")))
        scale = _mase_scale(values[: len(values) - a.holdout]) or float("nan")

        def mase(b: str, errs: dict[str, list[float]] = errs, scale: float = scale) -> str:
            return f"{statistics.fmean(errs[b]) / scale:.2f}" if errs[b] else "-"

        cover = f"{sum(hits) / len(hits):.2f}" if hits else "-"
        picked = max(set(picks), key=picks.count) if picks else "-"
        lines.append(
            f"| {metric} | {len(values)} | ok | {picked} | {mase('auto')} | {mase('holt')} | "
            f"{mase('seasonal_naive')} | {cover} |"
        )
    lines += ["", f"Eligible: {ok}, refused: {refused}"]
    text = "\n".join(lines) + "\n"
    print(text)
    if a.out:
        a.out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
