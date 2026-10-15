"""Snapshot real brand series into a versioned eval fixture (plan P0.2).

usage: python3 snapshot.py <fixture_id> <start> <end> [dim,dim,...]

Metrics are chosen from the vendored dependency graph, never named here: every metric on a mechanism/feedback
edge plus its accounting neighbours (composition / ratio_component / declared_input, one hop). Series come from
the semantic layer through the same MCP tool the agent uses; segments are fetched for the given dimensions where
the metric supports them. The fixture records each source query and a sha256 over the data, so a case can pin it.

Writes eval/diag/fixtures/<fixture_id>.json. Totals are cross-checked against ClickHouse by crosscheck.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "regress"))
import mcp_direct

GRAPH = os.path.join(HERE, "..", "..", "src", "seleric_swarm", "causal", "data", "dependency_graph.json")
BATCH = 24
DEFINITION_BATCH = 10  # catalogue_get_metrics accepts at most 10 ids per call
CAUSAL_KINDS = ("mechanism", "feedback")
ACCOUNTING_KINDS = ("composition", "ratio_component", "declared_input")


def metric_set(graph: dict[str, Any]) -> list[str]:
    core = {e[k] for e in graph["edges"] if e["kind"] in CAUSAL_KINDS for k in ("from", "to")}
    near = {e[k] for e in graph["edges"] if e["kind"] in ACCOUNTING_KINDS
            for k, other in (("from", "to"), ("to", "from")) if e[other] in core}
    ids = {n["id"] for n in graph["nodes"]}
    return sorted((core | near) & ids)


def fingerprint(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _day(row: dict[str, Any]) -> str | None:
    for k, v in row.items():
        if k.endswith(".day") and v:
            return str(v)[:10]
    return None


def _parts(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in (result["parts"] if isinstance(result.get("parts"), list) else [result]) if isinstance(p, dict)]


def query(measures: list[str], start: str, end: str, brand: int, dimension: str | None, log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    args: dict[str, Any] = {"measures": measures, "time_range": {"start": start, "end": end}, "granularity": "day",
                            "filters": [{"dimension": "brand_id", "operator": "equals", "values": [brand]}]}
    if dimension:
        args["dimensions"] = [dimension]
        args["limit"] = 50000
    result = mcp_direct.call_tool("metrics_query", args)
    log.append({"arguments": args, "query_id": result.get("query_id"), "error": result.get("error"), "warnings": result.get("warnings")})
    if result.get("error"):
        raise RuntimeError(f"{measures[:3]}... {result['error']}")
    return _parts(result)


def main(fixture_id: str, start: str, end: str, dims: list[str]) -> None:
    with open(GRAPH) as fh:
        graph = json.load(fh)
    nodes = {n["id"]: n for n in graph["nodes"]}
    metrics = metric_set(graph)
    brand = 20
    defs: dict[str, Any] = {}
    catalogue_version = ""
    for i in range(0, len(metrics), DEFINITION_BATCH):
        got = mcp_direct.call_tool("catalogue_get_metrics", {"metric_ids": metrics[i:i + DEFINITION_BATCH]})
        if got.get("error"):
            raise RuntimeError(f"catalogue_get_metrics: {got['error']}")
        defs.update(got.get("metrics") or {})
        catalogue_version = str(got.get("catalogue_version") or catalogue_version)
    if set(metrics) - set(defs):
        raise RuntimeError(f"no catalogue definition for {sorted(set(metrics) - set(defs))}")
    log: list[dict[str, Any]] = []
    by_view: dict[str, list[str]] = {}
    for m in metrics:
        by_view.setdefault(nodes[m]["view"], []).append(m)
    series: dict[str, dict[str, float]] = {}
    segments: dict[str, dict[str, dict[str, dict[str, float]]]] = {}

    unavailable: dict[str, str] = {}

    def run(group: list[str], dimension: str | None) -> None:
        for i in range(0, len(group), BATCH):
            chunk = group[i:i + BATCH]
            try:
                parts = query(chunk, start, end, brand, dimension, log)
            except RuntimeError as e:
                # One unanswerable metric (declared not populated) fails the whole batch: retry one by one.
                if len(chunk) == 1:
                    unavailable[f"{chunk[0]}|{dimension or ''}"] = str(e)[:300]
                    continue
                for m in chunk:
                    run([m], dimension)
                continue
            for part in parts:
                for row in part.get("rows") or []:
                    day = _day(row)
                    if day is None:
                        continue
                    for m in chunk:
                        raw = row.get(m)
                        if raw is None:
                            continue
                        if dimension is None:
                            series.setdefault(m, {})[day] = float(raw)
                        else:
                            seg = str(row.get(dimension) if row.get(dimension) not in (None, "") else "(none)")
                            segments.setdefault(m, {}).setdefault(dimension, {}).setdefault(seg, {})[day] = float(raw)

    for view, group in sorted(by_view.items()):
        run(group, None)
        for dim in dims:
            ok = [m for m in group if dim in (defs.get(m, {}).get("supported_dimensions") or [])]
            if ok:
                run(ok, dim)
    data = {"series": series, "segments": segments, "definitions": {m: defs[m] for m in sorted(defs)}}
    fixture = {
        "schema_version": 1, "id": fixture_id, "brand": brand, "start": start, "end": end,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "graph_sha256": fingerprint(graph), "catalogue_version": catalogue_version, "metrics": metrics, "missing": sorted(set(metrics) - set(series)), "unavailable": unavailable,
        "queries": log, "sha256": fingerprint(data), **data,
    }
    out = os.path.join(HERE, "fixtures", f"{fixture_id}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(fixture, f, separators=(",", ":"), sort_keys=True)
    print(f"{out}: {len(series)}/{len(metrics)} metrics, {sum(len(v) for v in segments.values())} segmented, "
          f"{len(log)} queries, {os.path.getsize(out) / 1e6:.1f} MB, sha256 {fixture['sha256']}")


def extend(fixture_id: str, dims: list[str]) -> None:
    """Add the ratio components the fixture's ratio metrics need but lack, keeping every existing value as it is.

    The first snapshot chose metrics from the graph's causal edges only, so a ratio such as net ROAS (contribution margin /
    ad spend) had a numerator the engine could not fetch. Existing series and segments are not re-fetched, so earlier
    scores stay comparable; only the fingerprint changes (cases are rewritten with the new one).
    """
    path = os.path.join(HERE, "fixtures", f"{fixture_id}.json")
    with open(path) as fh:
        fx = json.load(fh)
    need: set[str] = set()
    for d in fx["definitions"].values():
        rc = d.get("ratio_components") or {}
        need |= {rc[k] for k in ("numerator", "denominator") if rc.get(k) and rc[k] not in fx["series"]}
    need -= set(fx["unavailable"])
    new_defs: dict[str, Any] = {}
    ids = sorted(need)
    for i in range(0, len(ids), DEFINITION_BATCH):
        got = mcp_direct.call_tool("catalogue_get_metrics", {"metric_ids": ids[i:i + DEFINITION_BATCH]})
        if got.get("error"):
            raise RuntimeError(f"catalogue_get_metrics: {got['error']}")
        new_defs.update(got.get("metrics") or {})
    if set(ids) - set(new_defs):
        raise RuntimeError(f"no catalogue definition for {sorted(set(ids) - set(new_defs))}")
    by_view: dict[str, list[str]] = {}
    for m, d in new_defs.items():
        by_view.setdefault(str((d.get("cube_mapping") or {}).get("view") or ""), []).append(m)
    series: dict[str, dict[str, float]] = {}
    segments: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    log: list[dict[str, Any]] = []
    unavailable: dict[str, str] = {}

    def run(group: list[str], dimension: str | None) -> None:
        for chunk in (group[i:i + BATCH] for i in range(0, len(group), BATCH)):
            try:
                parts = query(chunk, fx["start"], fx["end"], fx["brand"], dimension, log)
            except RuntimeError as e:
                if len(chunk) == 1:
                    unavailable[f"{chunk[0]}|{dimension or ''}"] = str(e)[:300]
                    continue
                for m in chunk:
                    run([m], dimension)
                continue
            for part in parts:
                for row in part.get("rows") or []:
                    day = _day(row)
                    for m in chunk:
                        if day is None or row.get(m) is None:
                            continue
                        if dimension is None:
                            series.setdefault(m, {})[day] = float(row[m])
                        else:
                            seg = str(row.get(dimension) if row.get(dimension) not in (None, "") else "(none)")
                            segments.setdefault(m, {}).setdefault(dimension, {}).setdefault(seg, {})[day] = float(row[m])

    for _view, group in sorted(by_view.items()):
        run(group, None)
        for dim in dims:
            ok = [m for m in group if dim in (new_defs[m].get("supported_dimensions") or [])]
            if ok:
                run(ok, dim)
    fx["series"].update(series)
    fx["segments"].update(segments)
    fx["definitions"].update({m: new_defs[m] for m in sorted(new_defs)})
    fx["metrics"] = sorted(set(fx["metrics"]) | set(new_defs))
    fx["queries"] += log
    fx["unavailable"].update(unavailable)
    fx["extended_with"] = sorted(new_defs)
    fx["sha256"] = fingerprint({k: fx[k] for k in ("series", "segments", "definitions")})
    with open(path, "w") as fh:
        json.dump(fx, fh, separators=(",", ":"), sort_keys=True)
    print(f"added {sorted(new_defs)}; unavailable {sorted(unavailable)}; sha256 {fx['sha256']}")


if __name__ == "__main__":
    if sys.argv[1] == "extend":
        extend(sys.argv[2], [d for d in (sys.argv[3] if len(sys.argv) > 3 else "").split(",") if d])
        sys.exit(0)
    main(sys.argv[1], sys.argv[2], sys.argv[3], [d for d in (sys.argv[4] if len(sys.argv) > 4 else "").split(",") if d])
