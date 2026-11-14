---
name: tdd
description: Test-driven development for this repo. Use when adding a feature or fixing a bug in src/seleric_swarm (pytest) or office-ui (vitest) and the user says "tdd", "test first", or "write a failing test".
---

# TDD

Red, green, refactor. One behaviour at a time.

1. **Red**: write one test for the next behaviour (a bug fix: a test that reproduces the bug). Run it. It must fail for the reason you expect. A test that passes first time proves nothing.
2. **Green**: write the least code that passes. No extras.
3. **Refactor**: tidy with the tests green. Run the whole suite, not just the new test.
4. Repeat for the next behaviour.

## Where tests go

- Python: `tests/unit` (pure logic), `tests/api`, `tests/contract`, `tests/integration` (needs an external service, mark `@pytest.mark.integration`). Shared fixtures in `tests/conftest.py` and `tests/fixtures`.
- UI: `*.test.ts(x)` next to the code in `office-ui/src`.

## Run

- One test: `pytest tests/unit/test_x.py::test_name -x`
- Python suite: `pytest`
- UI: `cd office-ui && npm test`

## Rules

- Test behaviour through the public function or endpoint, not internals.
- Never stash or reset the repo to get a baseline; use a git worktree.
- Don't edit a test to make it pass unless the test itself was wrong. Say so if it was.
- If you can't make the test fail first, stop and find out why.
