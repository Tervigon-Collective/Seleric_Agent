import asyncio
import os
from seleric_swarm.bootstrap import build_runtime
from seleric_swarm.config.settings import Settings

async def test():
    settings = Settings()
    runtime = build_runtime(settings)
    
    # Try calling metrics_query directly
    try:
        result = await runtime.mcp.call(
            agent_id="test",
            capability="seleric.metrics_query",
            arguments={
                "measures": ["metric.net_sales"],
                "time_range": {"start": "2026-09-29", "end": "2026-10-05"},
                "granularity": "day",
                "filters": [{"dimension": "brand_id", "operator": "equals", "values": ["20"]}]
            }
        )
        print("Success:")
        print(result)
    except Exception as e:
        print(f"Exception: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(test())
