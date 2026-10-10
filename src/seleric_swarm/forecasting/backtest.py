"""Offline rolling-origin backtest and bundle promotion (P1).

Run against an isolated Chronos container so the live service is not starved::

    python -m seleric_swarm.forecasting.backtest --target net_sales \\
        --chronos-url http://127.0.0.1:8113

Writes ``reports/forecasting/<date>/<target>.json`` + ``.md`` and prints a
proposed YAML bundle for human PR approval.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from seleric_swarm.models.evaluation import bias, coverage, horizon_total_ape, mase, wql
from seleric_swarm.paths import repo_root


def rolling_cutoffs(
    *,
    end: date,
    lookback_days: int = 180,
    step_days: int = 7,
) -> list[date]:
    start = end - timedelta(days=lookback_days)
    out: list[date] = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=step_days)
    return out


def score_fold(
    actuals: list[float],
    p10: list[float],
    p50: list[float],
    p90: list[float],
    *,
    insample: list[float] | None = None,
) -> dict[str, float]:
    out: dict[str, float] = {
        "wql": wql(actuals, {0.1: p10, 0.5: p50, 0.9: p90}),
        "mase": mase(actuals, p50, insample=insample),
        "coverage_80": coverage(actuals, p10, p90),
        "bias": bias(actuals, p50),
    }
    ape = horizon_total_ape(sum(actuals), sum(p50))
    if ape is not None:
        out["horizon_total_ape"] = ape
    return out


def propose_bundle_yaml(
    *,
    target: str,
    engine: str,
    co_targets: list[str],
    past_covariates: list[str],
    known_future: list[str],
    metrics: dict[str, float],
    report_path: str,
) -> str:
    return (
        f"  - {{id: {target}.v1, status: candidate, engine: {engine},\n"
        f"     co_targets: {co_targets}, past_covariates: {past_covariates},\n"
        f"     known_future: {known_future},\n"
        f"     backtest: {{report: {report_path}, wql: {metrics.get('wql')}, "
        f"mase: {metrics.get('mase')}, coverage_80: {metrics.get('coverage_80')}}}}}\n"
    )


def write_report(
    *,
    target: str,
    folds: list[dict[str, Any]],
    selection: list[str],
    rejected: list[dict[str, str]],
    out_dir: Path | None = None,
) -> Path:
    day = date.today().isoformat()
    root = out_dir or (repo_root() / "reports" / "forecasting" / day)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{target}.json"
    summary = {
        "target": target,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "folds": folds,
        "selection_path": selection,
        "rejected": rejected,
        "point_in_time_caveat": (
            "If the warehouse lacks revision snapshots, backtests use today's "
            "values for past days; maturity masking reduces that bias."
        ),
    }
    if folds:
        keys = [k for k in folds[0] if isinstance(folds[0].get(k), (int, float))]
        summary["mean_metrics"] = {
            k: round(sum(f[k] for f in folds if k in f) / len(folds), 6) for k in keys
        }
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    md = root / f"{target}.md"
    lines = [
        f"# Forecast backtest — {target}",
        "",
        f"Folds: {len(folds)}",
        f"Selection: {', '.join(selection) or '(baseline)'}",
        "",
        "## Mean metrics",
        "",
    ]
    for k, v in (summary.get("mean_metrics") or {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append(summary["point_in_time_caveat"])
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def seasonal_naive_bands(
    history: list[float], horizon: int, *, season: int = 7, z: float = 1.2816
) -> tuple[list[float], list[float], list[float]]:
    """Point = last season repeated; 80% band from seasonal residual spread."""
    import statistics

    if not history:
        return [0.0] * horizon, [0.0] * horizon, [0.0] * horizon
    m = season if len(history) >= season else 1
    point = [history[-m + (i % m)] for i in range(horizon)]
    if len(history) > m + 1:
        resid = [history[i] - history[i - m] for i in range(m, len(history))]
        sigma = statistics.pstdev(resid) if len(resid) > 1 else 0.0
    else:
        sigma = 0.0
    half = z * sigma
    return [p - half for p in point], point, [p + half for p in point]


async def fetch_daily_series(
    mcp: Any, metric_id: str, *, start: date, end: date
) -> dict[date, float]:
    from seleric_swarm.services.mcp_query import row_date
    from seleric_swarm.toolsets.semantic import raw_query_metric

    result = await raw_query_metric(
        mcp,
        agent_id="v3_agent",
        metric_id=metric_id,
        start=start.isoformat(),
        end=end.isoformat(),
        grain="day",
    )
    if result.get("error"):
        raise RuntimeError(f"metrics_query {metric_id}: {result['error']}")
    series: dict[date, float] = {}
    for row in result.get("rows") or []:
        if not isinstance(row, dict):
            continue
        ts = row_date(row)
        raw = row.get(metric_id)
        if ts is None or raw is None:
            continue
        try:
            series[date.fromisoformat(ts[:10])] = float(raw)
        except (TypeError, ValueError):
            continue
    return series


async def _chronos_univariate(
    url: str,
    *,
    history: list[float],
    start: date,
    horizon: int,
    model: str,
) -> tuple[list[float], list[float], list[float]] | None:
    import httpx

    body = {
        "tasks": [
            {
                "task_id": "fold",
                "start": start.isoformat(),
                "freq": "D",
                "targets": {"y": history},
                "past_covariates": {},
                "future_covariates": {},
            }
        ],
        "prediction_length": horizon,
        "quantile_levels": [0.1, 0.5, 0.9],
        "model": model,
    }
    predict_body = {
        "series": [
            {
                "series_id": "y",
                "values": history,
                "start": start.isoformat(),
                "freq": "D",
            }
        ],
        "prediction_length": horizon,
        "quantile_levels": [0.1, 0.5, 0.9],
    }
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            # The running image crashes when loading a second model
            # (set_num_interop_threads). Prefer the already-warm base model
            # via /predict; try /v2 only when the requested model is not the base.
            if model == "chronos-2":
                resp = await client.post(f"{url.rstrip('/')}/predict", json=predict_body)
            else:
                resp = await client.post(f"{url.rstrip('/')}/v2/forecast", json=body)
                if resp.status_code >= 500:
                    resp = await client.post(f"{url.rstrip('/')}/predict", json=predict_body)
            if resp.status_code == 404:
                resp = await client.post(f"{url.rstrip('/')}/predict", json=predict_body)
            if resp.status_code >= 400:
                print(
                    f"chronos fold failed: HTTP {resp.status_code} {resp.text[:400]}",
                    file=sys.stderr,
                )
                return None
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"chronos fold failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None
    steps = []
    tasks = payload.get("tasks") or []
    if tasks:
        steps = ((tasks[0].get("targets") or {}).get("y") or {}).get("forecast") or []
    if not steps:
        forecasts = (payload.get("forecasts") or [{}])[0]
        steps = forecasts.get("forecast") or []
    if len(steps) < horizon:
        return None

    def _q(step: dict[str, Any], q: str, fallback: str) -> float:
        quantiles = step.get("quantiles") or step
        if isinstance(quantiles, dict) and q in quantiles:
            return float(quantiles[q])
        return float(step.get(fallback, step.get("value", 0.0)))

    p10, p50, p90 = [], [], []
    for step in steps[:horizon]:
        p50.append(_q(step, "0.5", "p50"))
        p10.append(_q(step, "0.1", "p10"))
        p90.append(_q(step, "0.9", "p90"))
    return p10, p50, p90


async def evaluate_target(
    *,
    target: str,
    as_of: date,
    chronos_url: str,
    lookback_days: int,
    step_days: int,
    horizon_days: int,
    context_days: int,
    model: str,
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, str]]]:
    """Rolling-origin scores for seasonal-naive, ETS, and Chronos univariate."""
    from dotenv import load_dotenv

    from seleric_swarm.models.service import Z_80, forecast_path
    from seleric_swarm.protocols.mcp.gateway import MCPGateway

    load_dotenv(repo_root() / ".env")
    mcp = MCPGateway("config/mcp_servers.yaml")
    fetch_start = as_of - timedelta(days=lookback_days + context_days + horizon_days + 7)
    series = await fetch_daily_series(mcp, target, start=fetch_start, end=as_of - timedelta(days=1))
    if len(series) < 60:
        raise RuntimeError(f"{target}: only {len(series)} daily points fetched")

    last_day = max(series)
    cutoffs = rolling_cutoffs(
        end=min(as_of - timedelta(days=1), last_day) - timedelta(days=horizon_days),
        lookback_days=lookback_days,
        step_days=step_days,
    )
    folds: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for cutoff in cutoffs:
        hist_dates = sorted(d for d in series if d <= cutoff)
        if len(hist_dates) < 28:
            rejected.append({"cutoff": cutoff.isoformat(), "reason": "short_history"})
            continue
        hist_dates = hist_dates[-context_days:]
        history = [series[d] for d in hist_dates]
        future_dates = [cutoff + timedelta(days=i) for i in range(1, horizon_days + 1)]
        if any(d not in series for d in future_dates):
            rejected.append({"cutoff": cutoff.isoformat(), "reason": "immature_or_missing_actuals"})
            continue
        actuals = [series[d] for d in future_dates]
        fold: dict[str, Any] = {"cutoff": cutoff.isoformat(), "horizon": horizon_days}

        p10, p50, p90 = seasonal_naive_bands(history, horizon_days)
        fold["seasonal_naive"] = score_fold(actuals, p10, p50, p90, insample=history)

        try:
            path = forecast_path(
                history,
                horizon_days=horizon_days,
                model_id="forecast.ets.path",
                nonnegative=True,
                interval_z=Z_80,
            )
            fold["ets"] = score_fold(actuals, path.lows, path.points, path.highs, insample=history)
        except Exception as exc:  # noqa: BLE001
            rejected.append({"cutoff": cutoff.isoformat(), "engine": "ets", "reason": type(exc).__name__})

        if chronos_url:
            bands = await _chronos_univariate(
                chronos_url,
                history=history,
                start=hist_dates[0],
                horizon=horizon_days,
                model=model,
            )
            if bands is None:
                rejected.append({"cutoff": cutoff.isoformat(), "engine": model, "reason": "chronos_failed"})
            else:
                fold[model] = score_fold(actuals, bands[0], bands[1], bands[2], insample=history)
        folds.append(fold)
        print(
            json.dumps({"cutoff": cutoff.isoformat(), "engines": [k for k in fold if k not in {"cutoff", "horizon"}]}),
            flush=True,
        )
    selection = ["seasonal-naive", "ets"]
    if chronos_url:
        selection.append(f"{model}-univariate")
    return folds, selection, rejected


def _mean_by_engine(folds: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    engines: dict[str, list[dict[str, float]]] = {}
    for fold in folds:
        for key, val in fold.items():
            if isinstance(val, dict) and "wql" in val:
                engines.setdefault(key, []).append(val)
    out: dict[str, dict[str, float]] = {}
    for name, rows in engines.items():
        keys = rows[0].keys()
        out[name] = {
            k: round(sum(r[k] for r in rows if k in r) / len(rows), 6) for k in keys
        }
        out[name]["n_folds"] = float(len(rows))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seleric_swarm.forecasting.backtest")
    parser.add_argument("--target", required=True)
    parser.add_argument("--chronos-url", default="http://127.0.0.1:8112")
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--lookback-days", type=int, default=90)
    parser.add_argument("--step-days", type=int, default=14)
    parser.add_argument("--horizon-days", type=int, default=14)
    parser.add_argument("--context-days", type=int, default=365)
    parser.add_argument("--model", default="chronos-2")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    cutoffs = rolling_cutoffs(
        end=as_of - timedelta(days=1 + args.horizon_days),
        lookback_days=args.lookback_days,
        step_days=args.step_days,
    )
    print(
        json.dumps(
            {
                "target": args.target,
                "as_of": as_of.isoformat(),
                "chronos_url": args.chronos_url or "(unset)",
                "model": args.model,
                "n_cutoffs": len(cutoffs),
                "horizon_days": args.horizon_days,
                "dry_run": args.dry_run,
            },
            indent=2,
        ),
        flush=True,
    )
    if args.dry_run:
        return 0

    import asyncio

    folds, selection, rejected = asyncio.run(
        evaluate_target(
            target=args.target,
            as_of=as_of,
            chronos_url=args.chronos_url,
            lookback_days=args.lookback_days,
            step_days=args.step_days,
            horizon_days=args.horizon_days,
            context_days=args.context_days,
            model=args.model,
        )
    )
    means = _mean_by_engine(folds)
    # Winner: lowest mean WQL among engines that scored.
    winner = min(means, key=lambda n: means[n]["wql"]) if means else args.model
    path = write_report(
        target=args.target,
        folds=[{"cutoff": f["cutoff"], **{k: v for k, v in f.items() if isinstance(v, dict)}} for f in folds],
        selection=[*selection, f"winner:{winner}"],
        rejected=rejected,
    )
    # Flatten mean metrics into the JSON the writer already stored, then rewrite with engine means.
    blob = json.loads(path.read_text(encoding="utf-8"))
    blob["mean_by_engine"] = means
    blob["winner"] = winner
    blob["context_days"] = args.context_days
    blob["horizon_days"] = args.horizon_days
    path.write_text(json.dumps(blob, indent=2), encoding="utf-8")
    md = path.with_suffix(".md")
    extra = ["", "## Mean by engine", ""]
    for name, metrics in sorted(means.items(), key=lambda kv: kv[1]["wql"]):
        extra.append(
            f"- {name}: wql={metrics.get('wql')} mase={metrics.get('mase')} "
            f"coverage_80={metrics.get('coverage_80')} n={int(metrics.get('n_folds', 0))}"
        )
    extra.append(f"\nWinner (lowest WQL): **{winner}**\n")
    extra.append(
        "\nScored path is univariate (no calendar or metric covariates). "
        "Calendar known-future features were not part of this bake-off.\n"
    )
    md.write_text(md.read_text(encoding="utf-8") + "\n".join(extra), encoding="utf-8")
    print(
        propose_bundle_yaml(
            target=args.target,
            engine=winner if winner.startswith("chronos") else "chronos-2",
            co_targets=[],
            past_covariates=[],
            known_future=[],
            metrics=means.get(winner, {}),
            report_path=str(path.relative_to(repo_root())),
        )
    )
    print(json.dumps({"report": str(path), "winner": winner, "mean_by_engine": means}, indent=2))
    return 0 if folds else 1


if __name__ == "__main__":
    sys.exit(main())
