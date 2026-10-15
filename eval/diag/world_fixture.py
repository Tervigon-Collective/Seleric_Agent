"""Load a world fixture written by ``snapshot.py`` and verify it (plan P0.2).

The sha256 covers series, segments and definitions. A case that pins ``world.sha256`` fails to load against
a fixture that has been regenerated since, so a score is never computed on silently different data.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent / "fixtures"

Series = dict[date, float]


class FixtureError(ValueError):
    pass


@dataclass(frozen=True)
class Fixture:
    id: str
    brand: int
    start: date
    end: date
    sha256: str
    catalogue_version: str
    graph_sha256: str
    series: dict[str, Series]
    segments: dict[str, dict[str, dict[str, Series]]]  # metric -> dimension -> segment -> day -> value
    definitions: dict[str, dict[str, Any]]
    unavailable: dict[str, str]


def fingerprint(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _days(raw: dict[str, float]) -> Series:
    return {date.fromisoformat(d): float(v) for d, v in raw.items()}


def load_fixture(fixture_id: str, *, pinned_sha256: str = "", root: Path = FIXTURES) -> Fixture:
    path = root / f"{fixture_id}.json"
    if not path.exists():
        raise FixtureError(f"fixture {fixture_id!r} not found at {path}")
    raw = json.loads(path.read_text())
    data = {k: raw[k] for k in ("series", "segments", "definitions")}
    if fingerprint(data) != raw["sha256"]:
        raise FixtureError(f"{fixture_id}: data does not match its recorded sha256 (file edited or corrupt)")
    if pinned_sha256 and pinned_sha256 != raw["sha256"]:
        raise FixtureError(f"{fixture_id}: case pins {pinned_sha256[:12]} but the fixture is {raw['sha256'][:12]}")
    return Fixture(
        raw["id"], int(raw["brand"]), date.fromisoformat(raw["start"]), date.fromisoformat(raw["end"]), raw["sha256"],
        str(raw.get("catalogue_version") or ""), str(raw.get("graph_sha256") or ""),
        {m: _days(s) for m, s in raw["series"].items()},
        {m: {d: {seg: _days(s) for seg, s in segs.items()} for d, segs in dims.items()} for m, dims in raw["segments"].items()},
        raw["definitions"], dict(raw.get("unavailable") or {}),
    )
