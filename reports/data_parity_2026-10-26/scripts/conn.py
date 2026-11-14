import os, sys
sys.path.insert(0, os.path.dirname(__file__) + "/libs")
from dotenv import dotenv_values
ENV = dotenv_values("/opt/seleric/mage-ai/.env")
def ch():
    import clickhouse_connect
    c = clickhouse_connect.get_client(host=ENV["CLICKHOUSE_HOST"], port=int(ENV["CLICKHOUSE_PORT"]), username=ENV["CLICKHOUSE_USER"],
        password=ENV["CLICKHOUSE_PASSWORD"], secure=str(ENV.get("CLICKHOUSE_SECURE","")).lower() in ("1","true","yes"),
        settings={"readonly": 1}, connect_timeout=15, send_receive_timeout=120)
    return c
def trino_conn():
    import trino
    kw = dict(host=ENV["TRINO_ICEBERG_HOST"], port=int(ENV["TRINO_ICEBERG_PORT"]), user=ENV["TRINO_ICEBERG_USER"],
              catalog=ENV.get("TRINO_ICEBERG_CATALOG","iceberg"), http_scheme=ENV.get("TRINO_ICEBERG_HTTP_SCHEME","http"))
    if ENV.get("TRINO_ICEBERG_PASSWORD") and kw["http_scheme"]=="https":
        kw["auth"] = trino.auth.BasicAuthentication(ENV["TRINO_ICEBERG_USER"], ENV["TRINO_ICEBERG_PASSWORD"])
    return trino.dbapi.connect(**kw)
def trino_q(sql):
    import re
    assert re.match(r"\s*(select|show|describe|with)\b", sql, re.I), "read-only guard"
    cur = trino_conn().cursor(); cur.execute(sql); return [d[0] for d in cur.description], cur.fetchall()
