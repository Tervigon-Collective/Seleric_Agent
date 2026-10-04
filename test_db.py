import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def main():
    engine = create_async_engine("postgresql+psycopg://seleric:seleric@127.0.0.1:5433/seleric_swarm")
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT count(*) FROM runs"))
        print(result.scalar())

asyncio.run(main())
