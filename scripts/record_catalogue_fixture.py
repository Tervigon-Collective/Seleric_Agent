"""Write tests/fixtures/catalogue/snapshot.json from the live catalogue bootstrap.

Usage: uv run python scripts/record_catalogue_fixture.py
"""

from __future__ import annotations

import asyncio
import json
import sys

from seleric_swarm.paths import repo_root


async def _record() -> int:
    from seleric_swarm.bootstrap import build_runtime

    runtime = build_runtime()
    bootstrap = runtime.bootstrap
    if bootstrap is None:
        print("no catalogue bootstrap on the runtime", file=sys.stderr)
        return 2
    count = await bootstrap.warm()
    if not count:
        print("catalogue warm returned no metrics", file=sys.stderr)
        return 2
    port = bootstrap._semantic()
    await port.warm()
    model = port.read_model()
    if not model.metrics:
        print("semantic port snapshot is empty", file=sys.stderr)
        return 2
    payload = {
        "catalogue_version": model.version,
        "default_brand_id": model.default_brand_id,
        "scope_dimensions": list(model.scope_dimension_ids),
        "grain_defaults": model.grain_defaults,
        "hierarchies": {
            item.id: {"levels": list(item.levels), "views": list(item.views), **item.extra}
            for item in model.hierarchies
        },
        "views": [
            {"id": item.id, "freshness": item.freshness, **item.extra} for item in model.views
        ],
        "brands": [
            {"id": item.id, "name": item.name, "code": item.code, **item.extra} for item in model.brands
        ],
        "metrics": [
            {
                "id": metric.id,
                "label": metric.label,
                "view": metric.view,
                "unit": metric.unit,
                "aggregation": metric.aggregation,
                "supported_dimensions": list(metric.supported_dimensions),
                "date_basis": metric.date_basis,
                "date_twin": metric.date_twin,
                "grain_twins": list(metric.grain_twins),
                "volume_metric": metric.volume_metric,
                "composition": metric.composition,
                "split_by": metric.split_by,
                "valid_for": metric.valid_for,
                "excluded_dimensions": metric.excluded_dimensions,
                "currency": metric.currency,
                **metric.extra,
            }
            for metric in model.metrics
        ],
        "dimensions": [
            {
                "id": dim.id,
                "is_time": dim.is_time,
                "family": dim.family,
                "family_rank": dim.family_rank,
                "stable_key": dim.stable_key,
                "hierarchy": dim.hierarchy,
                "allowed_values": list(dim.allowed_values),
                **dim.extra,
            }
            for dim in model.dimensions
        ],
    }
    target = repo_root() / "tests" / "fixtures" / "catalogue" / "snapshot.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {target} ({len(model.metrics)} metrics, version {model.version or 'unknown'})")
    return 0


def main() -> None:
    try:
        code = asyncio.run(_record())
    except Exception as exc:
        print(f"record failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
