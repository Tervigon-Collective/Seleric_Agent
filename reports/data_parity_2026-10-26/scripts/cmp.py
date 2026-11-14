import json, os, collections
O = os.path.dirname(__file__) + "/out/"
METS = {m["id"]: m for m in json.load(open(O + "catalogue_metrics.json"))}
def tol_status(a, b, m):
    """a=Cube, b=CH. returns (status, delta)"""
    a = 0.0 if a is None else a; b = 0.0 if b is None else b
    d = abs(a - b); unit = (m.get("unit") or "").lower(); agg = m.get("aggregation")
    rel = d / max(abs(b), 1e-12)
    if agg in ("count", "count_distinct") or unit in ("count", "orders", "units"):
        ok, warn = d < 0.5, d < 5 or rel < 1e-3
    elif unit == "inr" or agg == "additive":
        ok, warn = (d <= 1 or rel <= 1e-4), (d <= 10 or rel <= 1e-3)
    else:
        ok, warn = (d <= 1e-6 or rel <= 1e-3), (rel <= 1e-2)
    return ("PASS" if ok else "WARN" if warn else "FAIL"), d
def classify(r):
    m = METS[r["metric"]]; cu = r["cube"] or {}; ch = r["ch"] or ("MISSING", "")
    if r["window"] == "today":
        pass
    if "error" in cu:
        return "CUBE_ERROR_or_REFUSED", None, None, cu["error"][:200]
    if ch[0] == "UNSUPPORTED": return "NOT_RECOMPUTED", cu.get("value"), None, ch[1]
    if ch[0] == "NO_TIMECOL": return "NOT_RECOMPUTED", cu.get("value"), None, ch[1]
    if ch[0] == "CH_ERROR": return "CH_ERROR", cu.get("value"), None, ch[1]
    s, d = tol_status(cu.get("value"), ch[1], m)
    return s, cu.get("value"), ch[1], f"delta={d:.6g}"
if __name__ == "__main__":
    rows = json.load(open(O + "parity_raw.json")); cnt = collections.Counter(); bad = []
    for r in rows:
        s, a, b, note = classify(r); cnt[(r["window"], s)] += 1
        if s in ("FAIL", "WARN", "CH_ERROR", "CUBE_ERROR_or_REFUSED"): bad.append((s, r["metric"], r["axis"], r["window"], a, b, note))
    for w in ("today", "yesterday", "last7", "last30"): print(w, {s: cnt[(w, s)] for s in ("PASS", "WARN", "FAIL", "NOT_RECOMPUTED", "CH_ERROR", "CUBE_ERROR_or_REFUSED")})
    print(len(bad))
    seen = set()
    for x in sorted(bad):
        if (x[0], x[1], x[2]) in seen or x[3] == "today": continue
        seen.add((x[0], x[1], x[2])); print(x)
