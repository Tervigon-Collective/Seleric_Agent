# `investigate`

## Purpose
A deep "why" as a code state machine: premise → health → decompose → localise ⇄ recurse → context → drivers → stop. Numbers come only from the injected `MetricReader`; an optional `Selector` (LLM) picks among typed candidates and never edits numbers.

## Interface
`Investigation(reader, read_model, graph, providers=, health_sources=, selector=, policy=).run(request, state=None) -> InvestigationState`
- `premise`: event vs baseline; not material or opposite to the asked direction → `premise_false`.
- `health`: `context` health sources; `incomplete` on the metric → `unhealthy`; other issues → limitation.
- `decompose`: identity edges from `graph` (additive terms: coef·Δ; ratio: log-share of Δln r).
- `localise`/`recurse`: per carried dimension, the top same-direction segment; drill while share ≥ `recurse_share` and depth < `max_depth`; additive metrics only.
- `context`: calendar/incident events in the event window.
- `drivers`: graph causes; `supported_cause` only when the cause moved materially in its signed direction.
- Passing a finished state back resumes one level below its last segment ("dig deeper").

## Flag
`INVESTIGATE_MODE` (`off|shadow|enforce`). Not wired yet (pipeline adapts the semantic port to `MetricReader`, `EngineStep(engine="investigate")`).

## Failure policy
Never raises: read budget → `budget` + limitation; any error → `done` + `investigation_error:<type>`.

## Tests
`uv run pytest tests/unit/investigate`
