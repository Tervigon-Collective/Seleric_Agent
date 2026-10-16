"""Write the lean reviewed priors the diagnosis engine reads (plan D-55): mechanism and feedback edges, pairs of metrics
that are the same quantity, and the funnel tiers of the metrics on a mechanism edge.

The input is Agent Core's generated graph (read-only here; Core is never edited from this repo). Arithmetic edges
(composition, ratio, declared input) are NOT copied: the engine reads them from the live catalogue definitions. Twin
pairs of product-level metrics are not copied either (product metrics are not causal nodes: their answer is the total,
split); their allocation and subset pairs are kept so a product outcome is never explained by its own total.

    python3 scripts/vendor_priors.py [path/to/dependency_graph.json]
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

CORE_GRAPH = Path("/opt/seleric/Seleric_Agent_Core/doc/dependency_graph/dependency_graph.json")
OUT = Path(__file__).resolve().parents[1] / "src" / "seleric_swarm" / "diagnosis" / "data" / "priors.json"
PRODUCT_VIEWS = {"product", "product_pnl"}
PAIR_KINDS = ("co_measure", "subset", "allocation", "twin")


def build(raw: dict, source_sha: str) -> dict:
    nodes = {n["id"]: n for n in raw["nodes"]}
    causal = [e for e in raw["edges"] if e["kind"] in ("mechanism", "feedback")]
    on_mechanism = sorted({m for e in causal for m in (e["from"], e["to"])})
    pairs = []
    for e in raw["edges"]:
        if e["kind"] not in PAIR_KINDS:
            continue
        if e["kind"] == "twin" and any(nodes[m]["view"] in PRODUCT_VIEWS for m in (e["from"], e["to"])):
            continue
        pairs.append({"a": e["from"], "b": e["to"], "kind": e["kind"], "expected": e.get("expected", "")})
    return {
        "version": 1,
        "source": {"file": "doc/dependency_graph/dependency_graph.json", "sha256": source_sha, "graph_digest": raw.get("generated_by", "")},
        "tiers": {str(k): v["name"] if isinstance(v, dict) else v for k, v in raw["tiers"].items()},
        "metric_tiers": {m: nodes[m]["tier"] for m in on_mechanism},
        "causal": [
            {
                "source": e["from"], "target": e["to"], "kind": e["kind"], "confidence": e["confidence"],
                "sign": e.get("sign", "+/-"), "lag_days": e.get("lag_days", [0, 0]),
            }
            for e in sorted(causal, key=lambda e: (e["from"], e["to"]))
        ],
        "pairs": sorted(pairs, key=lambda p: (p["kind"], p["a"], p["b"])),
    }


def main(argv: list[str]) -> int:
    src = Path(argv[0]) if argv else CORE_GRAPH
    text = src.read_text(encoding="utf-8")
    priors = build(json.loads(text), hashlib.sha256(text.encode()).hexdigest())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(priors, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    kinds = {k: sum(1 for p in priors["pairs"] if p["kind"] == k) for k in PAIR_KINDS}
    print(f"{OUT}: {len(priors['causal'])} causal edges, pairs {kinds}, {len(priors['metric_tiers'])} tiered metrics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
