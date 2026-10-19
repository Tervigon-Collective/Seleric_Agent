"""test_agent's 6-question tool-selection CHECKS, ported as predicates over a turn's tool calls.

A turn is {"tools": [{"name", "args", "ok"}], "today": "YYYY-MM-DD"} built from the mission's trace.steps
(kind=tool_call / tool_return). Ported from /opt/seleric/test_agent/eval.py (synced 2026-10-13); regex replaced by
substring tests. Only meaningful for runs whose trace.steps carry the Core MCP tool names (the slim loop onward).
"""
import datetime
import json


def _calls(t):
    return [c for c in t["tools"] if c["ok"]]


def _dims(c):
    a = c["args"]
    return set(a.get("dimensions") or []) | set(a.get("target_dimensions") or []) | ({a["hierarchy"]} if a.get("hierarchy") else set())


def _filters(c):
    return json.dumps(c["args"].get("filters") or c["args"].get("additional_filters") or []).lower()


# Seleric's DoWhy engine measures a change against a reference window and decomposes it by component and segment
# itself, so a successful diagnose_metric_change counts as both a comparison and a decomposition of its metric.
DIAGNOSIS = "diagnose_metric_change"


def _diagnosed(t):
    return [c for c in _calls(t) if c["name"] == DIAGNOSIS]


def _measures(t):
    return {m for c in _calls(t) for m in c["args"].get("measures") or []} | \
        {c["args"]["metric_id"] for c in _diagnosed(t) if c["args"].get("metric_id")}


def _compares(t):
    return bool(_diagnosed(t)) or any(c["args"].get("compare_period") for c in _calls(t) if c["name"] == "metrics_query")


def _decomposed(t):
    return bool(_diagnosed(t)) or any(
        c["name"] == "metrics_drilldown" or (c["name"] == "metrics_query" and _dims(c) - {"report_date", "order_date"})
        for c in _calls(t))


def _week_windows(t):
    """A query covers last week (Mon-Sun) or the same weekdays last week."""
    today = datetime.date.fromisoformat(t["today"])
    d = datetime.timedelta
    mon = today - d(days=today.weekday())
    last_mon = str(mon - d(days=7))
    ok_ends = {str(mon - d(days=1)), str(today - d(days=7)), str(today - d(days=8))}
    for c in _calls(t):
        tr = c["args"].get("time_range") or {}
        if c["name"] == "metrics_query" and tr.get("start") == last_mon and tr.get("end") in ok_ends:
            return True
        if c["name"] == "metrics_query" and tr.get("start") == str(mon) and c["args"].get("compare_period") == "previous_period" \
                and (today - mon).days >= 6:
            return True
    return False


_PRODUCT = {"product_title", "basket_product_title", "sku", "product_type", "product"}
_CHANNEL = {"channel", "platform", "ad_platform", "sub_channel", "finance_channel", "traffic", "lt_channel"}


def _has(text, words):
    return any(w in text for w in words)


