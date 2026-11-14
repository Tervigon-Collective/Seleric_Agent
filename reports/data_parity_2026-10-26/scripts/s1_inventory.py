import sys, os, json, glob, yaml
sys.path.insert(0, os.path.dirname(__file__)); import conn
OUT = os.path.dirname(__file__) + "/out/"
CORE = "/opt/seleric/Seleric_Agent_Core/catalogue_v3/"
CUBE = "/opt/seleric/mage-ai/infra/cube/model_v3/"
# 1. catalogue metrics
mets = []
for f in sorted(glob.glob(CORE + "metrics/*.yaml")):
    d = yaml.safe_load(open(f)); d["_file"] = os.path.basename(f); mets.append(d)
json.dump(mets, open(OUT + "catalogue_metrics.json", "w"), default=str)
print("catalogue metric files:", len(mets), "keys sample:", sorted(mets[0].keys()))
# 2. cube cubes / views
cubes = {}
for f in glob.glob(CUBE + "cubes/*.yml"):
    for c in yaml.safe_load(open(f)).get("cubes", []): c["_file"] = os.path.basename(f); cubes[c["name"]] = c
views = yaml.safe_load(open(CUBE + "views/views.yml"))["views"]
json.dump({"cubes": cubes, "views": views}, open(OUT + "cube_model.json", "w"), default=str)
print("cubes", len(cubes), "views", len(views))
# 3. ClickHouse object inventory
c = conn.ch()
r = c.query("select database, name, engine, total_rows, total_bytes, create_table_query from system.tables where database in ('gold','serve','semantic') order by database,name")
tabs = [dict(zip(r.column_names, row)) for row in r.result_rows]
r = c.query("select database, table, name, type from system.columns where database in ('gold','serve','semantic') order by database,table,position")
cols = {}
for db, t, n, ty in r.result_rows: cols.setdefault(f"{db}.{t}", []).append([n, ty])
json.dump({"tables": tabs, "columns": cols}, open(OUT + "ch_inventory.json", "w"), default=str)
print("CH objects", len(tabs), {k: sum(1 for t in tabs if t['database']==k) for k in ('gold','serve','semantic')})
# 4. Trino iceberg inventory (schemas gold/silver/raw/mart/dw)
tr = {}
for sch in ("gold", "mart", "dw", "silver", "raw"):
    try:
        _, rows = conn.trino_q(f"show tables from iceberg.{sch}"); tr[sch] = [x[0] for x in rows]
    except Exception as e: tr[sch] = f"ERR {str(e)[:100]}"
json.dump(tr, open(OUT + "trino_inventory.json", "w"))
print({k: (len(v) if isinstance(v, list) else v) for k, v in tr.items()})
