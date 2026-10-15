"""RiskLoc decision test (plan P0.9): do real changes sit in cross-dimension combinations that single-dimension splits miss?

usage: python3 riskloc_probe.py <leaf_fixture_id>

Uses leaf-level real data (every combination of the dimensions, daily). For each day the reference of a leaf is the median
of the same leaf on the same weekday over the previous four weeks (missing = 0). The change of a leaf is value - reference.
A *pattern* fixes values for k dimensions; its share of the change is the sum of the changes of the leaves it matches
divided by the total change. Real events are days whose total sits far outside its reference (robust z >= 2.5, found
without the engine); ordinary days (|z| < 1) give the chance level, because a search over many patterns always finds one
that looks concentrated. Output: per event the best pattern at k = 1, 2, 3, and the same statistic on ordinary days.

This measures how much a cross-dimension search could add over single-dimension localisation on this brand's own data. It
does not decide the question alone: planted cross-dimension worlds are cross-dimensional by construction (plan P6.1b).
"""

from __future__ import annotations

import itertools
import json
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import world_fixture as W

REF_WEEKS = 4
Z_EVENT, Z_ORDINARY = 2.0, 1.0
PURITY = 0.5  # a pattern is coherent when its net change is at least this share of its gross movement
MAX_ORDER = 3


def load(fixture_id: str) -> dict:
    raw = json.loads((W.FIXTURES / f"{fixture_id}.json").read_text())
    data = {k: raw[k] for k in ("measure", "dimensions", "cells")}
    if W.fingerprint(data) != raw["sha256"]:
        raise W.FixtureError(f"{fixture_id}: sha256 mismatch")
    return raw


def main(fixture_id: str) -> None:
    raw = load(fixture_id)
    dims = raw["dimensions"]
    by_leaf: dict[tuple[str, ...], dict[date, float]] = defaultdict(dict)
    for c in raw["cells"]:
        by_leaf[tuple(c["leaf"])][date.fromisoformat(c["day"])] = c["value"]
    start, end = date.fromisoformat(raw["start"]), date.fromisoformat(raw["end"])
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]

    def ref(leaf: tuple[str, ...], d: date) -> float:
        vals = [by_leaf[leaf].get(d - timedelta(days=7 * k), 0.0) for k in range(1, REF_WEEKS + 1)]
        return statistics.median(vals)

    scored = [d for d in days if d - timedelta(days=7 * REF_WEEKS) >= start]
    delta = {d: {leaf: by_leaf[leaf].get(d, 0.0) - ref(leaf, d) for leaf in by_leaf} for d in scored}
    total = {d: sum(delta[d].values()) for d in scored}
    resid = list(total.values())
    med = statistics.median(resid)
    mad = 1.4826 * statistics.median(abs(x - med) for x in resid)
    z = {d: (total[d] - med) / mad for d in scored}

    def best(d: date, k: int, by_abs_total: bool, coherent: bool = False) -> tuple[float, tuple]:
        """Best pattern of k fixed dimensions: largest |sum of changes| over L1 (concentration) or over the total change.

        ``coherent`` keeps only patterns whose leaves mostly move the same way (purity), so a wide pattern whose leaves
        cancel cannot win; this is what a root-cause set must be (all its leaves anomalous together).
        """
        l1 = sum(abs(v) for v in delta[d].values()) or 1.0
        denom = abs(total[d]) if by_abs_total else l1
        top = (0.0, ())
        for combo in itertools.combinations(range(len(dims)), k):
            acc: dict[tuple[str, ...], float] = defaultdict(float)
            gross: dict[tuple[str, ...], float] = defaultdict(float)
            for leaf, v in delta[d].items():
                key = tuple(leaf[i] for i in combo)
                acc[key] += v
                gross[key] += abs(v)
            for key, v in acc.items():
                if coherent and abs(v) < PURITY * gross[key]:
                    continue
                # a share of the total change counts only if it moves the same way as the total
                s = (v * (1 if total[d] >= 0 else -1)) / denom if by_abs_total else abs(v) / denom
                if s > top[0]:
                    top = (s, tuple(f"{dims[i]}={key[j]}" for j, i in enumerate(combo)))
        return top

    events = sorted((d for d in scored if abs(z[d]) >= Z_EVENT), key=lambda d: -abs(z[d]))
    ordinary = [d for d in scored if abs(z[d]) < Z_ORDINARY]
    rows = []
    for d in events:
        rows.append({"day": str(d), "z": round(z[d], 2), "change": round(total[d]),
                     "share_of_change": {k: [round(best(d, k, True)[0], 3), best(d, k, True)[1]] for k in range(1, MAX_ORDER + 1)},
                     "coherent_share": {k: [round(best(d, k, True, True)[0], 3), best(d, k, True, True)[1]] for k in range(1, MAX_ORDER + 1)},
                     "concentration": {k: round(best(d, k, False)[0], 3) for k in range(1, MAX_ORDER + 1)}})
    null = {k: sorted(best(d, k, False)[0] for d in ordinary) for k in range(1, MAX_ORDER + 1)}

    def q(xs: list[float], p: float) -> float:
        return round(xs[min(len(xs) - 1, int(p * len(xs)))], 3)

    out = {"fixture": fixture_id, "sha256": raw["sha256"], "dimensions": dims, "leaves": len(by_leaf), "days_scored": len(scored),
           "events": len(events), "ordinary_days": len(ordinary),
           "event_median": {k: round(statistics.median(r["concentration"][k] for r in rows), 3) for k in range(1, MAX_ORDER + 1)} if rows else {},
           "event_median_share_of_change": {k: round(statistics.median(r["share_of_change"][k][0] for r in rows), 3) for k in range(1, MAX_ORDER + 1)} if rows else {},
           "event_median_coherent_share": {k: round(statistics.median(r["coherent_share"][k][0] for r in rows), 3) for k in range(1, MAX_ORDER + 1)} if rows else {},
           "ordinary_concentration": {k: {"median": q(null[k], 0.5), "p90": q(null[k], 0.9)} for k in null},
           "rows": rows}
    path = HERE / "results" / f"p0_9_riskloc_probe_{fixture_id}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=1))
    for r in rows:
        print(r["day"], f"z={r['z']:+.1f}", f"change={r['change']:+d}", " | ".join(f"k{k}: {v[0]:.2f}/{r['coherent_share'][k][0]:.2f} {','.join(r['coherent_share'][k][1])[:40]}" for k, v in r["share_of_change"].items()))


if __name__ == "__main__":
    main(sys.argv[1])
