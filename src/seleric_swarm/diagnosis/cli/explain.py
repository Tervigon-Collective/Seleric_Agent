"""``python -m seleric_swarm.diagnosis.cli explain <record.json> --metric M``: why was (or was not) metric M a candidate
cause in this run, and what became of it (plan P3.4, D-21 item 4). Every answer names the step that decided it and,
where there is one, the event that shows it, so "why was X not blamed" is read off the record instead of guessed."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from seleric_swarm.diagnosis.domain.errors import DiagnosisError
from seleric_swarm.diagnosis.domain.record import RunRecord
from seleric_swarm.diagnosis.domain.subgraph import Subgraph
from seleric_swarm.diagnosis.trace import events as kinds

_NO_SUBGRAPH = "this run froze no subgraph (it ran before the graph was consulted, or the graph was rejected)"


def subgraph_of(record: RunRecord) -> Subgraph | None:
    """The subgraph the run used: the record's own copy, else the one in its plan."""
    raw: Mapping[str, Any] | None = record.subgraph or (record.request.get("plan") or {}).get("subgraph")
    return Subgraph.from_dict(raw) if raw else None


def _events_for(record: RunRecord, metric: str) -> list[tuple[int, str, str]]:
    """(seq, kind, what) of the events that name the metric in the way a reader asks about."""
    found: list[tuple[int, str, str]] = []
    for e in record.events:
        payload = e.get("payload") or {}
        kind = e["kind"]
        if kind == kinds.EXCLUSION and payload.get("metric") == metric:
            found.append((e["seq"], kind, f"{payload.get('reason_code')} ({payload.get('check_value')})"))
        elif kind == kinds.SUBGRAPH_BUILT and metric in (payload.get("candidates") or []):
            found.append((e["seq"], kind, "listed among the candidates"))
        elif kind in (kinds.HYPOTHESIS_OPENED, kinds.HYPOTHESIS_TESTED, kinds.HYPOTHESIS_RESOLVED):
            if metric in str(payload.get("hypothesis")):
                found.append((e["seq"], kind, str(payload.get("status", ""))))
        elif kind == kinds.REFUTER_RESULT and metric in str(payload.get("hypothesis")):
            found.append((e["seq"], kind, f"{payload.get('refuter')} passed={payload.get('passed')}"))
    return found


def explain_metric(record: RunRecord, metric: str) -> list[str]:
    lines: list[str] = []
    plan = record.request.get("plan") or {}
    sub = subgraph_of(record)
    where = "graph"
    if sub is None:
        lines.append(f"{where}: {_NO_SUBGRAPH}")
    elif metric == sub.outcome:
        lines.append(f"{where}: {metric} is the outcome itself")
    elif metric in sub.accounting:
        lines.append(f"{where}: part of the outcome's arithmetic ({sub.outcome}), reported by the decomposition, never offered as a cause")
    elif (cands := {c.metric: (i, c) for i, c in enumerate(sub.candidates, 1)}) and metric in cands:
        i, c = cands[metric]
        lines.append(
            f"{where}: candidate cause #{i} of {len(cands)} in graph order, {c.distance} edge(s) from {c.anchor} via {c.via} "
            f"({c.kind}, weakest edge {c.path_confidence}, lag {c.lag_days[0]}..{c.lag_days[1]} d, a rise moves {c.anchor} {c.direction})"
        )
    else:
        reasons = [e for e in sub.exclusions if e.metric == metric]
        for e in reasons:
            lines.append(f"{where}: set aside, {e.reason_code} ({e.action}; check against {e.check_value or 'n/a'})")
        for p in (p for p in sub.peers if p.peer == metric):
            lines.append(f"{where}: another measurement of {p.metric} ({p.kind}; expected: {p.expected or 'n/a'}); not a cause")
        if not reasons and not any(p.peer == metric for p in sub.peers):
            lines.append(f"{where}: not reached from {sub.outcome} within {sub.depth_limit} edge(s) of the reviewed graph")
    if plan:
        offered = metric in (plan.get("drivers") or [])
        lines.append(f"plan: {'offered to the engine as a driver' if offered else 'not in the driver list the engine was given'}"
                     f"{'' if metric in (plan.get('series_ids') or []) else '; its series was not fetched'}")
    answers = [s for s in record.snapshots if s.request.get("metric_id") == metric]
    if answers:
        errors = sorted({s.error for s in answers if s.error})
        lines.append(f"data: {len(answers)} recorded answer(s)" + (f", failures: {errors}" if errors else ""))
    for d in (record.report or {}).get("drivers") or []:
        if d.get("driver") == metric:
            share = d.get("share_of_change")
            lines.append(
                f"engine: {d.get('status')}"
                f"{', ' + d['classification'] if d.get('classification') else ''}: {d.get('reason') or 'estimated'}"
                f"{'' if share is None else f' (share of change {share:.2f})'}"
            )
    lines += [f"event #{seq} {kind}: {what}" for seq, kind, what in _events_for(record, metric)]
    return lines


def load_record(path: Path) -> RunRecord:
    return RunRecord.from_json(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="diagnosis explain")
    parser.add_argument("record", type=Path, help="a run record JSON file")
    parser.add_argument("--metric", required=True, help="the metric to ask about")
    args = parser.parse_args(argv)
    try:
        record = load_record(args.record)
    except (OSError, ValueError, KeyError, DiagnosisError) as exc:
        print(f"cannot read {args.record}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"{record.investigation_id}: {args.metric}")
    for line in explain_metric(record, args.metric):
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
