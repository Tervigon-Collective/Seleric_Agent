"""Evaluate due rules on a snapshot. Pure: no I/O, so the same code runs live, in the dry-run API and in the backtest."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, replace

from seleric_swarm.services.live_monitor.core import conditions
from seleric_swarm.services.live_monitor.core.context import RuleContext
from seleric_swarm.services.live_monitor.core.model import SEVERITY_RANK, Entity, Finding, Hit
from seleric_swarm.services.live_monitor.core.registry import RULES, RuleDef

log = logging.getLogger(__name__)


def finding_id(brand_id: int, platform: str, rule_id: str, entity_id: str) -> str:
    return "live:" + hashlib.sha1(f"{brand_id}|{platform}|{rule_id}|{entity_id}".encode()).hexdigest()[:24]


@dataclass(frozen=True)
class Evaluation:
    findings: list[Finding]
    evaluated_rules: set[str]  # rule ids that ran (their open findings without a hit resolve)
    errors: list[str]


def _custom_rule(cr) -> RuleDef:
    from seleric_swarm.services.live_monitor.core.registry import Params

    def fn(ctx: RuleContext, e: Entity, _p: Params) -> Hit | None:
        ok, evidence = conditions.evaluate(cr.conditions, cr.match, e, ctx)
        if not ok:
            return None
        return Hit(cr.severity, cr.title, {**evidence, **({"hint": cr.explain_hint} if cr.explain_hint else {})},
                   actions=tuple(cr.actions))

    return RuleDef(f"custom.{cr.id}", cr.title, cr.explain_hint or "Rule written in Seleric Agent", "custom", (cr.platform,),
                   (cr.level,), "5m", Params, fn, tuple(cr.actions), "custom rule")


def active_rules(cfg, platform: str, due: set[str] | None = None) -> list[tuple[RuleDef, object]]:
    """(rule, params) for enabled rules on ``platform``; ``due`` limits built-ins to the given cadences."""
    out: list[tuple[RuleDef, object]] = []
    for rid, rule in RULES.items():
        if platform not in rule.platforms or not cfg.rule_setting(rid).enabled:
            continue
        if due is not None and rule.cadence not in due:
            continue
        out.append((rule, cfg.params_for(rid)))
    if due is None or "5m" in due:
        for cr in cfg.custom_rules:
            if cr.enabled and cr.platform == platform:
                out.append((_custom_rule(cr), None))
    return out


def evaluate(ctx: RuleContext, cfg, tenant, due: set[str] | None = None) -> Evaluation:
    snap = ctx.snapshot
    findings: list[Finding] = []
    evaluated: set[str] = set()
    errors: list[str] = []
    for rule, params in active_rules(cfg, snap.platform, due):
        evaluated.add(rule.id)
        setting = cfg.rule_setting(rule.id) if not rule.id.startswith("custom.") else None
        hits: list[tuple[Entity, Hit]] = []
        for e in snap.entities:
            if e.level not in rule.levels or (e.level != "account" and not _watchable(rule, e, snap)):
                continue
            try:
                hit = rule.fn(ctx, e, params)
            except Exception as exc:  # one broken rule must not stop the others
                log.exception("live rule %s failed on %s", rule.id, e.id)
                errors.append(f"{rule.id} on {e.id}: {exc}")
                continue
            if hit is None:
                continue
            if rule.efficiency and hit.severity != "info" and ctx.learning(e):
                hit = replace(hit, severity="info", evidence={**hit.evidence, "learning": True},
                              summary=hit.summary + " (Still learning: shown for information.)")
            if setting is not None and setting.severity:
                hit = replace(hit, severity=setting.severity)
            hits.append((e, hit))
        if rule.rollup:
            hits = _rollup(snap, hits)
        for e, hit in hits:
            findings.append(Finding(
                finding_id(tenant.brand_id, snap.platform, rule.id, e.id), tenant.tenant_id, tenant.brand_id, snap.platform,
                rule.id, rule.title, rule.tier, rule.playbook_ref, e, tuple(snap.path_names(e)), hit,
            ))
    findings = _supersede(findings)
    findings.sort(key=lambda f: (SEVERITY_RANK[f.hit.severity], -(f.hit.impact_inr or 0.0)))
    return Evaluation(findings, evaluated, errors)


def _supersede(findings: list[Finding]) -> list[Finding]:
    """Where a rule and one it supersedes both fire on an entity, keep the more severe (the superseding one on a tie)
    and note the other in its evidence."""
    by_key = {(f.entity.id, f.rule_id): i for i, f in enumerate(findings)}
    drop: set[int] = set()
    for i, f in enumerate(findings):
        rule = RULES.get(f.rule_id)
        for other_id in (rule.supersedes if rule else ()):
            j = by_key.get((f.entity.id, other_id))
            if j is None or j in drop or i in drop:
                continue
            keep, lose = (j, i) if SEVERITY_RANK[findings[j].hit.severity] < SEVERITY_RANK[f.hit.severity] else (i, j)
            k, gone = findings[keep], findings[lose]
            also = [*k.hit.evidence.get("also", []), {"rule": gone.rule_id, "summary": gone.hit.summary}]
            findings[keep] = replace(k, hit=replace(k.hit, evidence={**k.hit.evidence, "also": also}))
            drop.add(lose)
    return [f for k, f in enumerate(findings) if k not in drop]


def _watchable(rule: RuleDef, e: Entity, snap) -> bool:
    """Status rules look at non-delivering entities; every other rule only at ones that can spend."""
    return True if rule.id in ("status_not_active",) else snap.deliverable(e)


def _rollup(snap, hits: list[tuple[Entity, Hit]]) -> list[tuple[Entity, Hit]]:
    """Drop a hit when an ancestor of its entity also hit the same rule; the evidence names the children."""
    hit_ids = {e.id for e, _ in hits}
    kept: dict[str, tuple[Entity, Hit]] = {}
    children: dict[str, list[str]] = {}
    for e, h in hits:
        hit_ancestors = [a for a in snap.lineage(e)[1:] if a.id in hit_ids]
        if hit_ancestors:
            children.setdefault(hit_ancestors[-1].id, []).append(e.name)
        else:
            kept[e.id] = (e, h)
    out = []
    for eid, (e, h) in kept.items():
        if children.get(eid):
            h = replace(h, evidence={**h.evidence, "also_below": sorted(children[eid])[:10], "also_below_count": len(children[eid])})
        out.append((e, h))
    return out
