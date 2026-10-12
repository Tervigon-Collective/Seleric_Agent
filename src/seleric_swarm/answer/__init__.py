"""Answers by construction (Phase 3): the model drafts claims and blocks, code renders numbers,
the verifier checks structure against the spec and evidence, and the turn summary is typed."""

from seleric_swarm.answer._assemble import ReportSkeleton, assemble
from seleric_swarm.answer._draft_call import INSTRUCTIONS, AnswerLLM, answer, evidence_prompt
from seleric_swarm.answer._evidence import Evidence, Resolved, resolve_claim
from seleric_swarm.answer._format import format_value
from seleric_swarm.answer._render import render
from seleric_swarm.answer._turn import TurnSummary, turn_summary
from seleric_swarm.answer._verify import Issue, Verdict, verify

__all__ = [
    "INSTRUCTIONS",
    "AnswerLLM",
    "Evidence",
    "Issue",
    "ReportSkeleton",
    "Resolved",
    "TurnSummary",
    "Verdict",
    "answer",
    "assemble",
    "evidence_prompt",
    "format_value",
    "render",
    "resolve_claim",
    "turn_summary",
    "verify",
]
