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


def main(argv: list[str]) -> int:
    import case_format as C
    import runner as R
    import score as S

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--only", default="")
    ap.add_argument("--allow-dirty", action="store_true")
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
    summary = {"engine": state, "dirty_allowed": args.allow_dirty, "fixtures": sorted({c.world.fixture for c in cases}),
               "aggregate": S.aggregate(scores), "cases": [asdict(s) for s in scores]}
    (out / "scores.json").write_text(json.dumps(summary, indent=1, default=str))
    (out / "reports.json").write_text(json.dumps(raw, default=str))
    print(json.dumps(summary["aggregate"], indent=1))
    print(f"written {out}")
    return 0


if __name__ == "__main__":  # the estimate pool is a forkserver and re-imports this module
    sys.exit(main(sys.argv[1:]))
