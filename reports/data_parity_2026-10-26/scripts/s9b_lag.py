import sys, os, json, concurrent.futures as cf; sys.path.insert(0, os.path.dirname(__file__)); import conn
O = os.path.dirname(__file__) + "/out/"; inv = json.load(open(O + "ch_inventory.json")); tr = json.load(open(O + "trino_inventory.json"))
common = sorted({t["name"] for t in inv["tables"] if t["database"] == "gold"} & set(tr["gold"]))
def one(t):
    cols = dict(inv["columns"][f"gold.{t}"]); col = next((c for c in ("_loaded_at", "_gold_created_at", "refreshed_at") if c in cols), None)
    if not col: return None
    try:
        a = conn.ch().query(f"SELECT max(`{col}`), count() FROM gold.`{t}`").result_rows[0]
        _, r = conn.trino_q(f'SELECT max("{col}"), count(*) FROM iceberg.gold."{t}"'); b = r[0]
        ca = a[0].replace(tzinfo=None) if a[0] else None; cb = b[0].replace(tzinfo=None) if b[0] else None
        return dict(table=t, col=col, ch_max=str(ca), ice_max=str(cb), lag_min=((cb - ca).total_seconds() / 60 if ca and cb else None))
    except Exception as e: return dict(table=t, col=col, error=str(e)[:100])
with cf.ThreadPoolExecutor(4) as ex: res = [r for r in ex.map(one, common) if r]
json.dump(res, open(O + "lag.json", "w"), default=str)
big = sorted([r for r in res if r.get("lag_min") is not None], key=lambda r: -r["lag_min"])[:12]
for r in big: print(r)
print(len(res), "tables;", sum(1 for r in res if (r.get("lag_min") or 0) > 15), "lag>15min")
