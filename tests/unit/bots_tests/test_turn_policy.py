"""D-247 P0 loop defects: bot budget, per-run thread, no email tools for bots, scratchpad shown to the model."""
from __future__ import annotations

from pydantic_ai.usage import RunUsage

from seleric_swarm.agent.local_tools import ANALYSIS_TOOLS, build_local_tools
from seleric_swarm.agent.loop import TurnPolicy, _Budget, _instructions
from seleric_swarm.agent.mcp_toolset import TurnState
from seleric_swarm.services.bots import worker
from seleric_swarm.state.scratchpad import Scratchpad

MAIL = {"draft_mom_email", "draft_ad_hoc_email", "send_email"}
CHAT_ONLY = {"search_brand_documents"}  # D-260: bots recall the same banks in their brief


def _names(tools):
    return {t.__name__ for t in tools}


def test_analysis_allowlist_covers_every_non_email_tool():
    every = _names(build_local_tools(TurnState()))
    assert every - MAIL - CHAT_ONLY == ANALYSIS_TOOLS  # a new local tool must be classed here, or bots silently lose it
    assert _names(build_local_tools(TurnState(), only=ANALYSIS_TOOLS)) == ANALYSIS_TOOLS


def test_bot_policy_uses_its_own_limit_and_drops_email():
    p = worker.policy({"limits": {"max_tool_calls": 40}})
    assert p.max_tool_calls == 40 and p.max_steps >= 20 and not (p.local_tools & MAIL)


def test_budget_follows_the_policy():
    state = TurnState()
    state.tool_calls = 25
    assert _Budget(state, is_cancelled=lambda: False, started=1e18).spent(RunUsage()) is True  # chat default 20
    assert _Budget(state, is_cancelled=lambda: False, started=1e18,
                   policy=TurnPolicy(max_tool_calls=40, max_steps=20)).spent(RunUsage()) is False


def test_standing_runs_get_their_own_thread_chat_keeps_one():
    bot = {"bot_id": "b1"}
    assert worker.thread_of(bot, {"trigger_kind": "chat", "run_id": "r1"}) == "bot-b1"
    a = worker.thread_of(bot, {"trigger_kind": "cron", "run_id": "r1"})
    b = worker.thread_of(bot, {"trigger_kind": "cron", "run_id": "r2"})
    assert a != b != "bot-b1"


def test_scratchpad_notes_reach_the_prompt():
    class Deps:
        scratchpad = Scratchpad()

    class Ctx:
        deps = Deps()

    Deps.scratchpad.note("diagnose_metric_change(spend) → localised: campaign X")
    blocks = _instructions("sys", [], [], _Budget(TurnState(), is_cancelled=lambda: False, started=0))
    rendered = [b(Ctx()) for b in blocks if callable(b)]
    assert any("campaign X" in r for r in rendered)
