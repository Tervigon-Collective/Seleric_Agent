import sys, os, json, re, time, concurrent.futures as cf
sys.path.insert(0, os.path.dirname(__file__)); import conn
O = os.path.dirname(__file__) + "/out/"
WINDOWS = {"today": ("2026-10-26", "2026-10-26"), "yesterday": ("2026-10-25", "2026-10-25"),
           "last7": ("2026-10-19", "2026-10-25"), "last30": ("2026-09-26", "2026-10-25")}
inv = json.load(open(O + "ch_inventory.json")); tr = json.load(open(O + "trino_inventory.json"))
chtabs = {t["name"]: t for t in inv["tables"] if t["database"] == "gold"}
common = sorted(set(chtabs) & set(tr["gold"]))
only_ch = sorted(set(chtabs) - set(tr["gold"])); only_tr = sorted(set(tr["gold"]) - set(chtabs))
print("gold common", len(common), "only CH", len(only_ch), "only Trino", len(only_tr))
PREF_DATE = ["report_date", "order_date", "event_date", "session_date", "date", "created_date", "report_day", "snapshot_date", "movement_date"]
MONEYISH = re.compile(r"(spend|sales|revenue|amount|cost|price|total|qty|quantity|count|clicks|impressions|orders|units|value|fee|discount|tax)", re.I)
def is_num(t): return bool(re.search(r"(Int|UInt|Float|Decimal)", t)) and "Date" not in t
def pick(cols):
    dates = [(n, t) for n, t in cols if t.startswith(("Date", "Nullable(Date", "LowCardinality(Date")) and "Time" not in t.split("(")[0] or t.startswith("Date") ]
    dn = None
    names = [n for n, t in cols if "Date" in t]
    for p in PREF_DATE:
        if p in names: dn = p; break
    if dn is None and names: dn = names[0]
    nums = [n for n, t in cols if is_num(t) and not n.endswith("_id") and n != "id" and n != "brand_id"]
    nums = sorted(nums, key=lambda n: (not MONEYISH.search(n), n))[:8]
    return dn, nums
def trino_cols(t):
    _, rows = conn.trino_q(f"describe iceberg.gold.{t}"); return {r[0] for r in rows}
def one(t):
    cols = inv["columns"][f"gold.{t}"]; dn, nums = pick(cols)
    res = {"table": t, "date_col": dn, "rows": []}
    if not dn: res["note"] = "no date column"; 
    try: tcols = trino_cols(t)
    except Exception as e: res["error"] = "trino describe: " + str(e)[:150]; return res
    nums = [n for n in nums if n in tcols]; has_brand = "brand_id" in tcols and any(n == "brand_id" for n, _ in cols)
    if dn and dn not in tcols: dn = None; res["note"] = "date col missing in trino"
    eng = chtabs[t]["engine"]; final = " FINAL" if "Replacing" in eng or "Collapsing" in eng else ""
    # CH
    chexpr, trexpr, keys = [], [], []
    for w, (a, b) in WINDOWS.items():
        cc = f"toDate(`{dn}`) >= '{a}' AND toDate(`{dn}`) <= '{b}'" if dn else "1=1"
        tc = f"CAST(\"{dn}\" AS date) >= DATE '{a}' AND CAST(\"{dn}\" AS date) <= DATE '{b}'" if dn else "true"
        chexpr.append(f"countIf({cc}) AS `{w}__rows`"); trexpr.append(f"count_if({tc}) AS \"{w}__rows\""); keys.append((w, "rows"))
        for n in nums:
            chexpr.append(f"sumIf(toFloat64OrZero(toString(`{n}`)), {cc}) AS `{w}__{n}`")
            trexpr.append(f"sum(IF({tc}, CAST(\"{n}\" AS double), 0)) AS \"{w}__{n}\""); keys.append((w, n))
    wh = " WHERE brand_id = 20" if has_brand else ""
    try:
        r = conn.ch().query(f"SELECT {', '.join(chexpr)} FROM gold.`{t}`{final}{wh}"); chv = r.result_rows[0]
    except Exception as e: res["error"] = "CH: " + str(e)[:200]; return res
    try:
        _, rows = conn.trino_q(f"SELECT {', '.join(trexpr)} FROM iceberg.gold.\"{t}\"" + (" WHERE brand_id = 20" if has_brand else "")); trv = rows[0]
    except Exception as e: res["error"] = "Trino: " + str(e)[:200]; return res
    for (w, n), a, b in zip(keys, chv, trv): res["rows"].append([w, n, a, b])
    res["final"] = bool(final); res["brand_filter"] = has_brand
    return res
t0 = time.time(); results = []
with cf.ThreadPoolExecutor(4) as ex:
    for r in ex.map(one, common): results.append(r)
json.dump({"results": results, "only_ch": only_ch, "only_trino": only_tr}, open(O + "sync_parity.json", "w"), default=str)
print("done", round(time.time() - t0), "s;", sum(1 for r in results if r.get("error")), "errors")
