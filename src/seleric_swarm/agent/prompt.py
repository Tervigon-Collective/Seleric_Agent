"""The single reasoning loop's system prompt.

Assembled from markdown modules under prompt_library/ (see prompt_modules.py): behaviour + query-type playbooks, the Core MCP server's own instructions (data rules), answer rules, per-domain notes gated on the catalogue, and a compact catalogue index.
The calendar is last so the long stable prefix is byte-identical across turns (prompt caching).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from seleric_swarm.agent import prompt_modules as pm
from seleric_swarm.agent import prompt_slim
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot


def _working_rules() -> str:
    """Identity, then the generic rules, the helper tools, and one playbook per query type (see prompt_library/)."""
    bullets = [m.body for m in (*pm.folder("behaviour"), *pm.folder("playbooks")) if m.name not in ("identity", "answer_rules", "analysis_report")]
    return f"{pm.one('behaviour', 'identity')}\n\nHow to work:\n" + "\n".join(bullets) + "\n"


PREAMBLE = _working_rules()
ANSWER_RULES = pm.one("behaviour", "answer_rules")
ANALYSIS_RULES = pm.one("behaviour", "analysis_report")


def metric_index(catalogue: CatalogueSnapshot) -> str:
    """Compact catalogue snapshot: per view its date axis, what it is for (the catalogue's description), then its metric ids (labels when they differ).

    Dimensions are left out on purpose: catalogue_search_metrics returns them per metric, and listing every view's
    dimensions was a third of this snapshot (the A/B found no quality difference without them)."""
    views: dict[str, dict[str, set[str] | list[str]]] = {}
    for m in sorted(catalogue.metrics, key=lambda m: m.id):
        v = views.setdefault(m.view or "?", {"axis": set(), "metrics": []})
        _, found, rest = str((m.raw or {}).get("description") or "").partition("Date axis: ")
        axis = "".join(ch for ch in rest.split(" ")[0].split(";")[0].split(",")[0] if ch.isalnum() or ch == "_") if found else ""
        if axis:
            v["axis"].add(axis)  # type: ignore[union-attr]
        label = (m.label or "").strip()
        v["metrics"].append(m.id if label.lower().replace(" ", "_") in ("", m.id) else f"{m.id} ({label})")  # type: ignore[union-attr]
    notes = dict(catalogue.view_descriptions)
    return "\n".join(
        f"## {name} [date axis: {', '.join(sorted(v['axis'])) or '?'}]\n"
        + (f"about: {notes[name][:400]}\n" if notes.get(name) else "")
        + f"metrics: {'; '.join(v['metrics'])}"
        for name, v in sorted(views.items())
    )


def calendar(today: date, now: datetime | None = None) -> str:
    """Deterministic date facts so the model never does calendar arithmetic (changes once a day)."""
    d = timedelta
    mon = today - d(days=today.weekday())

    def rng(a: date, b: date) -> str:
        return f"{a:%Y-%m-%d}..{b:%Y-%m-%d}"

    first_of_month = today.replace(day=1)
    last_month_end = first_of_month - d(days=1)
    zone = getattr(now.tzinfo, "key", None) or (now.tzname() if now else None) or "UTC" if now else "Asia/Kolkata"
    clock = f" It is {now:%H:%M} ({zone}): ask for today with period today; the tools cut the comparison window at the same hour, so today needs no caveat." if now else ""
    return (
        f"Today is {today:%Y-%m-%d} ({today:%A}), timezone {zone}.{clock} "
        f"Yesterday: {today - d(days=1):%Y-%m-%d} ({today - d(days=1):%A}).\n"
        f"This week so far (Mon-today): {rng(mon, today)}. Last week (Mon-Sun): {rng(mon - d(days=7), mon - d(days=1))}. "
        f"Same weekdays last week: {rng(mon - d(days=7), today - d(days=7))}.\n"
        f"Last 7 complete days: {rng(today - d(days=7), today - d(days=1))}; the 7 days before: {rng(today - d(days=14), today - d(days=8))}.\n"
        f"Month to date: {rng(first_of_month, today)}. Last month: {rng(last_month_end.replace(day=1), last_month_end)}."
    )


SERVER_GUIDE_HEADER = (
    "=== Server guide (Core MCP instructions: data rules) ===\n"
    "Where it differs from the Answer rules below on answer style or on arithmetic, the Answer rules win "
    "(figures computed with run_python are allowed)."
)


def system_prompt(catalogue: CatalogueSnapshot, now: datetime, server_guide: str = "") -> str:
    index = metric_index(catalogue)
    guide_text = prompt_slim.slim_guide(server_guide)
    guide = f"\n{SERVER_GUIDE_HEADER}\n{guide_text.strip()}\n" if guide_text.strip() else ""
    snapshot = f"\n\n=== Catalogue snapshot (metric ids by view) ===\n{index}" if index else ""
    notes = pm.domains_for({m.view for m in catalogue.metrics})  # only the domains this deployment's catalogue serves
    domains = f"\n\n=== Domain notes (what the catalogue text does not say) ===\n{notes}" if notes else ""
    return f"{PREAMBLE}{guide}\n{ANSWER_RULES}\n\n{ANALYSIS_RULES}{domains}{snapshot}\n\n=== Calendar ===\n{calendar(now.date(), now)}"
