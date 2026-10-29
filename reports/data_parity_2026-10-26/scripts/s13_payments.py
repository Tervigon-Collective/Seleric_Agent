import sys, os, json, asyncio; sys.path.insert(0, os.path.dirname(__file__)); import conn
from dotenv import load_dotenv; load_dotenv("/opt/seleric/Seleric_Agent/.env")
from seleric_swarm.protocols.mcp.gateway import MCPGateway
from seleric_swarm.services.mcp_query import build_metrics_query_args
O = os.path.dirname(__file__) + "/out/"; ch = conn.ch(); Q = lambda s: ch.query(s).result_rows
W = {"today": ("2026-10-26", "2026-10-26"), "yesterday": ("2026-10-25", "2026-10-25"), "last7": ("2026-10-19", "2026-10-25"), "last30": ("2026-09-26", "2026-10-25")}
sj = lambda k: f"json_extract_scalar(data,'$.{k}')"; res = {}
_, kinds = conn.trino_q(f"""WITH r AS (SELECT {sj('kind')} k, {sj('status')} st, {sj('test')} t, row_number() OVER (PARTITION BY {sj('id')} ORDER BY extracted_at DESC) rn FROM iceberg.raw.shopify_transactions WHERE brand_id='20' AND dt >= DATE '2026-09-20' AND substr({sj('created_at')},1,10) BETWEEN '2026-09-26' AND '2026-10-25')
  SELECT k, st, t, count(*) FROM r WHERE rn=1 GROUP BY 1,2,3 ORDER BY 4 DESC""")
res["raw_kinds"] = kinds
for w, (a, b) in W.items():
    d = {}
    _, r = conn.trino_q(f"""WITH r AS (SELECT {sj('id')} id, {sj('kind')} k, {sj('status')} st, {sj('test')} t, TRY_CAST({sj('amount')} AS double) amt, substr({sj('created_at')},1,10) d,
        row_number() OVER (PARTITION BY {sj('id')} ORDER BY extracted_at DESC) rn FROM iceberg.raw.shopify_transactions WHERE brand_id='20' AND dt >= DATE '2026-09-20')
      SELECT count(*), count_if(st='success'), sum(if(st='success' AND t='false' AND k IN ('sale','capture'), amt, 0)), sum(if(st='success' AND t='false' AND k='refund', amt, 0)),
             sum(if(st='success' AND t='false' AND k IN ('sale','capture'), amt, 0)) - sum(if(st='success' AND t='false' AND k='refund', amt, 0)), count_if(st='success' AND t='false' AND k IN ('sale','capture')) FROM r WHERE rn=1 AND d BETWEEN '{a}' AND '{b}'""")
    d["raw"] = r[0]
    d["gold"] = Q(f"""SELECT uniqExact(transaction_id), uniqExactIf(transaction_id, is_successful=1), sumIf(toFloat64(amount), is_successful=1 AND NOT ifNull(is_test,0) AND is_refund=0), sumIf(toFloat64(amount), is_successful=1 AND NOT ifNull(is_test,0) AND is_refund=1),
        sumIf(amount_signed, is_successful=1 AND NOT ifNull(is_test,0)), uniqExactIf(transaction_id, is_successful=1 AND NOT ifNull(is_test,0) AND is_refund=0) FROM gold.fct_payments FINAL WHERE brand_id=20 AND transaction_date BETWEEN '{a}' AND '{b}'""")[0]
    d["serve"] = Q(f"""SELECT uniqExact(transaction_id), uniqExactIf(transaction_id, is_successful=1), sumIf(amount, is_successful=1 AND is_refund=0 AND payment_class='cash'), sumIf(amount, is_successful=1 AND is_refund=1 AND payment_class='cash'),
        sumIf(amount_signed, payment_class='cash'), uniqExactIf(transaction_id, payment_class='cash') FROM serve.fct_payment WHERE brand_id=20 AND transaction_date BETWEEN '{a}' AND '{b}'""")[0]
    d["serve_class"] = Q(f"SELECT payment_class, is_successful, is_refund, count(), sum(amount_signed) FROM serve.fct_payment WHERE brand_id=20 AND transaction_date BETWEEN '{a}' AND '{b}' GROUP BY 1,2,3 ORDER BY 4 DESC")
    res[w] = d
async def cube():
    g = MCPGateway("config/mcp_servers.yaml"); out = {}
    for w, (a, b) in W.items():
        for m in ("payment_amount", "transactions"):
            r = await g.call(agent_id="seleric_agent", capability="seleric.metrics_query", arguments=build_metrics_query_args(measure=m, start=a, end=b))
            out[f"{w}|{m}"] = float((r.get("rows") or [{}])[0].get(m) or 0) if not r.get("error") else None
    return out
res["cube"] = asyncio.run(cube()); json.dump(res, open(O + "payments.json", "w"), default=str)
for w in W: print(w, "raw", res[w]["raw"], "\n   gold", res[w]["gold"], "\n   serve", res[w]["serve"], "\n   cube", res["cube"][f"{w}|payment_amount"], res["cube"][f"{w}|transactions"])
print(res["raw_kinds"][:8]); print(res["last30"]["serve_class"])
