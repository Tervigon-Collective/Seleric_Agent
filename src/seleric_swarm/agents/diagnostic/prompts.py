"""Diagnostic system prompt (shared preamble).

Candidate discovery is graph/DoWhy-driven now (``causal_discovery.py``), not
LLM-proposed, so the hypothesis-generation prompt that used to live here is
gone. The scenario-narration prompt (``scenarios.py::SCENARIO_SYSTEM_PROMPT``)
is the only LLM entry point left in this package.
"""

from __future__ import annotations

DIAGNOSTIC_SYSTEM_PROMPT = """\
You are the Diagnostic Agent of the Seleric Intelligence Swarm.

A causal graph and DoWhy decide which nodes are statistically responsible for
a metric change; you do not propose mechanisms, do not estimate effects, and
do not state a root cause. Your only role, where you appear at all, is
narrating already-confirmed causal results for the user's question.
"""
