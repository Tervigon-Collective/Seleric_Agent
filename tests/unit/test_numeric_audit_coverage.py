"""PRD-005: fail CI if a new LLM-prose call site appears unaccounted for.

Nothing stops a *future* ``llm.complete(...)``/``generate_text(...)`` call
site from feeding free text into a user-facing response without going
through ``services/numeric_audit.py``. This test enumerates every such call
site today and fails if an unrecognized one shows up, forcing whoever adds
it to explicitly classify it here instead of silently skipping the audit.
"""

from __future__ import annotations

import re
from pathlib import Path

_CALL_RE = re.compile(r"\.(?:complete|generate_text)\(")

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "seleric_swarm"

# file (relative to src/seleric_swarm) -> why it's safe without numeric_audit.
_ALLOWLIST: dict[str, str] = {
    # Not prose producers: LLMPort implementations / wrappers themselves,
    # not callers deciding what to do with the text.
    "llm/metering.py": "LLMPort wrapper, forwards whatever the caller sent",
    "llm/adapters/azure_openai_compatible.py": "LLMPort adapter internals (repair loop)",
    "llm/adapters/fake.py": "test fake adapter",
    "agents/prediction/reasoning.py": "LLMPort wrapper behind ReasoningModel protocol",
    "agents/skeptic/reasoning.py": "LLMPort wrapper behind ReasoningModel protocol",
    # Not final_response: dev-only endpoint, gated by is_dev_surface().
    "main.py": "/v1/llm/ping dev-surface echo, never reaches a mission response",
    # Not final_response: offline eval tooling, not the production answer path.
    "eval/llm_judge.py": "offline eval judge, not a mission response path",
    # KNOWN GAP (PRD-005): these write free text into result.audit["narrative"]
    # / verdict.explanation. Neither field is read anywhere else in the
    # codebase today (see coordinator/* — no reference), so nothing reaches
    # the user unaudited yet. If either field is ever wired into
    # final_response, it MUST go through numeric_audit first — do not just
    # widen this allowlist comment.
    "agents/prediction/agent.py": "narrative written to result.audit, not yet read by any caller",
    "agents/skeptic/agent.py": "explanation written to verdict.explanation, not yet read by any caller",
}


def _call_sites() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in _SRC_ROOT.rglob("*.py"):
        rel = path.relative_to(_SRC_ROOT).as_posix()
        lines = path.read_text(encoding="utf-8").splitlines()
        hits = [i + 1 for i, line in enumerate(lines) if _CALL_RE.search(line)]
        if hits:
            found[rel] = hits
    return found


def test_every_llm_prose_call_site_is_classified() -> None:
    unclassified = {rel: lines for rel, lines in _call_sites().items() if rel not in _ALLOWLIST}
    assert not unclassified, (
        "New LLM completion call site(s) found that aren't classified in "
        "tests/unit/test_numeric_audit_coverage.py's _ALLOWLIST: "
        f"{unclassified}. If this text can reach a mission's final_response, "
        "route it through services.numeric_audit.unaudited_numbers before "
        "returning it, then add it to the allowlist as 'audited'. Otherwise "
        "add it with a comment explaining why it's safe."
    )
