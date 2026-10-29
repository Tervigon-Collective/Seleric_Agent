import sys, os, json; sys.path.insert(0, os.path.dirname(__file__)); import conn
O = os.path.dirname(__file__) + "/out/"; ch = conn.ch(); Q = lambda s: ch.query(s).result_rows
A, B = "2026-09-26", "2026-10-25"; res = {}
# Shopify order waterfall (last30, brand 20): raw -> gold -> non-test -> sale order -> Cube
sj = lambda k: f"json_extract_scalar(data,'$.{k}')"
_, r = conn.trino_q(f"""WITH r AS (SELECT substr({sj('created_at')},1,10) d, {sj('id')} id, {sj('test')} tst, {sj('source_name')} src, row_number() OVER (PARTITION BY {sj('id')} ORDER BY extracted_at DESC) rn
  FROM iceberg.raw.shopify_orders WHERE brand_id='20' AND dt >= DATE '2026-09-20') SELECT count(*), count_if(tst='true'), count_if(src='shopify_draft_order') FROM r WHERE rn=1 AND d BETWEEN '{A}' AND '{B}'""")
res["raw"] = r[0]
res["gold"] = Q(f"SELECT uniqExact(order_id), uniqExactIf(order_id, is_test), uniqExactIf(order_id, is_revenue_adjustment), uniqExactIf(order_id, NOT is_test AND NOT is_revenue_adjustment) FROM gold.fct_orders FINAL WHERE brand_id=20 AND order_date BETWEEN '{A}' AND '{B}'")[0]
res["serve_by_source"] = Q(f"""SELECT g.source_name, s.is_sale_order, count() FROM (SELECT order_id, any(source_name) source_name FROM gold.fct_orders FINAL WHERE brand_id=20 AND order_date BETWEEN '{A}' AND '{B}' GROUP BY order_id) g
  LEFT JOIN (SELECT order_id, any(is_sale_order) is_sale_order FROM serve.fct_order WHERE brand_id=20 GROUP BY order_id) s ON s.order_id=g.order_id GROUP BY 1,2 ORDER BY 3 DESC""")
res["order_class"] = Q(f"SELECT order_type, refund_class, outcome, count() FROM semantic.core_order_class WHERE brand_id=20 AND order_date BETWEEN '{A}' AND '{B}' GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 15")
# ad-spend P&L vs delivery, per day last 7 + refreshed stamps
res["adspend_days"] = Q("""SELECT a.d, a.f, b.p, a.f-b.p FROM (SELECT report_date d, sum(spend) f FROM serve.fct_ad_delivery WHERE brand_id=20 AND report_date BETWEEN '2026-10-19' AND '2026-10-25' GROUP BY 1) a
  LEFT JOIN (SELECT order_date d, sum(ad_spend) p FROM serve.mart_profit WHERE brand_id=20 AND order_date BETWEEN '2026-10-19' AND '2026-10-25' GROUP BY 1) b ON a.d=b.d ORDER BY 1""")
res["adspend_refresh"] = Q("SELECT max(refreshed_at) FROM serve.mart_profit WHERE brand_id=20")
res["adspend_platform_1025"] = Q("""SELECT a.ad_platform, a.f, b.p FROM (SELECT ad_platform, sum(spend) f FROM serve.fct_ad_delivery WHERE brand_id=20 AND report_date='2026-10-25' GROUP BY 1) a
  LEFT JOIN (SELECT finance_channel ad_platform, sum(ad_spend) p FROM serve.mart_profit WHERE brand_id=20 AND order_date='2026-10-25' GROUP BY 1) b ON a.ad_platform=b.ad_platform""")
json.dump(res, open(O + "extra.json", "w"), default=str)
for k, v in res.items(): print(k, str(v)[:400])
