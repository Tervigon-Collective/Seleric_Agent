# observability

Stage-level tracing and replayability (M0.3). Every pipeline stage runs inside
`stage_span`, which emits one OTel span + Langfuse observation per stage and
persists a redacted, replayable record of the stage input/output.

## Public interface

Exactly two entry points (A9):

- `stage_span(stage, *, mission_id, inp=None, out_setter=None, enabled=True, io_store=None, trace_store=None) -> ContextManager[StageSpanHandle]`
- `configure_tracing(settings) -> bool`

Import only from the package root: `from seleric_swarm.observability import stage_span`.

## What `stage_span` does

For a stage named `<stage>` it:

1. Opens an OTel span `seleric.stage.<stage>` with attributes
   `seleric.mission_id`, `seleric.stage`, `seleric.stage.input_fingerprint`,
   and — for LLM stages (`understand`, `answer`, `draft`, `select`, `clarify`) —
   `gen_ai.operation.name`. On `handle.set_output(...)` it adds
   `seleric.stage.output_fingerprint`; `handle.set_gen_ai(model=..., input_tokens=..., output_tokens=...)`
   adds the `gen_ai.request.model` / `gen_ai.response.model` / `gen_ai.usage.*`
   attributes. The GenAI semconv version is pinned in `GENAI_SEMCONV_VERSION`.
2. Persists the full redacted input/output JSON to a Langfuse observation
   (no-op when Langfuse is unconfigured or `enabled=False`).
3. Writes a compact record to the mission result's `trace["stages"][<stage>]`
   (via `StageTraceStore`) and a redacted JSON file to the local IO store
   (`settings.trace_local_dir`) so the mission can be replayed without Langfuse.

Fingerprints are `sha256` of the canonical JSON dump
(`contracts.pipeline.fingerprint`).

## Usage

```python
from seleric_swarm.observability import stage_span

with stage_span("understand", mission_id=mid, inp=draft_input) as span:
    draft = run_understand(draft_input)
    span.set_output(draft)          # output fingerprint + Langfuse output + trace record
# on an LLM stage, after the model call:
#   span.set_gen_ai(model="gpt-5-mini", input_tokens=120, output_tokens=80)
```

`out_setter=` is invoked with the output model so callers can capture it without
a separate variable. `io_store=` / `trace_store=` are injectable for tests.

`llm_call_span(name, model=..., mission_id=...)` wraps a raw pydantic-ai model
call in an OTel span with `gen_ai.*` attributes; it nests under the enclosing
stage span.

## Flag

`settings.stage_tracing_mode: Literal["off", "shadow", "enforce"] = "off"` —
read by the pipeline (M1.2+) to decide whether to wrap stages in `stage_span`.
`stage_span` itself always traces when called; the flag gates the caller.
`settings.trace_backend` (`none|otel|langfuse|both`, default `both`) controls
which export backends `configure_tracing` activates.

## Failure policy

Observability never aborts a mission. Every OTel/Langfuse/IO call is guarded; a
failure degrades to a no-op. If the stage body raises, the error is recorded in
`trace["stages"][<stage>].error` and the IO store, then re-raised.

## Test command

```
uv run pytest tests/unit/observability/test_stages.py -q
```
