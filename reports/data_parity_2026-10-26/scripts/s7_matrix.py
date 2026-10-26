import sys, os, json, asyncio, collections
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv; load_dotenv("/opt/seleric/Seleric_Agent/.env")
from seleric_swarm.protocols.mcp.gateway import MCPGateway
from seleric_swarm.services.mcp_query import build_metrics_query_args
O = os.path.dirname(__file__) + "/out/"
ms = json.load(open(O + "list_metrics.json")); CAT = {m["id"]: m for m in json.load(open(O + "catalogue_metrics.json"))}
KEYDIMS = ["brand_id", "ad_platform", "campaign_id", "adset_id", "ad_id", "finance_channel", "sub_channel", "is_paid", "is_new_customer", "payment_bucket", "shipping_state", "shipping_region", "sku", "product_title", "product_type", "vendor", "order_id", "channel", "medium_group", "creative_type", "is_cod"]
byview = collections.defaultdict(list)
for m in ms: byview[m["view"]].append(m)
viewdims = {v: set().union(*[set(m.get("supported_dimensions", [])) for m in lst]) for v, lst in byview.items()}
def rep(v):
    c = [m for m in byview[v] if m.get("aggregation") == "additive" and CAT[m["id"]]["status"] == "certified"]
    c.sort(key=lambda m: (-len(m.get("supported_dimensions", [])), m["id"])); return c[0]["id"] if c else None
REP = {v: rep(v) for v in byview}
matrix = {v: {d: (d in viewdims[v]) for d in KEYDIMS} for v in byview}
json.dump({"matrix": matrix, "rep": REP, "viewdims": {v: sorted(s) for v, s in viewdims.items()}}, open(O + "join_matrix_static.json", "w"))
async def main():
    g = MCPGateway("config/mcp_servers.yaml"); sem = asyncio.Semaphore(8); res = []
    async def q(m, dims, ax=None):
        args = build_metrics_query_args(measure=m, start="2026-09-26", end="2026-10-25", dimensions=dims, limit=50000)
        if ax: args["date_axis"] = ax
        async with sem:
            try: return await g.call(agent_id="seleric_agent", capability="seleric.metrics_query", arguments=args)
            except Exception as e: return {"error": str(e)[:200]}
    async def one(v, d):
        m = REP[v]; t = await q(m, None); r = await q(m, [d])
        if t.get("error") or r.get("error") or not t.get("rows"):
            return dict(view=v, metric=m, dim=d, status="ERROR", note=(t.get("error") or r.get("error") or "empty")[:160])
        tv = float(t["rows"][0].get(m) or 0); rs = r.get("rows") or []
        sv = sum(float(x.get(m) or 0) for x in rs); nul = sum(float(x.get(m) or 0) for x in rs if x.get(d) in (None, "", "(not set)", "unknown", "null"))
        return dict(view=v, metric=m, dim=d, total=tv, sum_by_dim=sv, groups=len(rs), null_value=nul, null_share=(nul / tv if tv else None),
                    preserved=(abs(sv - tv) <= max(1, 1e-4 * abs(tv))), warnings=(r.get("warnings") or [])[:1])
    jobs = [one(v, d) for v in byview if REP[v] for d in KEYDIMS if d in viewdims[v]]
    out = await asyncio.gather(*jobs); json.dump(out, open(O + "join_matrix_cov.json", "w"), default=str)
    print(len(out), collections.Counter("ERR" if o.get("status") == "ERROR" else ("preserved" if o["preserved"] else "NOT_PRESERVED") for o in out))
asyncio.run(main())
