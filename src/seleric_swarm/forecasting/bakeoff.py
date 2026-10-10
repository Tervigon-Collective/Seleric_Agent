"""Feature-set bake-off for the multivariate Chronos-2 forecast.

Chronos-2 accepts past-only covariates and known-future covariates, but extra
covariates do not always help (and can hurt). So the feature set is *validated*:
on rolling origins inside the target's own history, the same Chronos model is run
with and without each candidate, using exactly the data that would have been
available at that origin (same cutoff lag, same masked outages, covariates cut at
the same point). Features are kept only if they lower the held-out error.

Search: (1) calendar known-future vs none; (2) add each candidate past covariate
on top of the winner; (3) greedily try a second covariate. A feature must lower
the score by ``MIN_GAIN`` (parsimony) to be kept.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx

from seleric_swarm.forecasting.types import FeatureFrame
from seleric_swarm.models.service import ForecastUnavailable

_log = logging.getLogger("seleric.forecasting.bakeoff")

HORIZON_DEFAULT = 14
N_ORIGINS = 12
ORIGIN_STEP = 14
CTX_LEN = 730
MIN_GAIN = 0.03
MAX_CANDIDATES = 5
SECOND_ROUND = 3
_TASKS_PER_REQUEST = 8
_CONCURRENCY = 2
_CACHE_TTL_S = 6 * 3600
_CACHE: dict[tuple, tuple[float, "BakeoffResult"]] = {}


@dataclass
class FeatureChoice:
    past: list[str] = field(default_factory=list)
    known: list[str] = field(default_factory=list)


@dataclass
class BakeoffResult:
    choice: FeatureChoice
    scores: dict[str, float]
    baseline_name: str
    baseline_score: float
    chosen_name: str
    chosen_score: float
    n_origins: int
    horizon: int
    seconds: float = 0.0
    note: str = ""

    def summary_line(self, label_of: Any = lambda m: m) -> str:
        ranked = sorted(self.scores.items(), key=lambda kv: kv[1])
        shown = "; ".join(f"{n} {s:.0%}" for n, s in ranked[:6])
        feats = [label_of(c) for c in self.choice.past]
        used = (
            f"{', '.join(feats)} as past-only covariate(s)" if feats else "no extra covariates"
        ) + (" plus calendar known-future features" if self.choice.known else "")
        gain = 1.0 - self.chosen_score / self.baseline_score if self.baseline_score else 0.0
        tail = (
            f", {gain:.0%} better than the history-only model"
            if self.chosen_name != self.baseline_name and gain > 0
            else ""
        )
        return (
            f"- feature validation (Chronos-2, {self.n_origins} held-out {self.horizon}-day origins, "
            f"score = average of total error and daily error; lower is better): {shown}. "
            f"Selected: {used} ({self.chosen_score:.0%}{tail})."
        )


def _score(forecast: list[float], actual: list[float | None]) -> float | None:
    pairs = [(f, a) for f, a in zip(forecast, actual, strict=False) if a is not None]
    if len(pairs) < max(3, len(actual) - 3):
        return None
    ta = sum(a for _, a in pairs)
    if ta <= 0:
        return None
    ape_total = abs(sum(f for f, _ in pairs) - ta) / ta
    wape = sum(abs(f - a) for f, a in pairs) / ta
    return (ape_total + wape) / 2.0


def _origins(frame: FeatureFrame, target_id: str, horizon: int, gap: int) -> list[int]:
    n = len(frame.targets.get(target_id) or [])
    cutoff_idx = n - 1 - gap
    out: list[int] = []
    for k in range(N_ORIGINS):
        o = cutoff_idx - horizon + 1 - ORIGIN_STEP * k
        observed_before = sum(1 for v in (frame.targets[target_id][: max(0, o - gap)]) if v is not None)
        if observed_before >= 120:
            out.append(o)
    return out


def _task(
    frame: FeatureFrame, target_id: str, cfg_name: str, cfg: FeatureChoice, o: int, horizon: int, gap: int
) -> dict[str, Any]:
    s0 = max(0, o - CTX_LEN)

    def window(values: list[float | None]) -> list[float | None]:
        w = list(values[s0:o])
        for i in range(max(0, len(w) - gap), len(w)):
            w[i] = None  # same immature tail the live forecast has
        return w

    dates = [d for d in frame.index]
    past_cov = {
        c: window(frame.past_covariates[c]) for c in cfg.past if c in frame.past_covariates
    }
    future_cov: dict[str, dict[str, list[float | None]]] = {}
    for name in cfg.known:
        parts = frame.future_covariates.get(name)
        if not parts:
            continue
        full = list(parts.get("past") or []) + list(parts.get("future") or [])
        if len(full) >= o + horizon:
            future_cov[name] = {"past": full[s0:o], "future": full[o : o + horizon]}
    return {
        "task_id": f"{cfg_name}@{o}",
        "start": dates[s0][:10],
        "freq": "D",
        "targets": {target_id: window(frame.targets[target_id])},
        "past_covariates": past_cov,
        "future_covariates": future_cov,
    }


async def _run_configs(
    url: str,
    frame: FeatureFrame,
    target_id: str,
    configs: dict[str, FeatureChoice],
    origins: list[int],
    horizon: int,
    gap: int,
    model: str,
) -> dict[str, float]:
    tasks: list[dict[str, Any]] = []
    for name, cfg in configs.items():
        for o in origins:
            tasks.append(_task(frame, target_id, name, cfg, o, horizon, gap))
    batches = [tasks[i : i + _TASKS_PER_REQUEST] for i in range(0, len(tasks), _TASKS_PER_REQUEST)]
    sem = asyncio.Semaphore(_CONCURRENCY)
    scores: dict[str, list[float]] = {n: [] for n in configs}
    series = frame.targets[target_id]

    async with httpx.AsyncClient(timeout=120) as client:

        async def _one(batch: list[dict[str, Any]]) -> None:
            body = {
                "tasks": batch,
                "prediction_length": horizon,
                "quantile_levels": [0.1, 0.5, 0.9],
                "model": model,
            }
            async with sem:
                resp = await client.post(f"{url}/v2/forecast", json=body)
            if resp.status_code != 200:
                raise ForecastUnavailable(
                    f"bakeoff chronos http {resp.status_code}", warning="bakeoff_failed"
                )
            for task in resp.json().get("tasks", []):
                name, _, o_s = str(task["task_id"]).rpartition("@")
                o = int(o_s)
                fc = [p["p50"] for p in task["targets"][target_id]["forecast"]]
                s = _score(fc, series[o : o + horizon])
                if s is not None and name in scores:
                    scores[name].append(s)

        try:
            await asyncio.gather(*[_one(b) for b in batches])
        except httpx.HTTPError as exc:
            raise ForecastUnavailable(f"bakeoff chronos error: {exc}", warning="bakeoff_failed") from exc
    return {n: statistics.mean(v) for n, v in scores.items() if len(v) >= max(4, len(origins) // 2)}


async def run_bakeoff(
    frame: FeatureFrame,
    target_id: str,
    candidates: list[str],
    *,
    chronos_url: str,
    known_names: list[str],
    model: str = "chronos-2",
    use_cache: bool = True,
) -> BakeoffResult:
    horizon = max(7, min(30, (frame.horizon_end - frame.horizon_start).days + 1))
    gap = max(0, (frame.horizon_start - frame.cutoff).days - 1)
    cands = [c for c in candidates if c in frame.past_covariates and c != target_id][:MAX_CANDIDATES]
    known = [k for k in known_names if k in frame.future_covariates]
    key = (target_id, frame.cutoff.isoformat(), horizon, tuple(cands), tuple(known), frame.content_hash[:12])
    hit = _CACHE.get(key)
    if use_cache and hit and time.time() - hit[0] < _CACHE_TTL_S:
        return hit[1]

    t0 = time.perf_counter()
    origins = _origins(frame, target_id, horizon, gap)
    if len(origins) < 4:
        raise ForecastUnavailable("not enough history for a feature bake-off", warning="bakeoff_thin")

    scores: dict[str, float] = {}
    base_cfgs = {"history only": FeatureChoice(), "history + calendar": FeatureChoice(known=known)}
    r1 = await _run_configs(chronos_url, frame, target_id, base_cfgs, origins, horizon, gap, model)
    scores.update(r1)
    if "history only" not in r1:
        raise ForecastUnavailable("bake-off produced no baseline score", warning="bakeoff_failed")
    baseline_name, baseline = "history only", r1["history only"]
    best_name, best, best_cfg = baseline_name, baseline, FeatureChoice()
    cal = r1.get("history + calendar")
    if cal is not None and known and cal < best * (1 - MIN_GAIN):
        best_name, best, best_cfg = "history + calendar", cal, FeatureChoice(known=known)

    # Round 1: each candidate added on top of the winner.
    cfgs1 = {
        f"{best_name} + {c}": FeatureChoice(past=[*best_cfg.past, c], known=list(best_cfg.known))
        for c in cands
    }
    r2 = await _run_configs(chronos_url, frame, target_id, cfgs1, origins, horizon, gap, model) if cfgs1 else {}
    scores.update(r2)
    ranked = sorted(r2.items(), key=lambda kv: kv[1])
    if ranked and ranked[0][1] < best * (1 - MIN_GAIN):
        best_name, best = ranked[0]
        best_cfg = cfgs1[best_name]
        # Round 2: greedy second covariate from the next-best singles.
        used = set(best_cfg.past)
        nxt = [n for n, _ in ranked[1 : 1 + SECOND_ROUND] if cfgs1[n].past[-1] not in used]
        cfgs2 = {
            f"{best_name} + {cfgs1[n].past[-1]}": FeatureChoice(
                past=[*best_cfg.past, cfgs1[n].past[-1]], known=list(best_cfg.known)
            )
            for n in nxt
        }
        r3 = await _run_configs(chronos_url, frame, target_id, cfgs2, origins, horizon, gap, model) if cfgs2 else {}
        scores.update(r3)
        top3 = sorted(r3.items(), key=lambda kv: kv[1])
        if top3 and top3[0][1] < best * (1 - MIN_GAIN):
            best_name, best = top3[0]
            best_cfg = cfgs2[best_name]

    res = BakeoffResult(
        choice=best_cfg,
        scores=scores,
        baseline_name=baseline_name,
        baseline_score=baseline,
        chosen_name=best_name,
        chosen_score=best,
        n_origins=len(origins),
        horizon=horizon,
        seconds=time.perf_counter() - t0,
    )
    _CACHE[key] = (time.time(), res)
    _log.info("bakeoff target=%s chosen=%s score=%.3f base=%.3f secs=%.1f", target_id, best_name, best, baseline, res.seconds)
    return res