CHECKS = {
    "q1": [("measures the change with compare_period", _compares),
           ("decomposes it (drilldown or breakdown)", _decomposed),
           ("uses a sales metric", lambda t: bool(_measures(t) & {"net_sales", "total_sales", "gross_sales", "pnl_net_sales", "orders"}))],
    "q2": [("filters to Meta", lambda t: any("meta" in _filters(c) for c in _calls(t))),
           ("breaks down by campaign", lambda t: any(_dims(c) & {"campaign_name", "campaign_id", "campaign"} for c in _calls(t))),
           ("uses spend plus an outcome metric", lambda t: any("ad_spend" in m for m in _measures(t)) and len(_measures(t)) > 1)],  # any spend id counts (order_pnl_ad_spend too)
    "q3": [("product grain", lambda t: any(_dims(c) & _PRODUCT or
                                          any(m.startswith("product") for m in c["args"].get("measures") or []) for c in _calls(t))),
           ("profit/margin metric", lambda t: any(_has(m, ("profit", "margin", "contribution")) for m in _measures(t))),
           ("returns and ad cost covered", lambda t: bool(_measures(t) & {"product_net_profit", "product_pnl_net_profit"}) or
            any(_has(m, ("return", "refund", "ad_spend", "ad_cost")) for m in _measures(t)))],
    "q4": [("ROAS attempted or derived from its definition", lambda t: any(
        "roas" in json.dumps(c["args"]) for c in t["tools"]
        if c["name"] in ("metrics_query", "catalogue_get_metrics", "catalogue_get_metric", DIAGNOSIS))),
           ("net profit queried", lambda t: any("profit" in m for m in _measures(t))),
           ("compares periods", _compares),
           ("definitions fetched via catalogue", lambda t: any(c["name"].startswith("catalogue_get_metric") or c["name"] == "catalogue_resolve_concept" for c in _calls(t)))],
    "q5": [("compares against last week or same weekdays last week", _week_windows),
           ("compares periods", _compares),
           ("decomposes", _decomposed)],
    "q6": [("product breakdown", lambda t: any(_dims(c) & _PRODUCT or c["args"].get("hierarchy") == "product" for c in _calls(t))),
           ("channel breakdown", lambda t: any(_dims(c) & _CHANNEL for c in _calls(t))),
           ("reuses prior scope (drilldown or same window)", lambda t: any(c["name"] == "metrics_drilldown" for c in _calls(t)) or _week_windows(t))],
}


def q1_reference_checks(answer, numbers, ref):
    """Answer states yesterday's net sales and its comparison (within display rounding) and the right direction."""
    has = lambda v: any(abs(n - v) <= max(0.006 * v, 1) for n in numbers)
    rose = ref["net_sales_yesterday"] > ref["net_sales_prev_day"]
    words = set("".join(ch if ch.isalpha() else " " for ch in answer.lower()).split())
    up = {"rose", "increase", "increased", "up", "higher", "grew"}
    down = {"drop", "dropped", "decline", "declined", "fell", "down", "lower", "decrease", "decreased"}
    text = answer.lower()
    direction = bool(words & up) or any(p in text for p in ("did not drop", "didn't drop", "not drop", "no drop")) if rose \
        else bool(words & down)
    return {"has_yesterday_value": has(ref["net_sales_yesterday"]), "has_prev_value": has(ref["net_sales_prev_day"]), "direction_ok": direction}


def _day(value):
    return datetime.date.fromisoformat(str(value)[:10]) if value not in (None, "None", "") else None


def comparison_windows(calls):
    """(current, previous) inclusive date pairs the mission analysed, from its own diagnose_metric_change arguments.

    The previous window is the explicit compare window, else the equal-length period just before the event."""
    for c in calls:
        a = c["args"]
        if c["name"] == DIAGNOSIS and c["ok"] and _day(a.get("event_start")) and _day(a.get("event_end")):
            start, end = _day(a["event_start"]), _day(a["event_end"])
            end = min(end, datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).date() - datetime.timedelta(days=1))  # IST; today is partial; answers state the last full day
            if _day(a.get("compare_start")) and _day(a.get("compare_end")):
                return (start, end), (_day(a["compare_start"]), _day(a["compare_end"]))
            n = (end - start).days + 1
            return (start, end), (start - datetime.timedelta(days=n), start - datetime.timedelta(days=1))
    return None


def window_reference_checks(answer_numbers, windows, measures, query):
    """Each measure's total over the current and the previous window (queried directly) appears in the answer.

    A figure from the diagnosis's blended "usual" level or a per-day average would not match either total."""
    def has(v):
        tol = 0.01 if abs(v) < 10 else max(0.006 * abs(v), 1)
        return any(abs(n - v) <= tol for n in answer_numbers)

    out = {}
    for label, (a, b) in zip(("current", "previous"), windows, strict=True):
        for m in measures:
            v = query(m, str(a), str(b))
            out[f"{m} {label} {a}..{b} = {v:.4g}"] = has(v)
    return out
