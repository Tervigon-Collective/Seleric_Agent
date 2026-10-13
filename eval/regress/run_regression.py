"""Live regression through the real API: status, latency, tool/LLM counts, grounding, checks, p50/p90.

usage: python3 run_regression.py <questions.json> <out_dir> [concurrency] [only=1,2,...]

Each item runs in its own thread, except items sharing a "session" key: those run in order in one thread
(follow-ups). Item keys: q, aspect, checks (answer checks: contains_sql / contains_number / contains_any /
contains_all_words / not_lead / not_contains / max_revisions), tool_checks (key into agent6_checks.CHECKS, scored
over trace.steps), reference ("q1": direct MCP net_sales yesterday vs previous_period).
Writes r<NN>.json per item and summary.json (pass counts, p50/p90 wall time) in <out_dir>.
"""
import concurrent.futures as cf
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent6_checks
import mcp_direct


def _read_lines(path):
    with open(path) as f:
        return f.read().splitlines()


def _write_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=1)


KEY = next(l.split("=", 1)[1].strip() for l in _read_lines("/opt/seleric/Seleric_Agent/.env") if l.startswith("API_KEY="))
BASE = "http://127.0.0.1:8090/v1"
H = {"x-api-key": KEY, "content-type": "application/json"}


def req(method, path, body=None):
    r = urllib.request.Request(BASE + path, method=method, headers=H,
                               data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(r, timeout=60) as f:
        return json.loads(f.read() or b"{}")


def psql(sql):
    return subprocess.run(["docker", "exec", "-i", "seleric_agent-postgres-1", "psql", "-U", "seleric", "-d",
                           "seleric_swarm", "-At", "-c", sql], capture_output=True, text=True, check=False).stdout.strip()


def numbers(text):
    """Every number written in the answer, as floats (thousands separators and unicode minus handled)."""
    out, cur = [], ""
    t = (text or "").replace("−", "-").replace("–", "-")
    for ch in t + " ":
        if ch.isdigit() or ch in ".,-" and (cur or ch == "-"):
            cur += ch
            continue
        token = cur.replace(",", "").rstrip(".-")
        if token and token not in ("-",):
            try:
                out.append(float(token))
            except ValueError:
                pass
        cur = ""
    return out


def sql_number(sql):
    """Ground truth computed at check time (data refreshes move yesterday's numbers)."""
    out = subprocess.run([os.path.join(os.path.dirname(os.path.abspath(__file__)), "ch.sh"), sql],
                         capture_output=True, text=True, check=False).stdout.strip().splitlines()
    return float(out[-1].split("\t")[0])


def check(answer, revisions, c):
    a = answer or ""
    if "contains_sql" in c:
        want = sql_number(c["contains_sql"])
        tol = max(0.006, abs(want) * 0.002) if abs(want) < 100 else max(0.6, abs(want) * 0.002)
        # a deduction may be printed with its sign: the magnitude is the figure
        return any(abs(n - want) <= tol or abs(abs(n) - abs(want)) <= tol for n in numbers(a)), f"number {want} (live)"
    if "contains_number" in c:
        want = c["contains_number"]
        return any(abs(n - want) <= max(0.6, abs(want) * 0.0005) for n in numbers(a)), f"number {want}"
    if "contains_any" in c:
        return any(x.lower() in a.lower() for x in c["contains_any"]), f"any of {c['contains_any']}"
    if "contains_all_words" in c:
        return all(x.lower() in a.lower() for x in c["contains_all_words"]), f"all of {c['contains_all_words']}"
    if "not_lead" in c:
        return not a.strip().lower().startswith(c["not_lead"].lower()), f"does not lead with '{c['not_lead']}'"
    if "not_contains" in c:
        return c["not_contains"].lower() not in a.lower(), f"not '{c['not_contains']}'"
    if "max_revisions" in c:
        return revisions <= c["max_revisions"], f"revisions <= {c['max_revisions']}"
    return True, "?"


def tool_calls(steps):
    """[{name, args, ok}] from trace.steps: each tool_call paired in order with its tool_return."""
    calls, pending = [], {}
    for st in steps:
        if st.get("kind") == "tool_call":
            c = {"name": st.get("tool"), "args": st.get("args") if isinstance(st.get("args"), dict) else {}, "ok": st.get("ok", True)}
            calls.append(c)
            pending.setdefault(st.get("tool"), []).append(c)
        elif st.get("kind") == "tool_return" and pending.get(st.get("tool")):
            c = pending[st.get("tool")].pop(0)
            if "ok" not in st:  # old loop: success inferred from the stringified result
                c["ok"] = "'success': False" not in str(st.get("result"))
    return calls


_REF = {}


def reference(kind):
    if kind not in _REF:
        r = mcp_direct.call_tool("metrics_query", {"measures": ["net_sales"], "time_range": {"preset": "yesterday"},
                                                   "compare_period": "previous_period"})
        _REF[kind] = {"net_sales_yesterday": float(r["rows"][0]["net_sales"]),
                      "net_sales_prev_day": float(r["compare_rows"][0]["net_sales"])}
    return _REF[kind]


def pct(xs, p):
    xs = sorted(x for x in xs if x is not None)
    return round(statistics.quantiles(xs, n=100, method="inclusive")[p - 1]) if len(xs) > 1 else (xs[0] if xs else None)


def latest_run(tid):
    row = psql(f"select id||'|'||status||'|'||coalesce(mission_id,'') from runs where thread_id='{tid}' "
               "order by created_at desc limit 1")
    return (row.split("|") + ["", ""])[:3] if row else ["", "", ""]


def run(i, item, out_dir, tid):
    q = item["q"]
    prev_rid = latest_run(tid)[0]
    t0 = time.time()
    req("POST", f"/threads/{tid}/messages", {"parts": [{"type": "TEXT", "content": q}]})
    rid, st, mid = "", "", ""
    while time.time() - t0 < 900:
        time.sleep(4)
        rid, st, mid = latest_run(tid)
        if rid and rid != prev_rid and st in ("COMPLETED", "FAILED", "CANCELLED"):
            break
    res = json.loads(psql(f"select coalesce(result_json::text,'{{}}') from missions where mission_id='{mid}'") or "{}")
    tr = res.get("trace") or {}
    revisions = len((tr.get("validation") or {}).get("revisions") or [])
    retries = int(psql(f"select count(*) from run_events where run_id='{rid}' and event_type='agent.retrying'") or 0)
    steps = tr.get("steps") or []
    calls = tool_calls(steps)
    answer = res.get("final_response") or ""
    checks = [check(answer, revisions, c) for c in item.get("checks") or []]
    checks = [{"ok": ok, "what": what} for ok, what in checks]
    if item.get("tool_checks"):
        if calls:
            turn = {"tools": calls, "today": time.strftime("%Y-%m-%d")}
            checks += [{"ok": bool(fn(turn)), "what": f"tool: {label}"} for label, fn in agent6_checks.CHECKS[item["tool_checks"]]]
        else:
            checks += [{"ok": None, "what": "tool checks skipped: trace.steps carries no tool calls (pre-slim-loop system)"}]
    if item.get("reference"):
        ref = reference(item["reference"])
        for k, ok in agent6_checks.q1_reference_checks(answer, numbers(answer), ref).items():
            checks.append({"ok": ok, "what": f"reference {k} ({ref})"})
    stats = tr.get("stats") or {}
    out = {
        "n": i + 1, "aspect": item.get("aspect"), "q": q, "thread": tid, "mission": mid, "run_status": st,
        "status": res.get("status"), "wall_s": round(time.time() - t0), "elapsed_s": tr.get("elapsed_seconds"),
        "revisions": revisions, "retries": retries, "tool_calls": len(calls), "llm_calls": stats.get("llm_calls"),
        "grounding": tr.get("grounding"), "tools": [c["name"] for c in calls],
        "failed_tools": [c["name"] for c in calls if not c["ok"]], "checks": checks, "answer": answer,
    }
    _write_json(os.path.join(out_dir, f"r{i + 1:02d}.json"), out)
    verdict = "FAIL" if any(c["ok"] is False for c in out["checks"]) else \
        ("PASS" if any(c["ok"] for c in out["checks"]) else "n/a")
    print(f"{i + 1:>2} {out['status']:<10} {out['wall_s']:>4}s tools={len(calls)} rev={revisions} retry={retries} "
          f"checks={verdict} {q[:70]}", flush=True)
    return out


def run_group(idx_list, items, out_dir):
    tid = req("POST", "/threads", {"title": f"REGRESSION Q{idx_list[0] + 1}", "metadata": {"golden": True}})["id"]
    return [run(i, items[i], out_dir, tid) for i in idx_list]


def main():
    with open(sys.argv[1]) as f:
        items = json.load(f)
    out_dir = sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    conc = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    sel = list(range(len(items)))
    if len(sys.argv) > 4 and sys.argv[4].startswith("only="):
        sel = [int(x) - 1 for x in sys.argv[4][5:].split(",")]
    groups, order = {}, []
    for i in sel:
        key = items[i].get("session") or f"#{i}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(i)
    with cf.ThreadPoolExecutor(conc) as ex:
        outs = [o for g in ex.map(lambda k: run_group(groups[k], items, out_dir), order) for o in g]
    walls = [o["wall_s"] for o in outs]
    scored = [o for o in outs if any(c["ok"] is not None for c in o["checks"])]  # None = n/a, never a pass
    failed_checks = [o["n"] for o in scored if any(c["ok"] is False for c in o["checks"])]
    summary = {"items": len(outs), "run_completed": sum(o["run_status"] == "COMPLETED" for o in outs),
               "checked_items": len(scored), "checked_pass": len(scored) - len(failed_checks),
               "unchecked_items": [o["n"] for o in outs if o not in scored], "checks_failed_items": failed_checks,
               "wall_p50_s": pct(walls, 50), "wall_p90_s": pct(walls, 90), "concurrency": conc,
               "revisions": sum(o["revisions"] for o in outs), "retries": sum(o["retries"] for o in outs)}
    _write_json(os.path.join(out_dir, "summary.json"), summary)
    print("SUMMARY", json.dumps(summary))


if __name__ == "__main__":
    main()
