# semantic

One entry point for catalogue knowledge. A catalogue change is picked up by re-warming this port. Agent code does not gain a new metric id, dimension id, or view name.

## Interface

```python
from seleric_swarm.semantic import (
    FakeSemanticPort,
    McpSemanticPort,
    MetricsQueryArgs,
    SemanticPort,
    SemanticReadModel,
)
```

`__all__` is those four plus the values that cross the port: `CatalogueError`, `ConceptResolution`, `MetricDefinition`, `MetricsQueryArgs`, `QueryResponse`, `ValueCandidates`.

`SemanticPort`:

| Method | Returns |
|---|---|
| `version()` | `catalogue_version`, or `""` |
| `read_model()` | frozen `SemanticReadModel` |
| `resolve_concept(text, axes)` | `ConceptResolution` |
| `resolve_values(text, axes_only=False)` | `ValueCandidates` |
| `metric_definitions(ids)` | `dict[str, MetricDefinition]`, batched at 10 |
| `query(args)` | `QueryResponse` |
| `drill(args)` | `QueryResponse` |

`MetricsQueryArgs`, `ConceptResolution`, `ValueCandidates`, `MetricDefinition`, `QueryResponse`, and `CatalogueError` are exported so callers can build arguments and read results without importing a private module.

Unknown catalogue fields are kept on `extra`. Missing optional fields are `None`.

## Flag

`SEMANTIC_PORT_MODE`: `off` | `shadow` | `enforce`. Default `off`.

- `off` — `CatalogueBootstrap` warms as before, then binds the metric registry.
- `shadow` — the port warms beside that path and logs an id-set disagreement. The legacy snapshot is what missions read.
- `enforce` — the bootstrap cache is the port's read model.

Catalogue capability names live in `_mcp_port.py`. Callers use `McpSemanticPort` (`search_metrics`, `resolve_brand`, `fetch_metric`, `fetch_metrics`, `resolve_concept_payload`, `resolve_values_payload`, `ontology`, `related_metrics`, `pull`, `listing`). Those methods return the gateway payload and raise on a failed call, so existing tools keep their error handling. `SEMANTIC_PORT_MODE` still decides whether a mission reads the port's snapshot.

## Failure policy

A failed warm serves the last good snapshot. With none, `query` and `drill` return `CatalogueError(code="CATALOGUE_UNAVAILABLE")`. A metric id absent from a warm snapshot returns `METRIC_NOT_FOUND`. The port does not guess a replacement id.

Refresh: each `ensure_fresh` peeks `catalogue_version` (via `catalogue_list_brands` until Core has a version-only call) and re-warms when it changes. The TTL remains the fallback. `note_version` marks the snapshot stale when any other response carries a new version. `read_model()` is frozen, so a mission keeps the snapshot it was given.

## Redirect rank

`metrics_supporting_dimension` prefers a grain twin, then the same `unit`, then the same `aggregation`, then the most shared supported dimensions, then `extra.redirect_rank` if Core sends it.

## Tenant scope

The tenant dimension is `exploration.space.scope_dimensions(read_model)` when the snapshot declares `scope_dimensions` (or a dimension with `extra.role = "scope"`). Otherwise `config/scope.yaml` supplies the ids. `settings.default_brand_id` overrides the default brand in that file.

## Tests

```bash
uv run pytest tests/unit/semantic tests/contract/test_semantic_port.py tests/contract/test_semantic_pickup.py -q
```

Record a live fixture (optional; tests ship with a schema sample):

```bash
uv run python scripts/record_catalogue_fixture.py
```
