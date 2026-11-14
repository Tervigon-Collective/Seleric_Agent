"""Entity audit — the figures behind "which campaigns / products / channels work and which leak money", computed in code.

An analysis answer needs derived facts a table cannot show: the portfolio's return on spend, the share of spend that bought
nothing, who is best and worst *with enough orders to say so*, attention that does not convert. Left to the model these were
either missing (a 30.8% waste share sat in the evidence unread) or wrong (a hand-written script summed two periods).
This module computes them from the rows of results the turn already fetched; it never fetches and never reads wording.

Pure functions plus one thin tool (registered in ``agent/local_tools.py`` next to ``show_table``). The text it returns is
written for the model; its figures join the turn's grounding pool, so quoting them is verified.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from seleric_swarm.toolsets import policy_config as P

BLANK = "—"
UNSET = "(not set)"  # the row for spend no entity carries: it is not an entity and cannot be judged as one


@dataclass
class Entity:
    name: str
    spend: float
    returned: float | None  # the return measure (revenue); None = the view had no row for it (unknown, not zero)
    orders: float | None
    signal: float | None = None


@dataclass
class Roles:
    spend: str
    returned: str | None
    orders: str | None
    signal: str | None
    roas: str | None = None  # a return-on-spend ratio standing in for a missing revenue measure (return = ratio x spend)


def pick_roles(measures: list[str]) -> Roles | None:
    """The first catalogue measure of each role the fetched result carries; None without a spend measure."""

    def first(preferred: tuple[str, ...]) -> str | None:
        return next((m for m in preferred if m in measures), None)

    spend = first(P.AUDIT_SPEND_MEASURES)
    if spend is None:
        return None
    returned = first(P.AUDIT_RETURN_MEASURES)
    return Roles(spend=spend, returned=returned, orders=first(P.AUDIT_ORDER_MEASURES), signal=first(P.AUDIT_SIGNAL_MEASURES),
                 roas=None if returned else first(P.AUDIT_ROAS_MEASURES))


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def entities_of(rows: list[dict[str, Any]], entity_col: str, roles: Roles) -> list[Entity]:
    out: list[Entity] = []
    for row in rows:
        spend = _num(row.get(roles.spend))
        if spend is None or spend <= 0:
            continue  # an entity that spent nothing is not part of a spend audit
        returned = _num(row.get(roles.returned)) if roles.returned else None
        if returned is None and roles.roas:
            ratio = _num(row.get(roles.roas))
            returned = ratio * spend if ratio is not None else None
        out.append(Entity(
            name=str(row.get(entity_col) or UNSET), spend=spend,
            returned=returned,
            orders=_num(row.get(roles.orders)) if roles.orders else None,
            signal=_num(row.get(roles.signal)) if roles.signal else None))
    return out


def _money(v: float, symbol: str) -> str:
    whole = round(abs(v))
    digits = str(whole)
    if len(digits) > 3:
        head, tail, groups = digits[:-3], digits[-3:], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join([*groups, tail])
    return f"{'-' if v < 0 else ''}{symbol}{digits}"


@dataclass
class Audit:
    lines: list[str] = field(default_factory=list)
    facts: dict[str, float] = field(default_factory=dict)

    def text(self) -> str:
        return "\n".join(self.lines)


def audit(entities: list[Entity], *, labels: dict[str, str], roles: Roles, symbol: str = "₹", cut_at: int | None = None) -> Audit:
    """The facts, in business language. ``labels`` maps measure ids to the names the answer should use. ``cut_at``: the spend
    result returned exactly its row limit, so more entities exist than it holds."""
    a = Audit()
    unattributed = [e for e in entities if e.name == UNSET]
    entities = [e for e in entities if e.name != UNSET]
    n = len(entities)
    total_spend = sum(e.spend for e in entities)
    if not entities:
        a.lines.append("ENTITY AUDIT: every row of spend is unattributed (no entity attached), so nothing can be ranked.")
        return a
    spend_label = labels.get(roles.spend, roles.spend)
    ret_label = (labels.get(roles.returned, roles.returned) if roles.returned else
                 f"{labels.get(roles.roas or '', 'return')} × spend" if roles.roas else None)
    a.lines.append(f"ENTITY AUDIT over {n} entities with spend (every figure below is computed from the fetched rows; keep each as given).")
    if cut_at:
        a.lines.append(f"COVERAGE: the spend result returned its limit of {cut_at} rows, so entities beyond the top {cut_at} are not in these "
                       "totals: the portfolio figures are a lower bound for the account, not its total; never quote them as the account's.")
    a.lines.append(f"PORTFOLIO: {spend_label} {_money(total_spend, symbol)} across the {n} entities")
    a.facts["total_spend"] = total_spend
    if unattributed:
        loose = sum(e.spend for e in unattributed)
        share = 100 * loose / (total_spend + loose)
        a.lines.append(f"UNATTRIBUTED: {_money(loose, symbol)} = {share:.1f}% of all spend has no entity attached (the row with no name). "
                       f"It is not an entity: it has no orders because nothing is attributed to it, so it is neither a winner nor a "
                       f"leak by itself; say it needs its source traced (campaign, platform), and keep it out of the rankings.")
        a.facts["unattributed_spend"], a.facts["unattributed_share_pct"] = loose, share

    known = [e for e in entities if e.returned is not None]
    if (roles.returned or roles.roas) and known:
        returned = sum(e.returned or 0.0 for e in known)
        spend_known = sum(e.spend for e in known)
        a.lines.append(f"PORTFOLIO RETURN: {ret_label} {_money(returned, symbol)} on {_money(spend_known, symbol)} spend by the "
                       f"{len(known)} entities that have a {ret_label} row = {returned / spend_known:.2f}x ({ret_label} ÷ {spend_label}).")
        a.facts["return_on_spend"] = returned / spend_known
    if roles.orders:
        orders = sum(e.orders or 0.0 for e in entities)
        if orders > 0:
            a.lines.append(f"ORDERS: {orders:,.0f} in total; {spend_label} per order {_money(total_spend / orders, symbol)}.")
            a.facts["spend_per_order"] = total_spend / orders

    # spend that bought nothing: no orders (and no return) — a row with no value is "no orders recorded", stated as such
    if roles.orders or roles.returned or roles.roas:
        idle = [e for e in entities if (e.orders or 0.0) == 0 and (e.returned or 0.0) == 0]
        if idle:
            idle_spend = sum(e.spend for e in idle)
            share = 100 * idle_spend / total_spend
            names = "; ".join(f"{e.name} {_money(e.spend, symbol)}"
                              for e in sorted(idle, key=lambda e: -e.spend)[:P.AUDIT_LEAK_LIST])
            a.lines.append(f"NO ORDERS: {len(idle)} of {n} entities recorded no orders and no {ret_label or 'return'}, and absorbed "
                           f"{_money(idle_spend, symbol)} = {share:.1f}% of spend. Largest, in order: {names}. "
                           f"No-orders-recorded is a finding about these rows, not missing data.")
            a.facts["idle_spend"], a.facts["idle_share_pct"], a.facts["idle_count"] = idle_spend, share, float(len(idle))
    top = sorted(entities, key=lambda e: -e.spend)[:P.AUDIT_TOP_N]
    if n > P.AUDIT_TOP_N:
        top_share = 100 * sum(e.spend for e in top) / total_spend
        a.lines.append(f"CONCENTRATION: the {len(top)} largest spenders ({', '.join(e.name for e in top)}) carry {top_share:.1f}% of spend.")
        a.facts["top_share_pct"] = top_share

    if roles.returned or roles.roas:
        ranked = [e for e in entities if e.returned is not None and (e.orders or 0) > 0]
        solid = sorted((e for e in ranked if (e.orders or 0) >= P.AUDIT_MIN_ORDERS), key=lambda e: -(e.returned or 0) / e.spend)
        early = sorted((e for e in ranked if (e.orders or 0) < P.AUDIT_MIN_ORDERS), key=lambda e: -(e.returned or 0) / e.spend)

        def row(e: Entity) -> str:
            return (f"{e.name}: {ret_label} {_money(e.returned or 0, symbol)} on {_money(e.spend, symbol)} = "
                    f"{(e.returned or 0) / e.spend:.2f}x, {e.orders:,.0f} orders")

        if solid:
            a.lines.append(f"EVIDENCED (≥{P.AUDIT_MIN_ORDERS} orders) best: " + "; ".join(row(e) for e in solid[:P.AUDIT_TOP_N]) + ".")
            losers = [e for e in reversed(solid) if (e.returned or 0) < e.spend][:P.AUDIT_TOP_N]
            if losers:
                a.lines.append(f"EVIDENCED (≥{P.AUDIT_MIN_ORDERS} orders) returning less than spend: " + "; ".join(
                    f"{row(e)}, shortfall {_money(e.spend - (e.returned or 0), symbol)}" for e in losers) + ".")
        if early:
            a.lines.append(f"EARLY SIGNAL (fewer than {P.AUDIT_MIN_ORDERS} orders each: too few to justify scaling or cutting, say so): "
                           + "; ".join(row(e) for e in early[:P.AUDIT_TOP_N]) + ".")
        if not solid and not early:
            a.lines.append("No entity has both orders and a return row, so no best/worst ranking is possible.")

    if roles.signal and roles.orders:
        have = [e for e in entities if e.signal is not None]
        if len(have) >= 4:
            median = statistics.median(e.signal for e in have if e.signal is not None)
            median_spend = statistics.median(e.spend for e in have)
            odd = sorted((e for e in have if (e.orders or 0) == 0 and (e.signal or 0) > median and e.spend >= median_spend),
                         key=lambda e: -e.spend)[:P.AUDIT_TOP_N]
            if odd:
                sig = labels.get(roles.signal or "", roles.signal)
                a.lines.append(f"ATTENTION WITHOUT ORDERS ({sig} above the group median, spend above the median, no orders): "
                               + "; ".join(f"{e.name} ({_money(e.spend, symbol)} spend)" for e in odd)
                               + ". Worth examining the click quality or the landing page; this is an observation, not a cause.")
    a.lines.append("USE: state these as findings in the answer; do not claim why from them alone, and keep the sample-size wording for EARLY SIGNAL.")
    return a
