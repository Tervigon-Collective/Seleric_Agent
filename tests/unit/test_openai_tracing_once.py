"""Langfuse OpenAI tracing is registered once per process (2026-10-06).

Each AzureOpenAICompatible client called ``register_tracing()``, which wraps the
OpenAI SDK again every time: one LLM call was traced as 5-13 identical
generations, growing with process age.
"""

from __future__ import annotations

import pytest

pytest.importorskip("langfuse.openai")


def _wrapper_depth() -> int:
    import openai.resources.chat.completions as mod

    fn = mod.AsyncCompletions.create
    depth = 0
    while hasattr(fn, "__wrapped__"):
        depth += 1
        fn = fn.__wrapped__
    return depth


def test_repeated_tracing_setup_does_not_stack_wrappers() -> None:
    from seleric_swarm.observability.tracing import ensure_openai_tracing

    assert ensure_openai_tracing() is True
    before = _wrapper_depth()
    for _ in range(5):
        ensure_openai_tracing()
    assert _wrapper_depth() == before
