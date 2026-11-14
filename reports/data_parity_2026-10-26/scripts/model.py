"""Translate Cube v3 measures into ClickHouse SQL over the cube's own sql_table (primary-cube measures only)."""
import json, re, os
O = os.path.dirname(__file__) + "/out/"
CM = json.load(open(O + "cube_model.json")); CUBES = CM["cubes"]; VIEWS = {v["name"]: v for v in CM["views"]}
METS = json.load(open(O + "catalogue_metrics.json")); METBY = {m["id"]: m for m in METS}

class Unsupported(Exception): pass

# view member -> (cube, member)
IDX = {}
for vn, v in VIEWS.items():
    for ce in v.get("cubes", []):
        jp = ce["join_path"].split(".")[-1]; inc = ce.get("includes")
        if inc == "*":
            for m in CUBES.get(jp, {}).get("measures", []): IDX[(vn, m["name"])] = (jp, m["name"])
            continue
        for i in inc or []:
            if isinstance(i, str): IDX[(vn, i)] = (jp, i)
            else: IDX[(vn, i.get("alias") or i["name"])] = (jp, i["name"])

def resolve(metric_id):
    vn, mn = METBY[metric_id]["cube_mapping"]["measure"].split(".", 1)
    return vn, IDX[(vn, mn)]

def _sub(sql, cube, depth=0):
    """Replace {CUBE}.x with x, {member} refs with the member's SQL; refuse cross-cube refs."""
    if depth > 8: raise Unsupported("recursion")
    cdef = CUBES[cube]; mem = {m["name"]: m for m in cdef.get("measures", [])}; dims = {d["name"]: d for d in cdef.get("dimensions", [])}
    sql = sql.replace("{CUBE}.", "").replace("{CUBE}", "")
    def rep(mo):
        name = mo.group(1)
        if "." in name: raise Unsupported("cross-cube ref {" + name + "}")
        if name in mem: return "(" + measure_sql(cube, name, depth + 1) + ")"
        if name in dims: return "(" + _sub(dims[name]["sql"], cube, depth + 1) + ")"
        if name == "FILTER_PARAMS": raise Unsupported("filter params")
        raise Unsupported("unknown ref {" + name + "}")
    out = re.sub(r"\{([A-Za-z0-9_.]+)\}", rep, sql)
    if re.search(r"\b[a-z_]+\.[a-z_]+\b", re.sub(r"'[^']*'", "", out)) and re.search(r"\{", sql): raise Unsupported("qualified ref")
    return out

def measure_sql(cube, name, depth=0):
    m = [x for x in CUBES[cube]["measures"] if x["name"] == name][0]; t = m["type"]
    flt = " AND ".join("(" + _sub(f["sql"], cube, depth + 1) + ")" for f in (m.get("filters") or []))
    body = _sub(m["sql"], cube, depth + 1) if m.get("sql") else None
    if m.get("rolling_window"): raise Unsupported("rolling window")
    if t == "number": return body
    if t == "count":
        if body: return f"countIf({body} IS NOT NULL AND ({flt}))" if flt else f"count({body})"
        return f"countIf({flt})" if flt else "count()"
    fn = {"sum": "sum", "avg": "avg", "count_distinct": "uniqExact", "min": "min", "max": "max", "count_distinct_approx": "uniq"}.get(t)
    if not fn: raise Unsupported("type " + t)
    return f"{fn}If({body}, {flt})" if flt else f"{fn}({body})"

_CH = json.load(open(O + "ch_inventory.json"))["columns"]
def time_cols(cube):
    """time dimension -> CH day expression (IST calendar day for DateTime columns, as Cube's default timezone does)."""
    tab = CUBES[cube].get("sql_table"); types = {n: t for n, t in _CH.get(tab, [])}; out = {}
    for d in CUBES[cube].get("dimensions", []):
        if d.get("type") != "time": continue
        e = _sub(d["sql"], cube); t = types.get(e, "")
        out[d["name"]] = f"toDate({e}, 'Asia/Kolkata')" if "DateTime" in t else e
    return out
