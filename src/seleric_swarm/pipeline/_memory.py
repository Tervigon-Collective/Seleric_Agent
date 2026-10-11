"""Memory for the v2 path (M5.2): the previous turn's spec and per-brand defaults."""

from __future__ import annotations

import functools
import logging
from collections.abc import Mapping
from typing import Any

import yaml
from pydantic import ValidationError

from seleric_swarm.contracts.pipeline import QuestionDraft, QuestionSpec, WindowExpr, WindowSlot
from seleric_swarm.paths import repo_root

_log = logging.getLogger("seleric.pipeline.memory")


def prior_spec(turn_record: Mapping[str, Any] | None) -> QuestionSpec | None:
    """The spec the previous turn answered (stored as ``v2_spec`` on the turn record)."""
    raw = (turn_record or {}).get("v2_spec")
    if not isinstance(raw, dict):
        return None
    try:
        return QuestionSpec.model_validate(raw)
    except ValidationError:
        return None


@functools.cache
def brand_prefs(path: str | None = None) -> dict[str, dict[str, Any]]:
    p = repo_root() / "config" / "brand_prefs.yaml" if path is None else repo_root() / path
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    brands = data.get("brands") or {}
    return {str(k): v for k, v in brands.items() if isinstance(v, dict)}


def apply_brand_defaults(draft: QuestionDraft, prefs: Mapping[str, Any] | None) -> tuple[QuestionDraft, tuple[str, ...]]:
    """Fill what the question left open from the brand's defaults; each fill is an assumption."""
    if not prefs or draft.kind not in ("analysis", "exploration"):
        return draft, ()
    notes: list[str] = []
    window = prefs.get("default_window")
    if not draft.windows and not draft.names_period and isinstance(window, dict):
        try:
            expr = WindowExpr.model_validate(window)
        except ValidationError:
            _log.warning("brand_default_window_invalid", extra={"window": window})
        else:
            draft = draft.model_copy(update={"windows": (WindowSlot(role="event", expr=expr, source_span=""),)})
            notes.append("brand_default:window=" + ",".join(f"{k}={v}" for k, v in sorted(window.items())))
    return draft, tuple(notes)


def with_assumptions(spec: QuestionSpec, notes: tuple[str, ...]) -> QuestionSpec:
    return spec.model_copy(update={"assumptions": (*spec.assumptions, *notes)}) if notes else spec
