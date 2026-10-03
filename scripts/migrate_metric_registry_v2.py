#!/usr/bin/env python3
"""Semantic v2: rewrite config/metric_registry.yaml onto the v2 catalogue (Seleric_Agent_Core catalogue_v2).

  python scripts/migrate_metric_registry_v2.py --core /path/to/Seleric_Agent_Core [--write]

Per entry:
  catalogue_metric   v1 id -> v2 id (catalogue/migrations/v2_id_map.yaml); hard cut, no v1 ids remain
  catalogue_filters  the filters the v2 id needs for the same number (e.g. meta_spend -> ad_spend
                     with ad_platform = meta); the agent sends them with every query
  aliases            kept ONLY when the MCP v2 resolver answers the alias with this entry's metric and
                     filters — the registry can no longer disagree with the catalogue (one resolver)
Prints every change; --write rewrites the YAML in place (comments outside entries are not preserved).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "metric_registry.yaml"
# v1 registry ids that never existed in the v1 catalogue (meta ad-attribution, pre-serve names):
# their v2 meaning is Meta-attributed paid orders / revenue.
LEGACY = {
    "meta_attr_orders": ("orders", {"finance_channel": "meta", "is_paid": "true"}),
    "meta_attr_net_revenue": ("net_sales", {"finance_channel": "meta", "is_paid": "true"}),
    "meta_attr_gross_revenue": ("order_value_incl_tax", {"finance_channel": "meta", "is_paid": "true"}),
    "meta_attr_aov": ("net_aov", {"finance_channel": "meta", "is_paid": "true"}),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", required=True, help="Seleric_Agent_Core checkout with catalogue_v2/")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    core = Path(args.core)
    sys.path.insert(0, str(core / "src"))
    from seleric_mcp.catalogue_service.loader import load_catalogue
    from seleric_mcp.catalogue_service.service import CatalogueService, ResolvedTerm

    svc = CatalogueService(load_catalogue(core / "catalogue_v2"))
    idmap = yaml.safe_load((core / "catalogue" / "migrations" / "v2_id_map.yaml").read_text())
    old = {m["old"]: (m["new"], {k.split(".", 1)[1]: v for k, v in (m.get("filters") or {}).items()})
           for m in idmap["maps"]}
    doc = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    changed, dropped_aliases, unmapped = 0, [], []
    for e in doc["metrics"]:
        cm = e.get("catalogue_metric")
        if not cm:
            continue
        if cm in svc.cat.metrics and cm not in old:
            new, flt = cm, {}
        elif cm in old:
            new, flt = old[cm]
        elif cm in LEGACY:
            new, flt = LEGACY[cm]
        else:
            unmapped.append((e["id"], cm))
            continue
        if new != cm or flt:
            changed += 1
            print(f"  {e['id']:44s} {cm} -> {new}" + (f"  filters {flt}" if flt else ""))
        e["catalogue_metric"] = new
        if flt:
            e["catalogue_filters"] = flt
        else:
            e.pop("catalogue_filters", None)
        kept = []
        for a in e.get("aliases") or []:
            r = svc.resolve_term(str(a))
            if isinstance(r, ResolvedTerm) and r.metric_id == new and dict(r.filter) == flt:
                kept.append(a)
            else:
                got = f"{r.metric_id} {dict(r.filter) or ''}".strip() if isinstance(r, ResolvedTerm) else r.kind
                dropped_aliases.append((e["id"], a, got))
        if e.get("aliases") is not None:
            e["aliases"] = kept
    print(f"{changed} entries remapped; {len(dropped_aliases)} aliases dropped (the v2 resolver answers them "
          f"differently — the catalogue answer wins); unmapped: {unmapped or 'none'}")
    for eid, a, got in dropped_aliases:
        print(f"    alias {a!r} on {eid}: resolver says {got}")
    for e in doc["metrics"]:
        if e.get("catalogue_metric") and e["catalogue_metric"] not in svc.cat.metrics:
            raise SystemExit(f"{e['id']}: catalogue_metric {e['catalogue_metric']} not in catalogue_v2")
    if args.write:
        # Line edits only (catalogue_metric value, catalogue_filters line, alias list) so the
        # file's comments and layout survive review.
        by_id = {e["id"]: e for e in doc["metrics"]}

        def scalar(v: str) -> str:
            return f'"{v}"' if v.lower() in ("true", "false", "yes", "no", "on", "off") or v.isdigit() else v

        out, cur = [], None
        for line in REGISTRY.read_text(encoding="utf-8").splitlines(keepends=True):
            m = re.match(r"^(\s*)- id:\s*(\S+)", line)
            if m:
                cur = by_id.get(m.group(2))
                out.append(line)
                continue
            if cur is not None and re.match(r"^\s*catalogue_filters:", line):
                continue  # re-emitted after catalogue_metric
            m = re.match(r"^(\s*)catalogue_metric:\s*\S+\s*$", line)
            if m and cur is not None and cur.get("catalogue_metric"):
                out.append(f"{m.group(1)}catalogue_metric: {cur['catalogue_metric']}\n")
                if cur.get("catalogue_filters"):
                    flt = ", ".join(f"{k}: {scalar(str(v))}" for k, v in cur["catalogue_filters"].items())
                    out.append(f"{m.group(1)}catalogue_filters: {{{flt}}}\n")
                continue
            m = re.match(r"^(\s*)aliases:\s*\[.*\]\s*$", line)
            if m and cur is not None:
                out.append(f"{m.group(1)}aliases: [{', '.join(cur.get('aliases') or [])}]\n")
                continue
            out.append(line)
        header = "# Semantic v2: catalogue_metric = catalogue_v2 ids (+ catalogue_filters); aliases agree with the MCP resolver.\n"
        text = "".join(out)
        if not text.startswith(header):
            text = header + text
        REGISTRY.write_text(text, encoding="utf-8")
        check = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
        assert [e.get("catalogue_metric") for e in check["metrics"]] == [e.get("catalogue_metric") for e in doc["metrics"]]
        print(f"wrote {REGISTRY}")
    return 1 if unmapped else 0


if __name__ == "__main__":
    sys.exit(main())
