"""Size of what every loop step sends before the question: system prompt parts, MCP tool schemas, local tool schemas.

Run inside the recovery container (it needs the runtime and the live Core MCP):
    docker exec seleric_agent-recovery-1 python /app/scripts/prompt_budget.py [out_dir]
Writes the system prompt and the tool schemas as sent to out_dir (default /app/.data/prompt_budget), prints sizes.
"""
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from seleric_swarm.agent import prompt as P
from seleric_swarm.agent.local_tools import build_local_tools
from seleric_swarm.agent.mcp_toolset import TurnState, build_mcp_tools
from seleric_swarm.agent.prompt_slim import slim_defs
from seleric_swarm.agent.runner import _catalogue_snapshot, _server_guide, _tool_definitions
from seleric_swarm.bootstrap import build_runtime


async def main(out: Path) -> None:
    runtime = build_runtime()
    mcp = runtime.mcp
    catalogue, defs, guide = await asyncio.gather(_catalogue_snapshot(runtime), _tool_definitions(mcp), _server_guide(mcp))
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    sp, ts = await measure(mcp, catalogue, defs, guide, now, out)
    print(f"system prompt {sp} chars, tool schemas {ts} chars, total {sp + ts} chars (~{(sp + ts) // 4} tokens by chars/4)")


async def measure(mcp, catalogue, defs, guide, now, out):  # type: ignore[no-untyped-def]
    full = P.system_prompt(catalogue, now, guide)
    seen: dict[str, str] = {}

    def capture(messages, info: AgentInfo):  # type: ignore[no-untyped-def]
        shown = slim_defs(list(info.function_tools))
        seen["tools"] = json.dumps([{"name": t.name, "description": t.description, "parameters": t.parameters_json_schema}
                                    for t in shown], sort_keys=True)
        seen["mcp"] = json.dumps([{"name": t.name, "description": t.description, "parameters": t.parameters_json_schema}
                                  for t in info.function_tools if t.name in {d["name"] for d in defs}], sort_keys=True)
        seen["local"] = json.dumps([{"name": t.name, "description": t.description, "parameters": t.parameters_json_schema}
                                    for t in info.function_tools if t.name not in {d["name"] for d in defs}], sort_keys=True)
        return ModelResponse(parts=[TextPart("ok")])

    state = TurnState()
    agent = Agent(FunctionModel(capture), tools=[*build_mcp_tools(mcp, defs, state), *build_local_tools(state)], output_type=str)
    await agent.run("x", deps=None)
    out.mkdir(parents=True, exist_ok=True)
    (out / "system_prompt_full.txt").write_text(full)
    (out / "tools.json").write_text(seen["tools"])
    return len(full), len(seen["tools"])


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "/app/.data/prompt_budget")))
