"""Resource and inference benchmarks for Chronos-2 CPU deployment."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import psutil

from forecast import get_load_meta, get_pipeline, make_synthetic_series, predict_series

BASE_URL = os.getenv("CHRONOS_BASE_URL", "http://127.0.0.1:8112")
CONTAINER = os.getenv("CHRONOS_CONTAINER", "seleric_agent-chronos-1")


def _docker_stats(container: str) -> dict[str, Any]:
    """Best-effort docker stats. Prefer running this script on the host."""
    try:
        out = subprocess.check_output(
            [
                "docker",
                "stats",
                container,
                "--no-stream",
                "--format",
                "{{json .}}",
            ],
            text=True,
        ).strip()
        return json.loads(out)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "docker CLI not available in this environment; run benchmark.py on the host"
        ) from exc


def _parse_mem(mem_str: str) -> float:
    """Parse docker stats MemUsage like '1.23GiB / 4GiB' -> used GiB."""
    used = mem_str.split("/")[0].strip()
    num = float("".join(ch for ch in used if ch.isdigit() or ch == "."))
    unit = "".join(ch for ch in used if ch.isalpha()).lower()
    scale = {"b": 1 / (1024**3), "kib": 1 / (1024**2), "mib": 1 / 1024, "gib": 1.0, "tib": 1024.0}
    return num * scale.get(unit, 1.0)


def _dir_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def wait_ready(base_url: str, timeout_s: float = 600.0) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last_err = None
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{base_url}/ready", timeout=10.0)
            if resp.status_code == 200:
                return resp.json()
            last_err = resp.text
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
        time.sleep(2)
    raise TimeoutError(f"/ready not ready within {timeout_s}s: {last_err}")


def bench_local() -> dict[str, Any]:
    proc = psutil.Process(os.getpid())
    rss_before = proc.memory_info().rss / (1024**3)
    t0 = time.perf_counter()
    get_pipeline()
    load_s = time.perf_counter() - t0
    rss_after_load = proc.memory_info().rss / (1024**3)
    meta = get_load_meta()

    s180 = [make_synthetic_series(180, seed=11)]
    s365 = [make_synthetic_series(365, seed=12)]
    multi = [make_synthetic_series(180, series_id=f"sku_{i}", seed=200 + i) for i in range(10)]

    # Cold-ish (first timed call after load/warmup)
    t1 = time.perf_counter()
    r7 = predict_series(s180, prediction_length=7, batch_size=1)
    lat_7 = time.perf_counter() - t1

    t2 = time.perf_counter()
    r7b = predict_series(s180, prediction_length=7, batch_size=1)
    lat_7_warm = time.perf_counter() - t2

    t3 = time.perf_counter()
    r30 = predict_series(s365, prediction_length=30, batch_size=1)
    lat_30 = time.perf_counter() - t3

    t4 = time.perf_counter()
    rbatch = predict_series(multi, prediction_length=7, batch_size=2)
    lat_batch = time.perf_counter() - t4

    rss_peak = proc.memory_info().rss / (1024**3)
    cache_bytes = _dir_size_bytes(Path(os.getenv("HF_HOME", "/models")))

    return {
        "mode": "local",
        "model_load_seconds": round(load_s, 3),
        "reported_load_seconds": meta.get("load_seconds"),
        "rss_before_gib": round(rss_before, 3),
        "rss_after_load_gib": round(rss_after_load, 3),
        "rss_peak_gib": round(rss_peak, 3),
        "hf_cache_gib": round(cache_bytes / (1024**3), 3),
        "latency_7d_first_s": round(lat_7, 3),
        "latency_7d_warm_s": round(lat_7_warm, 3),
        "latency_30d_s": round(lat_30, 3),
        "latency_batch_10x7_s": round(lat_batch, 3),
        "throughput_series_per_s_batch": round(10 / lat_batch, 3) if lat_batch else None,
        "predict_counts": {
            "7d": r7["predict_count"],
            "7d_warm": r7b["predict_count"],
            "30d": r30["predict_count"],
            "batch": rbatch["predict_count"],
        },
        "model_load_count": meta.get("load_count"),
        "cpu_threads": os.getenv("TORCH_NUM_THREADS", "2"),
    }


def bench_api(base_url: str, container: str) -> dict[str, Any]:
    ready = wait_ready(base_url)
    series = make_synthetic_series(180, seed=21)
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
    body30 = {
        "series": [
            {
                "series_id": "rev365",
                "values": make_synthetic_series(365, seed=22).values,
                "start": "2024-01-01",
                "freq": "D",
            }
        ],
        "prediction_length": 30,
        "batch_size": 1,
    }
    body_batch = {
        "series": [
            {
                "series_id": f"sku_{i}",
                "values": make_synthetic_series(180, seed=300 + i).values,
                "start": "2024-01-01",
                "freq": "D",
            }
            for i in range(10)
        ],
        "prediction_length": 7,
        "batch_size": 2,
    }

    stats_before = _docker_stats(container)

    t0 = time.perf_counter()
    httpx.post(f"{base_url}/predict", json=body, timeout=600.0).raise_for_status()
    lat_7 = time.perf_counter() - t0

    t1 = time.perf_counter()
    httpx.post(f"{base_url}/predict", json=body, timeout=600.0).raise_for_status()
    lat_7_warm = time.perf_counter() - t1

    t2 = time.perf_counter()
    httpx.post(f"{base_url}/predict", json=body30, timeout=900.0).raise_for_status()
    lat_30 = time.perf_counter() - t2

    t3 = time.perf_counter()
    httpx.post(f"{base_url}/predict", json=body_batch, timeout=900.0).raise_for_status()
    lat_batch = time.perf_counter() - t3

    # Sequential stability sample with docker stats peaks
    peak_mem = _parse_mem(stats_before["MemUsage"])
    peak_cpu = float(stats_before["CPUPerc"].rstrip("%"))
    seq_lat: list[float] = []
    for _ in range(10):
        t = time.perf_counter()
        httpx.post(f"{base_url}/predict", json=body, timeout=600.0).raise_for_status()
        seq_lat.append(time.perf_counter() - t)
        st = _docker_stats(container)
        peak_mem = max(peak_mem, _parse_mem(st["MemUsage"]))
        peak_cpu = max(peak_cpu, float(st["CPUPerc"].rstrip("%")))

    image_size = subprocess.check_output(
        ["docker", "image", "inspect", "seleric_agent-chronos:latest", "--format", "{{.Size}}"],
        text=True,
    ).strip()

    return {
        "mode": "api",
        "ready": ready,
        "docker_mem_before": stats_before["MemUsage"],
        "docker_cpu_before": stats_before["CPUPerc"],
        "peak_mem_gib": round(peak_mem, 3),
        "peak_cpu_percent": round(peak_cpu, 2),
        "latency_7d_first_s": round(lat_7, 3),
        "latency_7d_warm_s": round(lat_7_warm, 3),
        "latency_30d_s": round(lat_30, 3),
        "latency_batch_10x7_s": round(lat_batch, 3),
        "stability_10_avg_s": round(sum(seq_lat) / len(seq_lat), 3),
        "stability_10_max_s": round(max(seq_lat), 3),
        "image_size_gib": round(int(image_size) / (1024**3), 3),
        "cpu_limit_target": "2 vCPU",
        "ram_limit_target": "4 GiB",
        "within_4gib": peak_mem <= 4.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["local", "api", "all"], default="api")
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--container", default=CONTAINER)
    args = parser.parse_args()

    report: dict[str, Any] = {"note": "Synthetic benchmarks only — not production forecasts."}
    if args.mode in ("local", "all"):
        report["local"] = bench_local()
    if args.mode in ("api", "all"):
        report["api"] = bench_api(args.base_url, args.container)

    # Recommend config
    peak = None
    if "api" in report:
        peak = report["api"]["peak_mem_gib"]
    elif "local" in report:
        peak = report["local"]["rss_peak_gib"]

    if peak is not None and peak > 4.0:
        report["recommendation"] = {
            "status": "exceeds_4gib",
            "observed_peak_gib": peak,
            "suggested": "4 vCPU / 6 GiB",
        }
    else:
        report["recommendation"] = {
            "status": "within_or_near_4gib",
            "observed_peak_gib": peak,
            "suggested": "2 vCPU / 4 GiB" if peak and peak <= 3.5 else "2 vCPU / 4–6 GiB",
        }

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
