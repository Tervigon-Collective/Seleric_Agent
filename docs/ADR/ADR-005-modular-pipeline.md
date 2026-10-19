# ADR-005 - Modular pipeline

## Status

Accepted (2026-10-11)

## Decision

New agent behaviour lands in the layered packages in `plans/REFACTOR_PLAN_2026-10-10.md`, behind an `off | shadow | enforce` flag. A lower layer never imports a higher one. Domain packages (`understand`, `spec`, `plan`, `derive`, `answer`, `investigate`, `context`, `graph`) do not import `re`, `httpx`, `openai`, `pydantic_ai`, or `seleric_swarm.adapters`. `derive`, `answer`, `context`, and `graph` do not import each other. A package's `_` modules are imported only from inside that package.

`import-linter` enforces this in CI (`uv run lint-imports`) on every push. Legacy packages (`agent`, `toolsets`, and the rest) stay outside the layer contract until their code moves. The semantic-literal scanner is the other half of the gate.

`contracts-v1` is frozen. A breaking change to `seleric_swarm.contracts.pipeline` needs a new ADR and a new `schema_version`.

## Consequence

A pull request that reaches into a private module, skips a layer, or decides intent with a regular expression in a domain package fails CI. Existing code keeps running until each module is switched to `enforce` and the legacy path is deleted.
