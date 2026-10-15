"""Run every case offline, score it, write the results (plan P0.6/P0.7).

usage: python3 run_suite.py [--out DIR] [--only id,id] [--allow-dirty]

Refuses to score while the engine files differ from HEAD (teammates edit this tree): a score filed under a commit
must be the score of that commit. The results file records HEAD, the dirty paths, each fixture's sha256 and every
case's raw report, so a number can be traced and re-derived.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


SHIFTS = (-2, -1, 1)  # days; a shift that leaves the fixture is skipped
SUBSAMPLE = {"drop_frac": 0.2, "seeds": (1, 2, 3)}
REPEATS = 2


def _jaccard(a: set[str], b: set[str]) -> float:
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def run_stability(cases, scores, R, S) -> dict:
    """How much the answer moves when the question or the data is perturbed slightly (plan P0.6).

    Variants: the window shifted by a day or two, 20% of the history days removed (seeded), and the unchanged run again
    (the engine is stochastic if repeats differ). Agreement is measured against the unperturbed run: same verdict, and the
    overlap (Jaccard) of the top-3 listed drivers and of the named causes.
    """
    out = {}
    base = {s.case_id: s for s in scores}
    for case in cases:
        b = base[case.id]
        if not b.ran:
            continue
        variants = [("shift", f"{k:+d}", {"shift_days": k}) for k in SHIFTS] + \
                   [("subsample", f"seed{sd}", {"drop_frac": SUBSAMPLE["drop_frac"], "seed": sd}) for sd in SUBSAMPLE["seeds"]] + \
                   [("repeat", str(i + 1), {}) for i in range(REPEATS)]
        rows = []
        for kind, label, kw in variants:
            try:
                res = R.run_case(case, **kw)
            except ValueError:
                continue  # window outside the fixture
            sc = S.score_case(case, res.report, res.success, res.error, res.queries, res.planted, json.loads(R.GRAPH_PATH.read_text()))
            rows.append({"kind": kind, "label": label, "ran": sc.ran, "verdict": sc.verdict, "same_verdict": sc.verdict == b.verdict,
                         "top3_jaccard": _jaccard(set(sc.listed[:3]), set(b.listed[:3])), "named_jaccard": _jaccard(set(sc.named), set(b.named))})
        out[case.id] = rows
        n = len(rows)
        print(f"  stability {case.id:34s} same verdict {sum(r['same_verdict'] for r in rows)}/{n}  top3 overlap {sum(r['top3_jaccard'] for r in rows) / max(n, 1):.2f}", flush=True)
    flat = [r for rows in out.values() for r in rows]
    by_kind = {k: {"n": len([r for r in flat if r["kind"] == k]),
                   "same_verdict": round(sum(r["same_verdict"] for r in flat if r["kind"] == k) / max(1, len([r for r in flat if r["kind"] == k])), 3),
                   "top3_jaccard": round(sum(r["top3_jaccard"] for r in flat if r["kind"] == k) / max(1, len([r for r in flat if r["kind"] == k])), 3)}
               for k in ("shift", "subsample", "repeat")}
    return {"by_kind": by_kind, "cases": out}


def main(argv: list[str]) -> int:
    import case_format as C
    import runner as R
    import score as S

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--only", default="")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--stability", action="store_true", help="also run window shifts, history subsamples and repeats per case")
    args = ap.parse_args(argv)
    state = R.tree_state()
    if state["engine_dirty"] and not args.allow_dirty:
        print("refusing to score: engine files differ from HEAD:\n  " + "\n  ".join(state["engine_dirty"]), file=sys.stderr)
        return 2
    graph = json.loads(R.GRAPH_PATH.read_text())
    cases = C.load_cases(HERE / "cases")
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c.id in wanted]
    out = Path(args.out) if args.out else HERE / "results" / state["head"][:10]
    out.mkdir(parents=True, exist_ok=True)
    scores, raw = [], {}
    for case in cases:
        t0 = time.time()
        res = R.run_case(case)
        sc = S.score_case(case, res.report, res.success, res.error, res.queries, res.planted, graph)
        scores.append(sc)
        raw[case.id] = {"summary": res.summary, "report": res.report, "seconds": round(time.time() - t0, 1)}
        print(f"{case.id:40s} {sc.verdict or 'CRASH':32s} named={sc.named[:3]} false={sc.false_attributions} {raw[case.id]['seconds']}s", flush=True)
    stab = run_stability(cases, scores, R, S) if args.stability else None
    summary = {"stability_runs": stab, "engine": state, "dirty_allowed": args.allow_dirty, "fixtures": sorted({c.world.fixture for c in cases}),
               "aggregate": S.aggregate(scores), "cases": [asdict(s) for s in scores]}
    (out / "scores.json").write_text(json.dumps(summary, indent=1, default=str))
    (out / "reports.json").write_text(json.dumps(raw, default=str))
    print(json.dumps(summary["aggregate"], indent=1))
    print(f"written {out}")
    return 0


if __name__ == "__main__":  # the estimate pool is a forkserver and re-imports this module
    sys.exit(main(sys.argv[1:]))
