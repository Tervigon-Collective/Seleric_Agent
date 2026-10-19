"""Aggregate a prompt A/B: python3 aggregate.py <ab_root> [judgments.json]  (ab_root holds <run>/<variant>/r*.json)."""
import glob
import json
import statistics as st
import subprocess
import sys
from collections import defaultdict

root = sys.argv[1]
judg = json.load(open(sys.argv[2])) if len(sys.argv) > 2 else {"absolute": {}, "pairwise": {}}


def psql(sql):
    return subprocess.run(["docker", "exec", "-i", "seleric_agent-postgres-1", "psql", "-U", "seleric", "-d", "seleric_swarm",
                           "-At", "-F|", "-c", sql], capture_output=True, text=True).stdout.strip()


runs = defaultdict(list)
for f in sorted(glob.glob(f"{root}/*/*/r*.json")):
    run, variant = f.split("/")[-3], f.split("/")[-2]
    r = json.load(open(f)); r["_run"], r["_variant"] = run, variant
    runs[variant].append(r)
ids = ",".join(f"'{r['mission']}'" for rs in runs.values() for r in rs if r.get("mission"))
stats = {}
for line in psql(f"""select m.mission_id, (m.result_json->'trace'->'stats'->'llm_steps'->0->>'prompt_tokens')::int,
 (select sum((x->>'prompt_tokens')::int) from jsonb_array_elements(m.result_json->'trace'->'stats'->'llm_steps') x),
 (select count(*) from jsonb_array_elements(m.result_json->'trace'->'stats'->'llm_steps') x where x->>'model' like 'gpt-4o%'),
 (select count(*) from jsonb_array_elements(m.result_json->'trace'->'stats'->'llm_steps') x)
 from missions m where m.mission_id in ({ids})""").split("\n"):
    if line:
        mid, p1, pt, fb, n = line.split("|")
        stats[mid] = (int(p1 or 0), int(pt or 0), int(fb), int(n))


def pct(x, q):
    x = sorted(x)
    return x[min(len(x) - 1, int(len(x) * q))] if x else 0


order = [v for v in ("base", "slim", "lean", "lean2", "noidx") if v in runs]
rows = []
for v in order:
    rs = runs[v]
    walls = [r["wall_s"] for r in rs]
    checked = [r for r in rs if r.get("checks") or r.get("tool_checks")]
    chk_ok = sum(all(c["ok"] for c in r["checks"] if c.get("ok") is not None) for r in checked if r.get("checks"))
    chk_n = sum(1 for r in checked if r.get("checks"))
    unmatched = sum(len(r["grounding"].get("unmatched") or []) for r in rs)
    mis = sum(len(r["grounding"].get("misbound") or []) for r in rs)
    S = [stats[r["mission"]] for r in rs if r.get("mission") in stats]
    ab = [judg["absolute"].get(f"{r['_run']}|{v}|{r['n']}") for r in rs]
    ab = [a for a in ab if a and "depth" in a]
    row = {
        "variant": v, "runs": len(rs), "completed": sum(r["status"] == "completed" for r in rs), "partial": sum(r["status"] == "partial" for r in rs),
        "failed": sum(r["status"] not in ("completed", "partial") for r in rs), "checks": f"{chk_ok}/{chk_n}",
        "unmatched_figs": unmatched, "misbound": mis, "revisions": sum(r.get("revisions", 0) for r in rs),
        "wall_p50": pct(walls, .5), "wall_p90": pct(walls, .9), "wall_mean": round(st.mean(walls), 1),
        "step1_tok": round(st.mean(s[0] for s in S)) if S else 0, "prompt_tok_total": round(st.mean(s[1] for s in S)) if S else 0,
        "llm_calls": round(st.mean(r["llm_calls"] for r in rs), 1), "tool_calls": round(st.mean(r["tool_calls"] for r in rs), 1),
        "fallback_steps": f"{sum(s[2] for s in S)}/{sum(s[3] for s in S)}",
        "answer_words": round(st.mean(len(r["answer"].split()) for r in rs)),
    }
    for d in ("depth", "user_facing", "completeness", "honesty"):
        row[d] = round(st.mean(a[d] for a in ab if isinstance(a.get(d), (int, float))), 2) if ab else None
    rows.append(row)
cols = list(rows[0])
print("\t".join(cols))
for r in rows:
    print("\t".join(str(r[c]) for c in cols))
pw = defaultdict(lambda: defaultdict(int))
for k, res in judg["pairwise"].items():
    run, variant, n = k.split("|")
    if "winner" in res:
        pw[variant][res["winner"]] += 1
print("\npairwise vs base (judge, blind, position randomised):")
for v in order[1:]:
    t = pw[v]
    print(f"{v:6s} variant wins {t['variant']}  base wins {t['base']}  ties {t['tie']}")
