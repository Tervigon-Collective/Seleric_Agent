import asyncio
from seleric_swarm.bootstrap import build_runtime
from seleric_swarm.config.settings import Settings
from seleric_swarm.services.business_state.facade import BusinessStateService
from seleric_swarm.domain.models import StateRequest
from seleric_swarm.contracts.lookup import TimeRangeV1

async def test():
    settings = Settings()
    runtime = build_runtime(settings)
    bss = BusinessStateService(runtime)
    
    req = StateRequest(
        metric_id="metric.net_sales",
        time_range=TimeRangeV1(kind="relative", relative_token="last_7d"),
        dimensions={"brand_id": "20"},
        agent_id="commerce_agent",
        need=["actual"]
    )
    state = await bss.get_metric_state(req)
    print(f"Status: {state.status}")
    print(f"Quality flags: {state.quality_flags}")
    print(f"Provenance: {state.provenance}")
    print(f"Error: {state.provenance.get('error')}")

if __name__ == "__main__":
    asyncio.run(test())
