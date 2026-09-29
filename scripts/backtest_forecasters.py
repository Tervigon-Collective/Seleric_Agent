"""Rolling-origin backtest of forecast backends (Holt vs statsforecast vs ...).

    uv run python -m scripts.backtest_forecasters --synthetic
    uv run python -m scripts.backtest_forecasters --csv history.csv   # columns: date,metric,value

Per (metric, horizon, backend) it reports sMAPE, MAE, MASE (vs seasonal-naive-7),
95% interval coverage, mean interval width and mean fit latency, then applies the
go/no-go rule below. Backends whose optional dependency is missing are skipped
and listed, never silently scored as zero.

Go/no-go (fixed before running): a candidate is promotable for a metric only if
its MASE beats the Holt baseline by >= 5% at BOTH h=7 and h=14 AND its interval
coverage is within [0.90, 0.98]. Promotion itself is a human edit to
config/model_registry.yaml.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

from seleric_swarm.models.backends import get_backend
from seleric_swarm.models.service import ForecastUnavailable
from seleric_swarm.paths import repo_root

HORIZONS = (1, 7, 14)
MIN_TRAIN = 28
STEP = 7
MASE_GAIN = 0.05
COVERAGE_RANGE = (0.90, 0.98)


def synthetic_history(days: int = 180) -> dict[str, list[float]]:
    """Deterministic weekly-seasonal series with trend and mild noise (no RNG)."""
    out: dict[str, list[float]] = {}
    for name, base, slope, amp in (
        ("metric.net_sales", 1000.0, 2.0, 200.0),
        ("metric.orders", 50.0, 0.1, 12.0),
    ):
        out[name] = [
            base + slope * i + amp * math.sin(2 * math.pi * (i % 7) / 7) + ((i * 37) % 11 - 5)
            for i in range(days)
        ]
    return out


def load_csv(path: Path) -> dict[str, list[float]]:
    rows: dict[str, list[tuple[date, float]]] = defaultdict(list)
    with path.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            rows[r["metric"]].append((date.fromisoformat(r["date"]), float(r["value"])))
    return {m: [v for _, v in sorted(pts)] for m, pts in rows.items()}


def _mase_scale(train: list[float], m: int = 7) -> float:
    diffs = [abs(train[i] - train[i - m]) for i in range(m, len(train))]
    return (sum(diffs) / len(diffs)) if diffs else 0.0


def evaluate(series: dict[str, list[float]], backends: list[str]) -> tuple[list[dict], list[str]]:
    skipped: list[str] = []
    live: list[str] = []
    for b in backends:
        try:
            get_backend(b).forecast(next(iter(series.values()))[:MIN_TRAIN], 1)
            live.append(b)
        except ForecastUnavailable as exc:
            skipped.append(f"{b}: {exc}")

    rows: list[dict] = []
    for metric, values in series.items():
        for h in HORIZONS:
            for b in live:
                errs, ape, hits, widths, lat, scales = [], [], [], [], [], []
                for cut in range(MIN_TRAIN, len(values) - h + 1, STEP):
                    train, actual = values[:cut], values[cut + h - 1]
                    t0 = time.perf_counter()
                    try:
                        f = get_backend(b).forecast(train, h, season_length=7)
                    except ForecastUnavailable:
                        continue
                    lat.append(time.perf_counter() - t0)
                    errs.append(abs(f.point - actual))
                    denom = (abs(actual) + abs(f.point)) / 2
                    ape.append(abs(f.point - actual) / denom if denom else 0.0)
                    hits.append(f.lower <= actual <= f.upper)
                    widths.append(f.upper - f.lower)
                    scales.append(_mase_scale(train))
                if not errs:
                    continue
                scale = sum(scales) / len(scales)
                rows.append(
                    {
                        "metric": metric,
                        "horizon": h,
                        "backend": b,
                        "n": len(errs),
                        "mae": sum(errs) / len(errs),
                        "smape": 100 * sum(ape) / len(ape),
                        "mase": (sum(errs) / len(errs)) / scale if scale else float("nan"),
                        "coverage": sum(hits) / len(hits),
                        "width": sum(widths) / len(widths),
                        "latency_ms": 1000 * sum(lat) / len(lat),
                    }
                )
    return rows, skipped


def verdicts(rows: list[dict], baseline: str = "holt") -> dict[tuple[str, str], str]:
    idx = {(r["metric"], r["horizon"], r["backend"]): r for r in rows}
    out: dict[tuple[str, str], str] = {}
    for metric in {r["metric"] for r in rows}:
        for b in {r["backend"] for r in rows} - {baseline}:
            ok = True
            for h in (7, 14):
                cand, base = idx.get((metric, h, b)), idx.get((metric, h, baseline))
                if not cand or not base or not (cand["mase"] < base["mase"] * (1 - MASE_GAIN)):
                    ok = False
                    break
                if not COVERAGE_RANGE[0] <= cand["coverage"] <= COVERAGE_RANGE[1]:
                    ok = False
                    break
            out[(metric, b)] = "PROMOTE" if ok else "keep baseline"
    return out


def render(rows: list[dict], skipped: list[str], verd: dict[tuple[str, str], str]) -> str:
    lines = [
        f"# Forecast backtest ({datetime.now(UTC).date()})",
        "",
        "| metric | h | backend | n | sMAPE% | MAE | MASE | coverage | width | ms |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda r: (r["metric"], r["horizon"], r["backend"])):
        lines.append(
            f"| {r['metric']} | {r['horizon']} | {r['backend']} | {r['n']} | {r['smape']:.1f} | "
            f"{r['mae']:.2f} | {r['mase']:.3f} | {r['coverage']:.2f} | {r['width']:.1f} | "
            f"{r['latency_ms']:.1f} |"
        )
    lines += ["", "## Verdicts (vs holt)", ""]
    lines += [f"- {m} / {b}: **{v}**" for (m, b), v in sorted(verd.items())] or ["- none"]
    if skipped:
        lines += ["", "## Skipped backends", ""] + [f"- {s}" for s in skipped]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--synthetic", action="store_true")
    src.add_argument("--csv", type=Path)
    ap.add_argument("--backends", default="holt,statsforecast,timesfm,chronos")
    ap.add_argument("--out-dir", type=Path, default=repo_root() / "docs/features/forecasting")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()

    series = synthetic_history() if args.synthetic else load_csv(args.csv)
    rows, skipped = evaluate(series, [b for b in args.backends.split(",") if b])
    verd = verdicts(rows)
    report = render(rows, skipped, verd)
    print(report)
    if not args.no_write:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).date().isoformat()
        tag = "synthetic" if args.synthetic else "real"
        (args.out_dir / f"BACKTEST_{tag}_{stamp}.md").write_text(report, encoding="utf-8")
        (args.out_dir / f"BACKTEST_{tag}_{stamp}.json").write_text(
            json.dumps(rows, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
