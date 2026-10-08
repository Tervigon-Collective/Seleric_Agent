import os
import json
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

async def main():
    url = "postgresql+psycopg://seleric:seleric@127.0.0.1:5433/seleric_swarm"
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT * FROM conversations WHERE id = 'thread_b1739390d09340ba9b21f5bbe13df3a0'"))
        for row in res:
            print(dict(row._mapping))
        
        print("\n=== MESSAGES ===")
        res2 = await conn.execute(text("SELECT * FROM events WHERE conversation_id = 'thread_b1739390d09340ba9b21f5bbe13df3a0' ORDER BY id ASC"))
        for row in res2:
            print(row._mapping["event_type"])
            print(row._mapping["payload"])
            
if __name__ == "__main__":
    asyncio.run(main())
