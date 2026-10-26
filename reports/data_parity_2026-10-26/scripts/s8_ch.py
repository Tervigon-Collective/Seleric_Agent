import sys, os, json, re, collections
sys.path.insert(0, os.path.dirname(__file__)); import conn
O = os.path.dirname(__file__) + "/out/"
ch = conn.ch(); Q = lambda s: ch.query(s).result_rows
W = {"today": ("2026-10-26", "2026-10-26"), "yesterday": ("2026-10-25", "2026-10-25"), "last7": ("2026-10-19", "2026-10-25"), "last30": ("2026-09-26", "2026-10-25")}
res = {"bridge": [], "serve_gold": [], "orders_axis": [], "freshness": []}
# ---- attribution bridge: coverage and credit sums per model
for w, (a, b) in W.items():
    sale = Q(f"SELECT uniqExactIf(order_id, is_sale_order=1) FROM serve.fct_order WHERE brand_id=20 AND order_date BETWEEN '{a}' AND '{b}'")[0][0]
    for model, orders_with, bad, mn, mx in Q(f"""
      SELECT model, uniqExact(order_id), countIf(abs(s-1) > 0.001), min(s), max(s) FROM (
        SELECT model, order_id, sum(credit) s FROM serve.bridge_order_attribution WHERE brand_id=20 AND cohort_date BETWEEN '{a}' AND '{b}' GROUP BY model, order_id) GROUP BY model"""):
        res["bridge"].append(dict(window=w, model=model, sale_orders=sale, orders_with_bridge=orders_with, orders_credit_not_1=bad, min_credit=mn, max_credit=mx))
# ---- serve vs gold direct (counts/sums that must tie)
def pair(name, w, left_name, lsql, right_name, rsql, kind="count"):
    try: l = Q(lsql)[0][0]; r = Q(rsql)[0][0]
    except Exception as e: res["serve_gold"].append(dict(check=name, window=w, left=left_name, left_val=None, right=right_name, right_val=None, note="ERR " + str(e)[:150])); return
    res["serve_gold"].append(dict(check=name, window=w, left=left_name, left_val=float(l or 0), right=right_name, right_val=float(r or 0), kind=kind))
for w, (a, b) in W.items():
    pair("Sale orders: serve.fct_order vs gold.fct_orders (non-test, non-adjustment)", w, "serve.fct_order is_sale_order", f"SELECT uniqExactIf(order_id, is_sale_order=1) FROM serve.fct_order WHERE brand_id=20 AND order_date BETWEEN '{a}' AND '{b}'",
         "gold.fct_orders FINAL NOT is_test AND NOT is_revenue_adjustment", f"SELECT uniqExactIf(order_id, NOT is_test AND NOT is_revenue_adjustment) FROM gold.fct_orders FINAL WHERE brand_id=20 AND order_date BETWEEN '{a}' AND '{b}'")
    pair("Sessions: serve.fct_web_session vs gold.fct_session_funnel", w, "serve sessions", f"SELECT uniqExact(session_id) FROM serve.fct_web_session WHERE brand_id=20 AND session_date BETWEEN '{a}' AND '{b}'",
         "gold sessions FINAL", f"SELECT uniqExact(session_id) FROM gold.fct_session_funnel FINAL WHERE brand_id=20 AND session_date BETWEEN '{a}' AND '{b}'")
    pair("Payments: serve.fct_payment vs gold.fct_payments (non-test)", w, "serve txns", f"SELECT uniqExact(transaction_id) FROM serve.fct_payment WHERE brand_id=20 AND transaction_date BETWEEN '{a}' AND '{b}'",
         "gold txns FINAL non-test", f"SELECT uniqExactIf(transaction_id, NOT ifNull(is_test,0)) FROM gold.fct_payments FINAL WHERE brand_id=20 AND transaction_date BETWEEN '{a}' AND '{b}'")
    pair("Payments amount: serve vs gold", w, "serve Σamount", f"SELECT sum(amount) FROM serve.fct_payment WHERE brand_id=20 AND transaction_date BETWEEN '{a}' AND '{b}'",
         "gold Σamount FINAL non-test", f"SELECT sumIf(toFloat64(amount), NOT ifNull(is_test,0)) FROM gold.fct_payments FINAL WHERE brand_id=20 AND transaction_date BETWEEN '{a}' AND '{b}'", "money")
    pair("Ad spend: serve.fct_paid_media_daily delivery vs serve.fct_ad_delivery", w, "paid_media delivery rows", f"SELECT sum(spend) FROM serve.fct_paid_media_daily WHERE brand_id=20 AND row_type='delivery' AND report_date BETWEEN '{a}' AND '{b}'",
         "fct_ad_delivery", f"SELECT sum(spend) FROM serve.fct_ad_delivery WHERE brand_id=20 AND report_date BETWEEN '{a}' AND '{b}'", "money")
    pair("Net sales: serve.mart_profit vs serve.mart_product_profit (order_date)", w, "mart_profit Σnet_sales", f"SELECT sum(net_sales) FROM serve.mart_profit WHERE brand_id=20 AND order_date BETWEEN '{a}' AND '{b}'",
         "mart_product_profit Σnet_sales", f"SELECT sum(net_sales) FROM serve.mart_product_profit WHERE brand_id=20 AND order_date BETWEEN '{a}' AND '{b}'", "money")
    pair("Net sales: serve.mart_profit vs serve.mart_product_profit (event_date)", w, "mart_profit Σnet_sales", f"SELECT sum(net_sales) FROM serve.mart_profit WHERE brand_id=20 AND event_date BETWEEN '{a}' AND '{b}'",
         "mart_product_profit Σnet_sales", f"SELECT sum(net_sales) FROM serve.mart_product_profit WHERE brand_id=20 AND event_date BETWEEN '{a}' AND '{b}'", "money")
    pair("Ad spend: serve.mart_profit vs paid_media delivery", w, "mart_profit Σad_spend (order_date)", f"SELECT sum(ad_spend) FROM serve.mart_profit WHERE brand_id=20 AND order_date BETWEEN '{a}' AND '{b}'",
         "fct_ad_delivery Σspend", f"SELECT sum(spend) FROM serve.fct_ad_delivery WHERE brand_id=20 AND report_date BETWEEN '{a}' AND '{b}'", "money")
