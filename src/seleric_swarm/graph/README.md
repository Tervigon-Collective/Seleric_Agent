# `graph`

## Purpose
One graph of metric relations for diagnosis (M4.3): identity edges (component → composite) from the read model's `composition` / `depends_on`, causal edges (cause → effect, sign, lag) from `config/causal_graph.yaml`.

## Interface
`build_graph(read_model, causal_config) -> MetricGraph` with `components`, `causes`, `parents`, `children`, `ancestors`. Pure: the YAML is parsed by the caller.

## Failure policy
Causal nodes that are not catalogue metric/dimension ids are skipped and listed in `MetricGraph.skipped` (semantic pickup). Malformed edges are ignored.

## Gaps
DoWhy-GCM adapter (`GRAPH_GCM_MODE`) not built.

## Tests
`uv run pytest tests/unit/graph`
