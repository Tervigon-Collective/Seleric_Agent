# understand.examples

Verified examples store (M1.3). Retrieves verified
`(question → QuestionSpec → template_id)` examples as understand exemplars and
marks trusted plans.

## Public interface

- `ExampleStore(backend, *, port=None, on_stale=None)`
- `ExampleStore.similar(question, k=3, *, read_model=None) -> list[VerifiedExample]`
- `VerifiedExample` (frozen model: `id, question, spec, template_id, verified_by, verified_at, catalogue_version`)
- `ExampleBackend` (Protocol), `InMemoryExampleBackend`

Import only from the package root: `from seleric_swarm.understand.examples import ExampleStore`.

## Behaviour

`similar` asks the backend for the `k * 2` nearest examples, then re-validates
each against the current catalogue read model (`port.read_model()`, or an injected
`read_model`). An example whose metric or dimension ids no longer exist is
**skipped and reported** via `on_stale` — the semantic pickup rule, so a renamed
catalogue id never reaches a prompt as if it were still valid.

## Backends

- `InMemoryExampleBackend` — token-overlap similarity; tests and local dev.
- `adapters/examples_qdrant.py::QdrantExampleBackend` — Qdrant vector store, reusing
  the `toolsets/catalogue_index.py` client/collection/embedding patterns.

## Seed

`eval/verified/seed.yaml` is generated from the M0.4 golden cases by
`scripts/seed_verified_examples.py`. Ids carry a `verified-` prefix so they never
collide with an eval case id (the eval runner fails on a collision — eval cases
and examples stay separate).

## Flag

`settings.verified_examples_mode: Literal["off", "shadow", "enforce"] = "off"` —
read by the understand stage (M1.1) to decide whether to inject examples.

## Failure policy

Retrieval never raises on a stale example — it is skipped and reported. A backend
failure propagates to the caller (the understand stage's failure policy applies).

## Test command

```
uv run pytest tests/unit/understand/examples/test_store.py -q
```
