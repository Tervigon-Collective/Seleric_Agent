"""Cross-check a fixture's daily totals against ClickHouse (plan P0.2, D-17).

usage: python3 crosscheck.py <fixture_id>

Each check pairs a fixture metric with the serve-table SQL that defines it, over the fixture's whole window.
The metric's own definition stays in the semantic layer; these are independent re-derivations of three anchor
totals (paid spend, impressions, placed orders, contribution margin). Exit code 1 if any day differs by more than the tolerance.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CH = os.path.join(HERE, "..", "regress", "ch.sh")
REL_TOL = 1e-6  # relative, with a floor of 1 currency unit
RECENT_DAYS, RECENT_TOL = 2, 0.01  # ClickHouse is live: the newest days are still being finalised after the snapshot

CHECKS = {
    "ad_spend": "SELECT toString(report_date) d, sum(spend) v FROM serve.fct_paid_media_daily WHERE brand_id={b} AND report_date BETWEEN '{s}' AND '{e}' GROUP BY d",
    "impressions": "SELECT toString(report_date) d, sum(impressions) v FROM serve.fct_paid_media_daily WHERE brand_id={b} AND report_date BETWEEN '{s}' AND '{e}' GROUP BY d",
    "contribution_margin": "SELECT toString(order_date) d, sum(net_sales - net_cogs) v FROM serve.order_pnl_daily WHERE brand_id={b} AND order_date BETWEEN '{s}' AND '{e}' GROUP BY d",
    "orders": "SELECT toString(order_date) d, countIf(is_commerce_placement_order) v FROM serve.commerce_orders WHERE brand_id={b} AND NOT is_test AND order_date BETWEEN '{s}' AND '{e}' GROUP BY d",
}


def ch(sql: str) -> dict[str, float]:
    out = subprocess.run(["bash", CH, sql], capture_output=True, text=True, check=True).stdout.splitlines()
    return {k: float(v) for k, v in (line.split("\t") for line in out[1:] if line)}


def main(fixture_id: str) -> int:
    with open(os.path.join(HERE, "fixtures", f"{fixture_id}.json")) as fh:
        fx = json.load(fh)
    bad = 0
    for metric, sql in CHECKS.items():
        truth = ch(sql.format(b=fx["brand"], s=fx["start"], e=fx["end"]))
        got = fx["series"][metric]
        days = sorted(set(truth) | set(got))
        recent = set(days[-RECENT_DAYS:])
        diffs = [(d, got.get(d, 0.0), truth.get(d, 0.0)) for d in days
                 if abs(got.get(d, 0.0) - truth.get(d, 0.0)) > (RECENT_TOL if d in recent else REL_TOL) * max(1.0, abs(truth.get(d, 0.0)))]
        bad += len(diffs)
        print(f"{metric}: {len(days)} days, {len(diffs)} differ" + (f", first {diffs[0]}" if diffs else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