# ---- orders-axis diagnostic (the 45 vs 26 case)
for w, (a, b) in W.items():
    r = Q(f"""SELECT uniqExactIf(o.order_id, o.is_sale_order=1) FROM serve.fct_order_commerce o WHERE o.brand_id=20 AND o.order_date BETWEEN '{a}' AND '{b}'""")[0][0]
    r2 = Q(f"""SELECT uniqExactIf(o.order_id, o.is_sale_order=1), count() FROM serve.fct_order_commerce o INNER JOIN serve.mart_product_profit e ON e.brand_id=o.brand_id AND e.order_id=o.order_id WHERE o.brand_id=20 AND e.event_date BETWEEN '{a}' AND '{b}'""")[0]
    r3 = Q(f"""SELECT uniqExactIf(o.order_id, o.is_sale_order=1) FROM serve.fct_order_commerce o INNER JOIN serve.mart_product_profit e ON e.brand_id=o.brand_id AND e.order_id=o.order_id WHERE o.brand_id=20 AND e.event_date BETWEEN '{a}' AND '{b}' AND o.order_date BETWEEN '{a}' AND '{b}'""")[0][0]
    res["orders_axis"].append(dict(window=w, placed_in_window=r, orders_joined_event_date=r2[0], of_which_placed_in_window=r3, older_orders_with_event_in_window=r2[0] - r3))
# ---- freshness: latest date per object
INV = json.load(open(O + "ch_inventory.json"))
PREF = ["refreshed_at", "_loaded_at", "updated_at", "order_date", "report_date", "session_date", "transaction_date", "movement_date", "inspection_date", "event_date", "created_at", "changed_at"]
for t in INV["tables"]:
    if t["database"] not in ("gold", "serve"): continue
    cols = dict(INV["columns"][f"{t['database']}.{t['name']}"]); col = next((c for c in PREF if c in cols and ("Date" in cols[c])), None)
    if not col: res["freshness"].append(dict(object=f"{t['database']}.{t['name']}", col=None, max=None, rows=t["total_rows"])); continue
    try:
        mx = Q(f"SELECT max(`{col}`) FROM {t['database']}.`{t['name']}`")[0][0]
        res["freshness"].append(dict(object=f"{t['database']}.{t['name']}", col=col, max=str(mx), rows=t["total_rows"]))
    except Exception as e: res["freshness"].append(dict(object=f"{t['database']}.{t['name']}", col=col, max="ERR " + str(e)[:80], rows=t["total_rows"]))
json.dump(res, open(O + "ch_checks.json", "w"), default=str)
print({k: len(v) for k, v in res.items()})
