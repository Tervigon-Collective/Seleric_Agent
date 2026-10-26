import asyncio, json
from dotenv import load_dotenv
load_dotenv("/opt/seleric/Seleric_Agent/.env")
from seleric_swarm.protocols.mcp.gateway import MCPGateway
async def main():
    g = MCPGateway("config/mcp_servers.yaml")
    for t in await g.list_tools():
        print(t.get("name"), "|", (t.get("description") or "")[:110].replace("\n"," "))
    print(json.dumps([t for t in await g.list_tools() if t["name"]=="metrics_query"][0].get("inputSchema"))[:3000])
asyncio.run(main())
