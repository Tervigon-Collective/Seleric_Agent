"""Deterministic India calendar covariates — the only known-future features."""

from __future__ import annotations

import calendar as _cal
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from seleric_swarm.paths import repo_root

CALENDAR_PREFIX = "calendar."


class FestivalEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    dates: list[str] = Field(default_factory=list)


class PaydayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    month_start_days: list[int] = Field(default_factory=lambda: [1, 2])
    month_end_days: list[int] = Field(default_factory=lambda: [0, -1])


class CalendarConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    timezone: str = "Asia/Kolkata"
    payday: PaydayConfig = Field(default_factory=PaydayConfig)
    month_end_days: list[int] = Field(default_factory=lambda: [0])
    festivals: list[FestivalEntry] = Field(default_factory=list)
    pre_festival_days: int = 3


@lru_cache(maxsize=1)
def load_calendar_config(path: str | None = None) -> CalendarConfig:
    p = Path(path) if path else repo_root() / "config" / "calendar_in.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return CalendarConfig.model_validate(data)


def clear_calendar_cache() -> None:
    load_calendar_config.cache_clear()


def festival_date_set(cfg: CalendarConfig | None = None) -> set[date]:
    cfg = cfg or load_calendar_config()
    out: set[date] = set()
    for entry in cfg.festivals:
        for iso in entry.dates:
            try:
                out.add(date.fromisoformat(iso[:10]))
            except ValueError:
                continue
    return out


def pre_festival_date_set(cfg: CalendarConfig | None = None) -> set[date]:
    cfg = cfg or load_calendar_config()
    festivals = festival_date_set(cfg)
    out: set[date] = set()
    for d in festivals:
        for i in range(1, max(1, cfg.pre_festival_days) + 1):
            out.add(d - timedelta(days=i))
    return out - festivals


def _resolve_month_day(year: int, month: int, offset: int) -> int:
    last = _cal.monthrange(year, month)[1]
    if offset <= 0:
        return last + offset  # 0 → last, -1 → second-to-last
    return offset


def is_payday(d: date, cfg: CalendarConfig | None = None) -> bool:
    cfg = cfg or load_calendar_config()
    last = _cal.monthrange(d.year, d.month)[1]
    if d.day in cfg.payday.month_start_days:
        return True
    for offset in cfg.payday.month_end_days:
        if d.day == _resolve_month_day(d.year, d.month, offset):
            return True
    return d.day == last and 0 in cfg.payday.month_end_days


def is_month_end(d: date, cfg: CalendarConfig | None = None) -> bool:
    cfg = cfg or load_calendar_config()
    for offset in cfg.month_end_days:
        if d.day == _resolve_month_day(d.year, d.month, offset):
            return True
    return False


def generate_calendar_features(
    dates: list[date],
    *,
    names: list[str] | None = None,
    cfg: CalendarConfig | None = None,
) -> dict[str, list[float]]:
    """Build calendar.* series for the given dates.

    Supported names: calendar.dow, calendar.dom, calendar.month_end,
    calendar.payday, calendar.festival, calendar.pre_festival.
    """
    cfg = cfg or load_calendar_config()
    wanted = names or [
        "calendar.dow",
        "calendar.dom",
        "calendar.month_end",
        "calendar.payday",
        "calendar.festival",
        "calendar.pre_festival",
    ]
    festivals = festival_date_set(cfg)
    pre = pre_festival_date_set(cfg)
    out: dict[str, list[float]] = {n: [] for n in wanted}
    for d in dates:
        if "calendar.dow" in out:
            out["calendar.dow"].append(float(d.weekday()))  # Mon=0
        if "calendar.dom" in out:
            out["calendar.dom"].append(float(d.day))
        if "calendar.month_end" in out:
            out["calendar.month_end"].append(1.0 if is_month_end(d, cfg) else 0.0)
        if "calendar.payday" in out:
            out["calendar.payday"].append(1.0 if is_payday(d, cfg) else 0.0)
        if "calendar.festival" in out:
            out["calendar.festival"].append(1.0 if d in festivals else 0.0)
        if "calendar.pre_festival" in out:
            out["calendar.pre_festival"].append(1.0 if d in pre else 0.0)
    return out


def split_past_future(
    features: dict[str, list[float]],
    *,
    context_len: int,
    horizon_len: int,
) -> dict[str, dict[str, list[float | None]]]:
    """Map full-index calendar series into {past, future} for Chronos known-future."""
    result: dict[str, dict[str, list[float | None]]] = {}
    for name, values in features.items():
        if len(values) < context_len + horizon_len:
            raise ValueError(
                f"{name}: need {context_len + horizon_len} values, got {len(values)}"
            )
        past = [float(v) for v in values[:context_len]]
        future = [float(v) for v in values[context_len : context_len + horizon_len]]
        result[name] = {"past": past, "future": future}  # type: ignore[assignment]
    return result


def feature_names_from_policy(known_future: list[str] | None) -> list[str]:
    names = list(known_future or [])
    if not names:
        return [
            "calendar.dow",
            "calendar.festival",
            "calendar.payday",
        ]
    return [n for n in names if n.startswith(CALENDAR_PREFIX)]
