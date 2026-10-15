"""Leaf-level snapshot for the RiskLoc decision test (plan P0.9).

usage: python3 leaf_snapshot.py <fixture_id> <start> <end> <measure> <dim,dim,...>

One query by every given dimension at once, daily: each row is a leaf (one value per dimension). Written to
eval/diag/fixtures/<fixture_id>.json with the source query and a sha256 over the cells.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "regress"))
import mcp_direct
from world_fixture import fingerprint


def main(fixture_id: str, start: str, end: str, measure: str, dims: list[str]) -> None:
    args = {"measures": [measure], "time_range": {"start": start, "end": end}, "granularity": "day", "dimensions": dims,
            "filters": [{"dimension": "brand_id", "operator": "equals", "values": [20]}], "limit": 50000}
    res = mcp_direct.call_tool("metrics_query", args)
    if res.get("error"):
        raise RuntimeError(res["error"])
    cells = []
    for row in res["rows"]:
        day = next(str(v)[:10] for k, v in row.items() if k.endswith(".day"))
        cells.append({"day": day, "leaf": [str(row.get(d) if row.get(d) not in (None, "") else "(none)") for d in dims], "value": float(row[measure])})
    if len(cells) >= args["limit"]:
        raise RuntimeError("result hit the row limit; the leaf set is truncated")
    data = {"measure": measure, "dimensions": dims, "cells": cells}
    out = {"schema_version": 1, "id": fixture_id, "start": start, "end": end, "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
           "query": {"arguments": args, "query_id": res.get("query_id")}, "sha256": fingerprint(data), **data}
    path = os.path.join(HERE, "fixtures", f"{fixture_id}.json")
    with open(path, "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    print(f"{path}: {len(cells)} cells, {len({tuple(c['leaf']) for c in cells})} leaves, sha256 {out['sha256']}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5].split(","))
