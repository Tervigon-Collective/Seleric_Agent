import json, os, collections
from cmp import METS, tol_status, O
def classify2(r):
    m = METS[r["metric"]]; cu = r["cube"] or {}; ch = r["ch"] or ("MISSING", ""); ch2 = r.get("ch_after") or ch
    if "error" in cu:
        e = cu["error"]
        chv = ch[1] if ch[0] == "OK" else None
        if "stale_data" in e: return "REFUSED:STALE", None, chv, "Core refused (freshness gate, fail_closed); ClickHouse value recomputed from the cube table for review"
        if "Not populated" in e or "cannot be answered" in e: return "REFUSED:NOT_POPULATED", None, chv, e[:160]
        return "CUBE_ERROR", None, chv, e[:200]
    if ch[0] in ("UNSUPPORTED", "NO_TIMECOL"): return "NOT_RECOMPUTED", cu.get("value"), None, ch[1]
    if ch[0] == "CH_ERROR": return "CH_ERROR", cu.get("value"), None, ch[1]
    s1, d1 = tol_status(cu.get("value"), ch[1], m); s2, d2 = tol_status(cu.get("value"), ch2[1] if ch2 and ch2[0] == "OK" else ch[1], m)
    best = min((s1, d1), (s2, d2), key=lambda x: (["PASS", "WARN", "FAIL"].index(x[0]), x[1]))
    drift = tol_status(ch[1], ch2[1], m)[0] if ch2 and ch2[0] == "OK" else "PASS"
    s = best[0]
    if s != "PASS" and drift != "PASS": s = "UNSTABLE"
    return s, cu.get("value"), ch[1], f"delta={best[1]:.6g}" + (" (CH moved during run)" if drift != "PASS" else "")
if __name__ == "__main__":
    rows = json.load(open(O + "parity_raw2.json")); cnt = collections.Counter(); bad = []
    for r in rows:
        s, a, b, note = classify2(r)
        if r["window"] == "today" and s in ("WARN", "FAIL", "UNSTABLE"): s = "INFO(partial)"
        cnt[(r["window"], s)] += 1
        if s in ("FAIL", "WARN", "UNSTABLE", "CH_ERROR", "CUBE_ERROR") and r["window"] != "today": bad.append((s, r["metric"], r["axis"], r["window"], a, b, note))
    for w in ("today", "yesterday", "last7", "last30"): print(w, {s: cnt[(w, s)] for s in sorted({k[1] for k in cnt})})
    seen = set()
    for x in sorted(bad):
        k = (x[1], x[2])
        if k in seen: continue
        seen.add(k); print(x)
