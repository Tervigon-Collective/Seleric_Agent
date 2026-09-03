from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from seleric_swarm.paths import repo_root
from seleric_swarm.services.evidence import utc_now

METRIC_FIELD = {"metric.cac": "cac"}


class FixturePerformanceServer:
    capability = "performance.daily_cac"

    def __init__(self, fixture_path: str | Path) -> None:
        root = repo_root()
        path = Path(fixture_path)
        if not path.is_absolute():
            path = root / path
        self.path = path
        self._data = json.loads(path.read_text(encoding="utf-8"))

    def call(self, arguments: dict[str, Any]) -> dict[str, Any]:
        day = str(arguments.get("date") or "")
        metrics = list(arguments.get("metrics") or ["metric.cac"])
        days = self._data.get("days") or {}
        row = days.get(day)
        retrieved_at = utc_now()
        query_hash = sha256(json.dumps({"date": day, "metrics": metrics}, sort_keys=True).encode()).hexdigest()[:16]
        base = {
            "date": day,
            "source": self._data.get("source", "fixture.performance.daily_cac"),
            "tool_version": self._data.get("tool_version", "1.0.0"),
            "timezone": self._data.get("timezone", "Asia/Kolkata"),
            "currency": self._data.get("currency", "INR"),
            "retrieved_at": retrieved_at,
            "query_hash": query_hash,
        }
        if row is None:
            return {**base, "found": False, "metrics": {}, "row_count": 0}
        values: dict[str, Any] = {}
        for metric_id in metrics:
            field = METRIC_FIELD.get(metric_id)
            if field is None or field not in row:
                continue
            values[metric_id] = row[field]
        return {
            **base,
            "found": True,
            "metrics": values,
            "row_count": 1,
            "raw_untrusted_text": arguments.get("injected_text") or row.get("note") or "",
        }
