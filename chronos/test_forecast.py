"""Functional smoke tests for Chronos-2 CPU deployment.

These tests use synthetic sales series only — not real business forecasts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import httpx

from forecast import get_load_meta, get_pipeline, make_synthetic_series, predict_series

BASE_URL = os.getenv("CHRONOS_BASE_URL", "http://127.0.0.1:8112")


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _validate_forecast_payload(payload: dict[str, Any], expected_series: int, horizon: int) -> None:
    _assert("forecasts" in payload, "missing forecasts")
    _assert(len(payload["forecasts"]) == expected_series, f"expected {expected_series} series")
    for item in payload["forecasts"]:
        points = item["forecast"]
        _assert(len(points) == horizon, f"{item['series_id']}: expected {horizon} points, got {len(points)}")
        prev_ts = None
        for point in points:
            for key in ("timestamp", "mean", "p10", "p50", "p90"):
                _assert(key in point, f"missing {key}")
            _assert(point["p10"] <= point["p50"] <= point["p90"], "quantile ordering failed")
            ts = point["timestamp"]
            if prev_ts is not None:
                _assert(ts > prev_ts, "timestamps must be increasing")
            prev_ts = ts


def test1_basic_forecast() -> dict[str, Any]:
    series = [make_synthetic_series(180, series_id="revenue", seed=1)]
    result = predict_series(series, prediction_length=7, batch_size=1)
    _validate_forecast_payload(result, expected_series=1, horizon=7)
    return {"name": "test1_basic_7d", "ok": True, "inference_seconds": result["inference_seconds"]}


def test2_longer_forecast() -> dict[str, Any]:
    series = [make_synthetic_series(365, series_id="revenue_365", seed=2)]
    result = predict_series(series, prediction_length=30, batch_size=1)
    _validate_forecast_payload(result, expected_series=1, horizon=30)
    return {"name": "test2_30d", "ok": True, "inference_seconds": result["inference_seconds"]}


def test3_multi_series_batched() -> dict[str, Any]:
    series = [
        make_synthetic_series(180, series_id=f"sku_{i:02d}", seed=100 + i, base=500 + 50 * i)
        for i in range(10)
    ]
    result = predict_series(series, prediction_length=7, batch_size=2)
    _validate_forecast_payload(result, expected_series=10, horizon=7)
    return {
        "name": "test3_multi_sku",
        "ok": True,
        "inference_seconds": result["inference_seconds"],
        "series_count": result["series_count"],
    }


def test4_api(base_url: str) -> dict[str, Any]:
    series = make_synthetic_series(180, series_id="api_revenue", seed=7)
    ready = httpx.get(f"{base_url}/ready", timeout=60.0)
    ready.raise_for_status()
    ready_json = ready.json()
    _assert(ready_json.get("loaded") is True, "/ready not loaded")
    load_count_before = ready_json.get("load_count", 1)

    body = {
        "series": [
            {
                "series_id": series.series_id,
                "values": series.values,
                "start": series.start,
                "freq": series.freq,
            }
        ],
        "prediction_length": 7,
        "quantile_levels": [0.1, 0.5, 0.9],
        "batch_size": 1,
    }
    r1 = httpx.post(f"{base_url}/predict", json=body, timeout=600.0)
    r1.raise_for_status()
    p1 = r1.json()
    _validate_forecast_payload(p1, expected_series=1, horizon=7)

    r2 = httpx.post(f"{base_url}/predict", json=body, timeout=600.0)
    r2.raise_for_status()
    p2 = r2.json()
    _assert(p1["model_load_count"] == p2["model_load_count"] == load_count_before, "model was reloaded")
    _assert(p2["predict_count"] >= p1["predict_count"], "predict_count did not advance")
    return {
        "name": "test4_api",
        "ok": True,
        "model_load_count": p2["model_load_count"],
        "predict_count": p2["predict_count"],
        "inference_seconds": [p1["inference_seconds"], p2["inference_seconds"]],
    }


def test5_stability(base_url: str, n: int = 10) -> dict[str, Any]:
    series = make_synthetic_series(180, series_id="stability", seed=9)
    body = {
        "series": [
            {
                "series_id": series.series_id,
                "values": series.values,
                "start": series.start,
                "freq": series.freq,
            }
        ],
        "prediction_length": 7,
        "batch_size": 1,
    }
    latencies: list[float] = []
    failures = 0
    for i in range(n):
        try:
            t0 = time.perf_counter()
            resp = httpx.post(f"{base_url}/predict", json=body, timeout=600.0)
            elapsed = time.perf_counter() - t0
            resp.raise_for_status()
            payload = resp.json()
            _validate_forecast_payload(payload, expected_series=1, horizon=7)
            latencies.append(elapsed)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"stability request {i + 1} failed: {exc}", file=sys.stderr)

    _assert(failures == 0, f"{failures}/{n} stability requests failed")
    return {
        "name": "test5_stability",
        "ok": True,
        "requests": n,
        "failures": failures,
        "avg_latency_seconds": round(sum(latencies) / len(latencies), 4),
        "max_latency_seconds": round(max(latencies), 4),
        "min_latency_seconds": round(min(latencies), 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Chronos-2 functional smoke tests")
    parser.add_argument("--mode", choices=["local", "api", "all"], default="all")
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args()

    results: list[dict[str, Any]] = []
    print("NOTE: All series are synthetic. Results are not real business forecasts.")

    if args.mode in ("local", "all"):
        print("Loading model for local tests...")
        get_pipeline()
        print("load_meta:", json.dumps(get_load_meta()))
        results.append(test1_basic_forecast())
        print("PASS", results[-1])
        results.append(test2_longer_forecast())
        print("PASS", results[-1])
        results.append(test3_multi_series_batched())
        print("PASS", results[-1])

    if args.mode in ("api", "all"):
        results.append(test4_api(args.base_url))
        print("PASS", results[-1])
        results.append(test5_stability(args.base_url))
        print("PASS", results[-1])

    summary = {"passed": len(results), "results": results}
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
