"""``python -m seleric_swarm.diagnosis.cli diff <a.json> <b.json>``: what changed between two recorded runs, from the
inputs down to the answer (plan P3.4, D-21 item 6). The first section that differs is the likely cause of the later
ones, so sections come in the order a run depends on them: versions and settings, the subgraph and the plan's driver
list, the data, the events, the report. Exit status: 0 the runs agree, 1 they differ, 2 a record cannot be read."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from seleric_swarm.diagnosis.cli.explain import load_record, subgraph_of
from seleric_swarm.diagnosis.domain.compare import differences
from seleric_swarm.diagnosis.domain.errors import DiagnosisError
from seleric_swarm.diagnosis.domain.record import RunRecord

DEFAULT_TOLERANCE = 1e-6
_MAX_LINES = 12  # per section; the rest is counted


def _clip(lines: list[str]) -> list[str]:
    return lines if len(lines) <= _MAX_LINES else [*lines[:_MAX_LINES], f"... and {len(lines) - _MAX_LINES} more"]


def _set_diff(label: str, a: list[str], b: list[str]) -> list[str]:
    out = [f"{label} only in A: {', '.join(sorted(set(a) - set(b)))}"] if set(a) - set(b) else []
    out += [f"{label} only in B: {', '.join(sorted(set(b) - set(a)))}"] if set(b) - set(a) else []
    if not out and a != b:
        out.append(f"{label} same members, different order: {a} vs {b}")
    return out


def _plan(record: RunRecord) -> dict[str, Any]:
    return dict(record.request.get("plan") or {})


def _drivers(record: RunRecord) -> dict[str, tuple[Any, Any]]:
    return {d["driver"]: (d.get("status"), d.get("classification")) for d in (record.report or {}).get("drivers") or []}


def diff_records(a: RunRecord, b: RunRecord, tol: float = DEFAULT_TOLERANCE) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    sections["versions and settings"] = _clip(differences(dict(a.versions), dict(b.versions), 0.0) + differences(dict(a.config), dict(b.config), tol))
    pa, pb = _plan(a), _plan(b)
    plan: list[str] = []
    for key in ("outcome", "start", "end", "scope", "event_days", "baseline_days", "claimed_direction"):
        plan += differences(pa.get(key), pb.get(key), 0.0, key)
    plan += _set_diff("plan drivers", list(pa.get("drivers") or []), list(pb.get("drivers") or []))
    plan += _set_diff("series fetched", list(pa.get("series_ids") or []), list(pb.get("series_ids") or []))
    sa, sb = subgraph_of(a), subgraph_of(b)
    if (sa is None) != (sb is None):
        plan.append(f"subgraph: only in {'A' if sa else 'B'}")
    elif sa is not None and sb is not None and sa != sb:
        plan += [f"subgraph graph_version {sa.graph_version} != {sb.graph_version}"] if sa.graph_version != sb.graph_version else []
        plan += _set_diff("subgraph candidates", [c.metric for c in sa.candidates], [c.metric for c in sb.candidates])
        plan += _set_diff("subgraph exclusions", [f"{e.metric}:{e.reason_code}" for e in sa.exclusions], [f"{e.metric}:{e.reason_code}" for e in sb.exclusions])
    sections["plan and subgraph"] = _clip(plan)
    fa = {s.request.get("metric_id", ""): s.fingerprint for s in a.snapshots if not s.request.get("dimension")}
    fb = {s.request.get("metric_id", ""): s.fingerprint for s in b.snapshots if not s.request.get("dimension")}
    data = _set_diff("series recorded", list(fa), list(fb))
    data += [f"series {m} differs" for m in sorted(set(fa) & set(fb)) if fa[m] != fb[m]]
    sections["data"] = _clip(data)
    ea, eb = Counter(e["kind"] for e in a.events), Counter(e["kind"] for e in b.events)
    events = [f"{k}: {ea[k]} != {eb[k]}" for k in sorted(set(ea) | set(eb)) if ea[k] != eb[k]]
    first = next((i for i, (x, y) in enumerate(zip(a.events, b.events, strict=False)) if x["kind"] != y["kind"] or differences(x.get("payload"), y.get("payload"), tol)), None)
    if first is not None:
        events.append(f"first event that differs: #{first + 1} ({a.events[first]['kind']} / {b.events[first]['kind']})")
    sections["events"] = _clip(events)
    ra, rb = a.report or {}, b.report or {}
    report = [f"verdict {ra.get('verdict')!r} != {rb.get('verdict')!r}"] if ra.get("verdict") != rb.get("verdict") else []
    da, db = _drivers(a), _drivers(b)
    report += [f"driver {m}: {da.get(m, 'absent')} -> {db.get(m, 'absent')}" for m in sorted(set(da) | set(db)) if da.get(m) != db.get(m)]
    report += [f"report{d}" for d in differences({k: v for k, v in ra.items() if k not in ("drivers", "verdict")}, {k: v for k, v in rb.items() if k not in ("drivers", "verdict")}, tol)]
    sections["report"] = _clip(report)
    return {k: v for k, v in sections.items() if v}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="diagnosis diff")
    parser.add_argument("a", type=Path)
    parser.add_argument("b", type=Path)
    parser.add_argument("--tol", type=float, default=DEFAULT_TOLERANCE, help="relative tolerance for numbers")
    args = parser.parse_args(argv)
    try:
        a, b = load_record(args.a), load_record(args.b)
    except (OSError, ValueError, KeyError, DiagnosisError) as exc:
        print(f"cannot read a record: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    sections = diff_records(a, b, args.tol)
    print(f"A {a.investigation_id}  B {b.investigation_id}")
    if not sections:
        print("no differences")
        return 0
    for name, lines in sections.items():
        print(f"{name}:")
        for line in lines:
            print(f"  {line}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
