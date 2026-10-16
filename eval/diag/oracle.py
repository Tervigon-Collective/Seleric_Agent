"""Phase 2 oracle (plan P2, advisor 2026-10-16): freeze what the diagnosis tool returns, then prove a refactor changed nothing.

The Phase 0 scorer reads verdicts and driver lists; the agent narrates the whole ``summary`` (ladder, ratio split,
named drivers, levers) and the artifacts. This records all of it per case, and the pure engine input
(``DiagnosisInput``) so a change to ``causal/diagnosis.py`` can be checked in seconds without the tool around it.

usage:
  python3 oracle.py record DIR [--only id,id]     run the cases, write DIR/<case>.json and DIR/<case>.input.pkl
  python3 oracle.py compare A B [--tol 1e-6]      categorical equality, floats within tol (exit 1 on a difference)
  python3 oracle.py engine DIR                    re-run engine.diagnose on DIR's recorded inputs, compare with DIR's report
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

IGNORED_SOURCE_KEYS = {"investigation_id", "phase_ms"}
VOLATILE_KEYS = {"fetched_at"}  # wall-clock stamps the evidence rows carry; they differ on every run


def _plain(x: Any) -> Any:
    """JSON-able, key order stable."""
    if hasattr(x, "model_dump"):
        return _plain(x.model_dump(mode="json"))
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in sorted(x.items(), key=lambda kv: str(kv[0]))}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, (set, frozenset)):
        return sorted((_plain(v) for v in x), key=str)
    if isinstance(x, float) or x is None or isinstance(x, (bool, int, str)):
        return x
    return str(x)


def _normalise_ids(blob: dict[str, Any], ids: list[str]) -> dict[str, Any]:
    """Artifact and evidence ids are random per run; rename each stored id to the order it was created in."""
    text = json.dumps(blob, sort_keys=True)
    for i, known in enumerate(ids):
        text = text.replace(known, f"<id{i}>")
    return json.loads(text)


def capture(res: Any) -> dict[str, Any]:
    tr = res.tool_result
    meta = {k: v for k, v in (tr.provenance.source_metadata or {}).items() if k not in IGNORED_SOURCE_KEYS} if tr else {}
    kinds = {a.id: a.artifact_type for a in res.artifacts}
    blob = {
        "success": res.success,
        "error": res.error,
        "error_code": getattr(tr, "error_code", None),
        "summary": res.summary,
        "warnings": list(getattr(tr, "warnings", []) or []),
        "queries": res.queries,
        "artifact_ids": list(getattr(tr, "artifact_ids", []) or []),
        "source_metadata": meta,
        "artifacts": [  # in creation order: the store keeps insertion order, ids are renamed by it below
            {"type": a.artifact_type, "classification": getattr(a, "classification", None), "payload": a.payload,
             "evidence_ids": list(getattr(a, "evidence_ids", []) or [])}
            for a in res.artifacts
        ],
        "artifact_kinds": sorted(kinds.values()),
    }
    ordered = [a.id for a in res.artifacts]
    return _normalise_ids(_plain(blob), ordered)


def diff(a: Any, b: Any, tol: float, path: str = "") -> list[str]:
    if isinstance(a, float) or isinstance(b, float):
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
            if a == b or abs(a - b) <= tol * max(1.0, abs(a), abs(b)):
                return []
            return [f"{path}: {a!r} != {b!r}"]
        return [f"{path}: {a!r} != {b!r}"]
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[str] = []
        for k in sorted((set(a) | set(b)) - VOLATILE_KEYS):
            if k not in a or k not in b:
                out.append(f"{path}/{k}: only in {'A' if k in a else 'B'}")
            else:
                out += diff(a[k], b[k], tol, f"{path}/{k}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} != {len(b)}"]
        return [d for i, (x, y) in enumerate(zip(a, b, strict=True)) for d in diff(x, y, tol, f"{path}[{i}]")]
    return [] if a == b else [f"{path}: {str(a)[:160]!r} != {str(b)[:160]!r}"]


def record(out: Path, only: set[str]) -> int:
    import case_format as C
    import runner as R

    sys.path.insert(0, str(R.ROOT / "src"))
    from seleric_swarm.causal import diagnosis as engine

    out.mkdir(parents=True, exist_ok=True)
    taken: list[Any] = []
    real = engine.diagnose

    def spy(inp: Any, *args: Any, **kwargs: Any) -> Any:
        taken.append(inp)
        return real(inp, *args, **kwargs)

    engine.diagnose = spy  # the tool calls ``engine.diagnose`` through the module, so this sees its input
    try:
        for case in C.load_cases(HERE / "cases"):
            if only and case.id not in only:
                continue
            taken.clear()
            res = R.run_case(case)
            (out / f"{case.id}.json").write_text(json.dumps(capture(res), indent=1, default=str))
            (out / f"{case.id}.input.pkl").write_bytes(pickle.dumps(list(taken)))
            print(f"{case.id:40s} success={res.success} inputs={len(taken)}", flush=True)
    finally:
        engine.diagnose = real
    return 0


def compare(a: Path, b: Path, tol: float) -> int:
    bad = 0
    for fa in sorted(a.glob("*.json")):
        fb = b / fa.name
        if not fb.exists():
            print(f"{fa.stem}: missing in B")
            bad += 1
            continue
        d = diff(json.loads(fa.read_text()), json.loads(fb.read_text()), tol)
        print(f"{fa.stem:40s} {'same' if not d else f'{len(d)} differences'}")
        for line in d[:12]:
            print("    ", line)
        bad += bool(d)
    return 1 if bad else 0


def engine_check(d: Path, tol: float) -> int:
    import runner as R

    sys.path.insert(0, str(R.ROOT / "src"))
    from seleric_swarm.causal import diagnosis as engine

    bad = 0
    for pkl in sorted(d.glob("*.input.pkl")):
        case = pkl.name.removesuffix(".input.pkl")
        want = json.loads((d / f"{case}.json").read_text())["source_metadata"].get("diagnosis")
        inputs = pickle.loads(pkl.read_bytes())
        got = [_plain(engine.diagnose(i).as_dict()) for i in inputs]
        if not inputs:
            print(f"{case:40s} no engine input (the tool stopped before the engine)")
            continue
        # the tool puts its own notes and fetch-quality lines in front of the engine's data_quality
        engine_dq = got[-1].get("data_quality") or []
        if want and engine_dq and want.get("data_quality", [])[-len(engine_dq):] == engine_dq:
            want = {**want, "data_quality": engine_dq}
        diffs = diff(want, got[-1], tol)
        print(f"{case:40s} {'same' if not diffs else f'{len(diffs)} differences'}")
        for line in diffs[:12]:
            print("    ", line)
        bad += bool(diffs)
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("dir")
    r.add_argument("--only", default="")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--tol", type=float, default=1e-6)
    e = sub.add_parser("engine")
    e.add_argument("dir")
    e.add_argument("--tol", type=float, default=1e-6)
    args = ap.parse_args(argv)
    if args.cmd == "record":
        return record(Path(args.dir), set(filter(None, args.only.split(","))))
    if args.cmd == "compare":
        return compare(Path(args.a), Path(args.b), args.tol)
    return engine_check(Path(args.dir), args.tol)


if __name__ == "__main__":  # the estimate pool is a forkserver and re-imports this module
    sys.exit(main(sys.argv[1:]))
