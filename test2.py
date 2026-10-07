import asyncio
from seleric_swarm.bootstrap import build_runtime
from seleric_swarm.config.settings import Settings

async def test():
    settings = Settings()
    runtime = build_runtime(settings)
    
    definition = runtime.metrics.get("metric.net_sales")
    if definition:
        print(f"Metric ID: {definition.id}")
        print(f"Catalogue Metric: {definition.catalogue_metric}")
    else:
        print("Metric metric.net_sales not found")
        
if __name__ == "__main__":
    asyncio.run(test())
