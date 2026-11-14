"""User-editable monitoring configuration: shipped defaults (config/domain_health_profiles.yaml) plus stored overrides.

The overrides live next to the snapshots, on the volume the api and the business-state cron share, so a save in the UI
reaches the writer without a redeploy (the YAML is baked into the image and /app is read-only to the service user).
One writer (the api), many readers; the file is replaced atomically and carries a version for optimistic concurrency.

Everything that arrives from the UI is validated here, at the trust boundary: metric ids must exist in the live
catalogue, the alert rule is one of a fixed list, and the number of metrics per domain is capped because every
selected metric is one MCP query per refresh.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from seleric_swarm.services.metrics import MetricRegistry

MAX_METRICS_PER_DOMAIN = 40
MAX_LABEL = 80
ALERT_KINDS = ("anomaly", "worse_by")
DEFAULT_FEATURES = ["period_delta_pct", "rolling_mean_7d"]
_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ENTRY_KEYS = {"metric_id", "label", "alert", "direction_bad", "headline"}
_PAYLOAD_KEYS = {"enabled", "metrics", "expected_version"}
_lock = threading.Lock()


class ConfigError(ValueError):
    """The submitted configuration is not acceptable. The message is safe to show the user."""


class VersionConflict(ConfigError):
    """Someone saved a newer version first."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class OverrideStore:
    def __init__(self, base_dir: str | Path) -> None:
        self._path = Path(base_dir) / "_config" / "overrides.json"

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 0, "domains": {}}
        except (OSError, ValueError):
            # A corrupt file must not stop monitoring: fall back to the shipped defaults and say so in the log.
            import logging

            logging.getLogger("seleric.monitoring").error("monitoring overrides unreadable at %s", self._path)
            return {"version": 0, "domains": {}}
        if not isinstance(data, dict) or not isinstance(data.get("domains"), dict):
            return {"version": 0, "domains": {}}
        return data

    def get(self, domain: str) -> dict[str, Any] | None:
        return self.load()["domains"].get(domain)

    def updated_at(self, domain: str) -> str | None:
        entry = self.get(domain)
        return entry.get("updated_at") if entry else None

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(".overrides.json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._path)

    def save(self, domain: str, entry: dict[str, Any], *, expected_version: int | None, updated_by: str) -> dict[str, Any]:
        with _lock:
            data = self.load()
            current = int((data["domains"].get(domain) or {}).get("version", 0))
            if expected_version is not None and expected_version != current:
                raise VersionConflict(f"{domain} was changed by someone else (version {current}); reload and retry")
            saved = {**entry, "version": current + 1, "updated_at": _now(), "updated_by": updated_by}
            data["domains"][domain] = saved
            data["version"] = int(data.get("version", 0)) + 1
            self._write(data)
            return saved

    def reset(self, domain: str) -> bool:
        with _lock:
            data = self.load()
            if domain not in data["domains"]:
                return False
            del data["domains"][domain]
            data["version"] = int(data.get("version", 0)) + 1
            self._write(data)
            return True


def _clean_alert(raw: Any, where: str) -> dict[str, Any] | None:
    if raw in (None, {}):
        return None
    if not isinstance(raw, dict) or set(raw) - {"on", "threshold"}:
        raise ConfigError(f"{where}: alert must be {{on, threshold}}")
    kind = raw.get("on")
    if kind not in ALERT_KINDS:
        raise ConfigError(f"{where}: alert.on must be one of {', '.join(ALERT_KINDS)}")
    if kind == "anomaly":
        return {"on": "anomaly"}
    threshold = raw.get("threshold")
    if isinstance(threshold, bool) or not isinstance(threshold, int | float) or not math.isfinite(threshold) or not 0 < threshold <= 1000:
        raise ConfigError(f"{where}: alert.threshold must be a number above 0 and at most 1000")
    return {"on": "worse_by", "threshold": float(threshold)}


def validate_domain(
    domain: str, payload: Any, *, registry: MetricRegistry, defaults: list[dict[str, Any]]
) -> dict[str, Any]:
    """Return the normalised override for ``domain`` or raise ``ConfigError``."""
    if not isinstance(payload, dict) or set(payload) - _PAYLOAD_KEYS:
        raise ConfigError("body must be {enabled, metrics}")
    metrics = payload.get("metrics")
    if not isinstance(metrics, list):
        raise ConfigError("metrics must be a list")
    if len(metrics) > MAX_METRICS_PER_DOMAIN:
        raise ConfigError(f"at most {MAX_METRICS_PER_DOMAIN} metrics per domain (each one is a query on every refresh)")
    if not registry.all():
        raise ConfigError("the metric catalogue is not loaded yet; try again in a minute")
    by_default = {e["metric_id"]: e for e in defaults}
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(metrics):
        where = f"metrics[{i}]"
        if not isinstance(raw, dict) or set(raw) - _ENTRY_KEYS:
            raise ConfigError(f"{where}: allowed keys are {', '.join(sorted(_ENTRY_KEYS))}")
        metric_id = raw.get("metric_id")
        if not isinstance(metric_id, str) or not _ID.match(metric_id):
            raise ConfigError(f"{where}: metric_id must be a lowercase id")
        if metric_id in seen:
            raise ConfigError(f"{where}: {metric_id} is listed twice")
        seen.add(metric_id)
        catalogue_id = (by_default.get(metric_id) or {}).get("catalogue_metric") or metric_id
        definition = registry.get(catalogue_id)
        if definition is None:
            raise ConfigError(f"{where}: {catalogue_id} is not in the metric catalogue")
        if definition.grain != "day":
            raise ConfigError(f"{where}: {catalogue_id} is not a daily metric")
        entry: dict[str, Any] = {"metric_id": metric_id}
        label = raw.get("label")
        if label is not None:
            if not isinstance(label, str) or len(label) > MAX_LABEL:
                raise ConfigError(f"{where}: label must be text of at most {MAX_LABEL} characters")
            if label.strip():
                entry["label"] = label.strip()
        alert = _clean_alert(raw.get("alert"), where)
        if alert:
            entry["alert"] = alert
        polarity = raw.get("direction_bad")
        if polarity not in (None, "up", "down", "none"):
            raise ConfigError(f"{where}: direction_bad must be up, down or none")
        if polarity:
            entry["direction_bad"] = polarity
        headline = raw.get("headline")
        if headline is not None:
            if not isinstance(headline, bool):
                raise ConfigError(f"{where}: headline must be true or false")
            entry["headline"] = headline
        out.append(entry)
    enabled = payload.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("enabled must be true or false")
    return {"enabled": enabled, "metrics": out}


def merge_domain(default_block: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    """Effective domain block: the shipped one, or the stored selection laid over the shipped entries it names."""
    block = {k: v for k, v in default_block.items() if k != "metrics"}
    defaults = {e["metric_id"]: e for e in default_block.get("metrics", [])}
    if override is None:
        block["metrics"] = [dict(e) for e in defaults.values()]
        block["custom"] = False
        block["enabled"] = True
        return block
    merged = []
    for entry in override.get("metrics", []):
        base = dict(defaults.get(entry["metric_id"], {"features": DEFAULT_FEATURES}))
        base.update({k: v for k, v in entry.items() if v is not None})
        base.setdefault("features", DEFAULT_FEATURES)
        merged.append(base)
    block["metrics"] = merged
    block["custom"] = True
    block["enabled"] = bool(override.get("enabled", True))
    block["version"] = override.get("version", 0)
    block["updated_at"] = override.get("updated_at")
    block["updated_by"] = override.get("updated_by")
    return block


def candidates(registry: MetricRegistry, catalogue_domains: list[str]) -> list[dict[str, Any]]:
    """Daily catalogue metrics a user can pick for a domain. ``direction_bad`` is None when the catalogue has none."""
    from seleric_swarm.services.metrics import is_intraday_id

    rows = []
    for m in registry.all():
        if m.domain not in catalogue_domains or m.grain != "day" or is_intraday_id(m.id):
            continue
        raw = m.raw or {}
        polarity = raw.get("direction_bad")
        rows.append({
            "id": m.id,
            "name": m.description or m.id,
            "unit": m.unit,
            "domain": m.domain,
            "direction_bad": polarity if polarity in ("up", "down") else None,
            "dimensions": list(raw.get("supported_dimensions") or []),
        })
    return sorted(rows, key=lambda r: r["id"])
