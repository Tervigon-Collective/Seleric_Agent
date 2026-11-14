import sys, os, json, asyncio, yaml, time, collections
sys.path.insert(0, os.path.dirname(__file__)); import conn, model
from dotenv import load_dotenv; load_dotenv("/opt/seleric/Seleric_Agent/.env")
from seleric_swarm.protocols.mcp.gateway import MCPGateway
from seleric_swarm.services.mcp_query import build_metrics_query_args
O = os.path.dirname(__file__) + "/out/"
WINDOWS = {"today": ("2026-10-26", "2026-10-26"), "yesterday": ("2026-10-25", "2026-10-25"),
           "last7": ("2026-10-19", "2026-10-25"), "last30": ("2026-09-26", "2026-10-25")}
VIEWDATE = {v["name"]: v.get("date_dimension") for v in yaml.safe_load(open("/opt/seleric/Seleric_Agent_Core/catalogue_v3/views.yaml"))["views"]}
BRAND = 20

# ---- plan: (metric, view, cube, measure, axis, timecol_name)
plan = []
for mid, m in model.METBY.items():
    vn, (cu, mm) = model.resolve(mid)
    tcs = model.time_cols(cu); ax = m.get("date_axis")
    axes = ["order_date", "event_date"] if ax == "switchable" else [None]
    for a in axes:
        if a is None:
            want = VIEWDATE.get(vn); tc = want if want in tcs else (next(iter(tcs)) if tcs else None)
        else: tc = a if a in tcs else None
        plan.append(dict(metric=mid, view=vn, cube=cu, measure=mm, axis=a, timecol=tc, axis_class=ax))
print("plan rows", len(plan))

# ---- per (cube,timecol): CH(before) -> Cube -> CH(after)
ch = conn.ch(); chres = {}; chres2 = {}; chsql = {}; cuberes = {}
groups = collections.defaultdict(list)
for p in plan:
    if p["timecol"]: groups[(p["cube"], p["timecol"])].append(p)
def run_batch(store, cube, tc, win, items):
    a, b = WINDOWS[win]; tab = model.CUBES[cube]["sql_table"]; tcsql = model.time_cols(cube)[tc]
    exprs = {}
    for p in items:
        try: exprs[p["measure"]] = model.measure_sql(cube, p["measure"])
        except model.Unsupported as e: store[(p["metric"], p["axis"], win)] = ("UNSUPPORTED", str(e))
    def q(ns):
        sel = ", ".join(f"toFloat64OrNull(toString({exprs[n]})) AS `m{i}`" for i, n in enumerate(ns))
        return ch.query(f"SELECT {sel} FROM {tab} WHERE brand_id = {BRAND} AND {tcsql} >= '{a}' AND {tcsql} <= '{b}'").result_rows[0]
    def rec(n, v):
        for p in items:
            if p["measure"] == n:
                store[(p["metric"], p["axis"], win)] = ("OK", v)
                chsql[(p["metric"], p["axis"], win)] = f"SELECT {exprs[n]} FROM {tab} WHERE brand_id = {BRAND} AND {tcsql} BETWEEN '{a}' AND '{b}'"
    names = list(exprs)
    try:
        for n, v in zip(names, q(names)): rec(n, v)
    except Exception:
        for n in names:
            try: rec(n, q([n])[0])
            except Exception as e2:
                for p in items:
                    if p["measure"] == n: store[(p["metric"], p["axis"], win)] = ("CH_ERROR", str(e2)[:300])
async def cube_one(g, sem, p, win):
    a, b = WINDOWS[win]
    args = build_metrics_query_args(measure=p["metric"], start=a, end=b)
    if p["axis"]: args["date_axis"] = p["axis"]
    async with sem:
        try: r = await g.call(agent_id="seleric_agent", capability="seleric.metrics_query", arguments=args)
        except Exception as e:
            cuberes[(p["metric"], p["axis"], win)] = {"error": f"{type(e).__name__}: {str(e)[:300]}"}; return
    if not isinstance(r, dict) or r.get("error"):
        cuberes[(p["metric"], p["axis"], win)] = {"error": str(r)[:300]}; return
    rows = r.get("rows") or []; val = None
    if rows:
        v = rows[0].get(p["metric"]); val = float(v) if v not in (None, "") else None
    cuberes[(p["metric"], p["axis"], win)] = {"value": val, "nrows": len(rows), "applied": r.get("date_axis_applied"),
        "warn": (r.get("warnings") or [])[:2], "undated": (r.get("undated") or {}).get("rows") if isinstance(r.get("undated"), dict) else None}
async def main():
    g = MCPGateway("config/mcp_servers.yaml"); sem = asyncio.Semaphore(8)
    for (cube, tc), items in groups.items():
        for win in WINDOWS: run_batch(chres, cube, tc, win, items)
        await asyncio.gather(*[cube_one(g, sem, p, w) for p in items for w in WINDOWS])
        for win in WINDOWS: run_batch(chres2, cube, tc, win, items)
        print("group", cube, tc, len(items), flush=True)
    for p in plan:
        if not p["timecol"]:
            for win in WINDOWS: chres[(p["metric"], p["axis"], win)] = ("NO_TIMECOL", f"cube {p['cube']} has no {p['axis'] or 'time'} column (axis via join)")
            await asyncio.gather(*[cube_one(g, sem, p, w) for w in WINDOWS])
t0 = time.time(); asyncio.run(main()); print("done", round(time.time() - t0), "s", flush=True)
out = [dict(p, window=w, ch=chres.get((p["metric"], p["axis"], w)), ch_after=chres2.get((p["metric"], p["axis"], w)), ch_sql=chsql.get((p["metric"], p["axis"], w)), cube=cuberes.get((p["metric"], p["axis"], w))) for p in plan for w in WINDOWS]
json.dump(out, open(O + "parity_raw2.json", "w"), default=str)
print("saved", len(out), flush=True)
