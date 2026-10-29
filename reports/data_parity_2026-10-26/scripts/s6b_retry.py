import sys, os, json, asyncio; sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv; load_dotenv("/opt/seleric/Seleric_Agent/.env")
from seleric_swarm.protocols.mcp.gateway import MCPGateway
from seleric_swarm.services.mcp_query import build_metrics_query_args
O = os.path.dirname(__file__) + "/out/"; W = {"today": ("2026-10-26", "2026-10-26"), "yesterday": ("2026-10-25", "2026-10-25"), "last7": ("2026-10-19", "2026-10-25"), "last30": ("2026-09-26", "2026-10-25")}
rows = json.load(open(O + "chains.json")); bad = [x for x in rows if x["status"] == "ERROR" and "Continue wait" in str(x["note"])]
print(len(bad), "to retry")
async def main():
    g = MCPGateway("config/mcp_servers.yaml")
    async def q(m, w, axis, dims):
        a, b = W[w]; args = build_metrics_query_args(measure=m, start=a, end=b, dimensions=dims, limit=20000)
        if axis: args["date_axis"] = axis
        for attempt in range(4):
            r = await g.call(agent_id="seleric_agent", capability="seleric.metrics_query", arguments=args)
            if not (isinstance(r, dict) and "still building" in str(r.get("error", ""))): return r
            await asyncio.sleep(20)
        return r
    for x in bad:
        m = x["left"]; d = x["check"].split(" by ")[1].split(" sums")[0]; ax = None if x["axis"] in (None, "-", "") else x["axis"]
        t = await q(m, x["window"], ax, None); r = await q(m, x["window"], ax, [d])
        if t.get("error") or r.get("error"): x["note"] = "retry failed: " + str(t.get("error") or r.get("error"))[:100]; continue
        tv = float(t["rows"][0][m]); sv = sum(float(y.get(m) or 0) for y in r["rows"]); nul = sum(float(y.get(m) or 0) for y in r["rows"] if y.get(d) in (None, "", "(not set)", "unknown"))
        dd = abs(tv - sv); x.update(check=f"{m}: Σ by {d} = total", left=f"total {m}", left_val=tv, right=f"Σ over {len(r['rows'])} {d} groups", right_val=sv, delta=dd, status="PASS" if dd <= max(1, 1e-4 * abs(tv)) else "FAIL", note=f"null/unset share of value = {nul:,.2f}")
        print(x["check"], x["window"], x["axis"], x["status"])
    for x in rows:   # unsupported-dimension errors are coverage gaps, not failures
        if x["status"] == "ERROR" and "is not supported by metric" in str(x["note"]): x["status"] = "INFO"; x["note"] = "GAP: dimension not supported by this metric/view (" + x["note"][4:110] + ")"
    json.dump(rows, open(O + "chains.json", "w"), default=str)
asyncio.run(main())
