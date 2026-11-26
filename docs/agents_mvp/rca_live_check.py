import asyncio, json, sys
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from seleric_swarm.bootstrap import build_runtime
from seleric_swarm.config.settings import Settings
from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.agent.runner import _catalogue_snapshot, _principal
from seleric_swarm.api.v3_state import get_v3_artifact_store
from seleric_swarm.toolsets.diagnosis import diagnose_metric_change

IST = ZoneInfo("Asia/Kolkata")
CASES = json.loads(sys.argv[1])

async def main():
    rt = build_runtime(Settings())
    cat = await _catalogue_snapshot(rt)
    out = []
    for c in CASES:
        deps = SelericDeps(mission_id="rca-check", as_of=datetime(2026, 11, 26, 12, tzinfo=IST),
                           principal=_principal(workspace_id="default", user_id="rca-check"), thread_id="rca-check",
                           run_id="rca-check", trace_id="rca-check", context={}, mcp_client=rt.mcp,
                           artifact_store=get_v3_artifact_store(), limits=ExecutionLimits(max_runtime_seconds=300,
                           mcp_call_timeout_s=60, agent_retries=1), catalogue=cat, metrics=getattr(rt, "metrics", None))
        ctx = SimpleNamespace(deps=deps)
        d = lambda s: datetime.fromisoformat(s).replace(tzinfo=IST)
        kw = {k: (d(v) if k.endswith(("_start", "_end")) else v) for k, v in c.items() if k != "name"}
        r = await diagnose_metric_change(ctx, **kw)
        rep = (r.provenance.source_metadata or {}).get("diagnosis") if getattr(r, "provenance", None) else None
        out.append({"name": c["name"], "success": r.success, "summary": r.summary, "report": rep})
    print(json.dumps(out, default=str))

asyncio.run(main())
