import sys, os, json, asyncio, yaml; sys.path.insert(0, os.path.dirname(__file__)); import conn, model
from dotenv import load_dotenv; load_dotenv("/opt/seleric/Seleric_Agent/.env")
from seleric_swarm.protocols.mcp.gateway import MCPGateway
from seleric_swarm.services.mcp_query import build_metrics_query_args
O = os.path.dirname(__file__) + "/out/"; A, B = "2026-09-26", "2026-10-26"
VIEWDATE = {v["name"]: v.get("date_dimension") for v in yaml.safe_load(open("/opt/seleric/Seleric_Agent_Core/catalogue_v3/views.yaml"))["views"]}
HEAD = [("orders", False), ("gross_sales", False), ("order_pnl_net_sales", True), ("net_sales", True), ("cancel_revenue", True), ("rto_revenue", True), ("return_revenue", True), ("net_cogs", True), ("operating_cost", True),
        ("contribution_margin", True), ("net_profit", True), ("order_pnl_ad_spend", False), ("ad_spend", False), ("impressions", False), ("clicks", False), ("link_clicks", False), ("new_customers", False), ("cod_orders", False),
        ("sessions", False), ("add_to_carts", False), ("funnel_purchases", False), ("funnel_order_revenue", False), ("funnel_order_purchases", False), ("payment_amount", False), ("transactions", False),
        ("refund_count", False), ("touches", False), ("units_sold", True), ("product_order_net_sales", True), ("product_ad_spend", False)]
ch = conn.ch(); out = []
async def main():
    g = MCPGateway("config/mcp_servers.yaml"); sem = asyncio.Semaphore(6)
    async def job(mid, sw, axis):
        vn, (cu, mm) = model.resolve(mid); tcs = model.time_cols(cu); tc = axis if axis else (VIEWDATE.get(vn) if VIEWDATE.get(vn) in tcs else next(iter(tcs), None))
        args = build_metrics_query_args(measure=mid, start=A, end=B, grain="day"); args["granularity"] = "day"
        if axis: args["date_axis"] = axis
        async with sem:
            try: r = await g.call(agent_id="seleric_agent", capability="seleric.metrics_query", arguments=args)
            except Exception as e: r = {"error": str(e)[:150]}
        cubed = {}
        if not r.get("error"):
            for row in r.get("rows") or []:
                dk = [v for k, v in row.items() if k.endswith(".day")]
                if dk and row.get(mid) not in (None, ""): cubed[dk[0][:10]] = float(row[mid])
        chd = {}
        if tc and not r.get("error"):
            try:
                expr = model.measure_sql(cu, mm); tab = model.CUBES[cu]["sql_table"]; tcs_ = tcs[tc]
                sql = f"SELECT toString({tcs_}) d, toFloat64OrNull(toString({expr})) v FROM {tab} WHERE brand_id=20 AND {tcs_} >= '{A}' AND {tcs_} <= '{B}' GROUP BY d"
                chd = {d: v for d, v in ch.query(sql).result_rows}
            except Exception as e: chd = {"__err": str(e)[:100]}
        return mid, axis or "-", tc, r.get("error"), cubed, chd
    jobs = [job(m, sw, ax) for m, sw in HEAD for ax in (["order_date", "event_date"] if sw else [None])]
    return await asyncio.gather(*jobs)
res = asyncio.run(main())
json.dump(res, open(O + "daily.json", "w"), default=str)
print(len(res), [(r[0], r[1], r[3]) for r in res if r[3]], [(r[0], r[1]) for r in res if "__err" in r[5]])
