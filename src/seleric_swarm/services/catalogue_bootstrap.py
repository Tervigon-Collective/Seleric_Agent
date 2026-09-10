"""Live Cube catalogue cache — warmed once at startup, refreshed on TTL.

Rather than calling ``seleric.catalogue_get_metric`` per metric per mission
(the old Step 1 in ``_resolve_measure``), ``CatalogueBootstrap`` pulls the
full live metric list once and lets ``_resolve_measure`` check the cache as a
free Step 0.  This means:

- Staleness is visible at startup (``unresolvable()``), not buried in
  per-mission limitation strings discovered at query time.
- Hot missions pay **zero** MCP calls for known metrics.
- New metrics added to Cube are discoverable without a YAML PR — the next
  TTL refresh surfaces them automatically.

Design constraints
------------------
- ``warm()`` must never raise — failure is non-fatal; the system falls through
  to Steps 1+2 (exact lookup → semantic fallback) unchanged.
- ``CatalogueBootstrap`` is created synchronously inside ``build_runtime``
  (which is sync) and warmed lazily on the first ``_resolve_measure`` call
  (which is async).  No change to ``build_runtime``'s signature needed.
- ``coordinator_agent`` is the right agent for warming: its allowlist includes
  catalogue listing tools and it carries no module pin, so it sees the
  full unscoped catalogue — including cross-module views like ``canonical_pnl``
  that are invisible when the call is scoped to ``paidmedia`` or ``commerce``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from seleric_swarm.protocols.mcp.gateway import MCPGateway

log = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS: int = 900  # 15 minutes

_BOOTSTRAP_CAP = "seleric.catalogue_bootstrap"
_LIST_METRICS_CAP = "seleric.catalogue_list_metrics"
_SEARCH_CAP = "seleric.catalogue_search_metrics"


@dataclass
class CatalogueMetricMeta:
    """Lightweight snapshot of one live catalogue metric entry."""

    id: str
    label: str = ""
    view: str = ""
    supported_dimensions: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class CatalogueBootstrap:
    """In-process TTL cache of the live Seleric catalogue metric list.

    Usage
    -----
    ::

        # created synchronously during build_runtime()
        bootstrap = CatalogueBootstrap(mcp)

        # warmed lazily on first _resolve_measure() call
        await bootstrap.refresh_if_stale()

        # O(1) cache hit — no MCP call
        if bootstrap.has("session_conversion_rate"):
            return "session_conversion_rate"

        # startup staleness check
        stale = bootstrap.unresolvable(["session_purchase_rate", "total_ad_spend"])
    """

    def __init__(
        self,
        mcp: MCPGateway,
        agent_id: str = "coordinator_agent",
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._mcp = mcp
        self._agent_id = agent_id
        self._ttl = ttl_seconds
        self._cache: dict[str, CatalogueMetricMeta] = {}
        self._dimension_aliases: dict[str, list[str]] = {}
        self._grain_defaults: dict[str, Any] = {}
        self._warmed_at: float | None = None

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def is_warm(self) -> bool:
        """Return True if the cache has been populated at least once."""
        return self._warmed_at is not None and bool(self._cache)

    def should_refresh(self) -> bool:
        """Return True when the cache has never been warmed or the TTL has expired."""
        if self._warmed_at is None:
            return True
        return (time.monotonic() - self._warmed_at) > self._ttl

    # ------------------------------------------------------------------
    # Cache access
    # ------------------------------------------------------------------

    def has(self, metric_id: str) -> bool:
        """Return True if *metric_id* is present in the live catalogue cache."""
        return metric_id in self._cache

    def get(self, metric_id: str) -> CatalogueMetricMeta | None:
        """Return the cached metadata for *metric_id*, or None if not found."""
        return self._cache.get(metric_id)

    def known_ids(self) -> set[str]:
        """Return the set of all catalogue metric IDs currently in the cache."""
        return set(self._cache.keys())

    def entries(self) -> list[CatalogueMetricMeta]:
        """All cached metric rows (id, view, supported_dimensions)."""
        return list(self._cache.values())

    def dimension_ids(self) -> set[str]:
        """Explicit catalogue dimensions plus those listed on cached metrics."""
        ids = set(self._dimension_aliases)
        for meta in self._cache.values():
            ids.update(d for d in (meta.supported_dimensions or []) if d)
        return ids

    def alias_index(self) -> dict[str, str]:
        """Normalized alias / display token → dimension id."""
        out: dict[str, str] = {}
        for dim_id, aliases in self._dimension_aliases.items():
            out[dim_id.lower().replace("_", " ")] = dim_id
            for alias in aliases:
                key = str(alias).lower().replace("_", " ").strip()
                if key:
                    out[key] = dim_id
        return out

    def grain_defaults(self) -> dict[str, Any]:
        return dict(self._grain_defaults)

    def unresolvable(self, candidate_ids: list[str]) -> list[str]:
        """Return the subset of *candidate_ids* that are NOT in the live cache.

        Call this after ``warm()`` to surface stale registry entries at startup
        rather than discovering them mid-mission.

        Parameters
        ----------
        candidate_ids:
            Usually ``[m.catalogue_metric for m in metrics.all() if m.catalogue_metric]``.
        """
        return [cid for cid in candidate_ids if cid and cid not in self._cache]

    # ------------------------------------------------------------------
    # Warming
    # ------------------------------------------------------------------

    async def _call(self, capability: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await self._mcp.call(
                agent_id=self._agent_id,
                capability=capability,
                arguments=arguments,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "CatalogueBootstrap %s failed (%s: %s)",
                capability,
                type(exc).__name__,
                exc,
            )
            return {}
        return result if isinstance(result, dict) else {}

    def _load_payload(self, payload: dict[str, Any]) -> int:
        rows = payload.get("metrics") or payload.get("matches") or []
        self._cache.clear()
        for row in rows:
            if not isinstance(row, dict):
                continue
            mid = str(row.get("id") or "")
            if not mid:
                continue
            self._cache[mid] = CatalogueMetricMeta(
                id=mid,
                label=str(row.get("label") or row.get("display_name") or ""),
                view=str(row.get("view") or row.get("cube_view") or ""),
                supported_dimensions=list(row.get("supported_dimensions") or []),
                raw=row,
            )
        dims = payload.get("dimensions") or []
        self._dimension_aliases = {}
        for dim in dims:
            if not isinstance(dim, dict):
                continue
            did = str(dim.get("id") or "")
            if did:
                self._dimension_aliases[did] = [str(a) for a in (dim.get("aliases") or [])]
        defaults = payload.get("grain_defaults") or {}
        self._grain_defaults = dict(defaults) if isinstance(defaults, dict) else {}
        return len(self._cache)

    async def warm(self, registry_hints: list[str] | None = None) -> int:
        """Pull the live metric list (and dimension index when the server sends it).

        Prefer ``catalogue_bootstrap``, then ``catalogue_list_metrics``, then
        empty ``catalogue_search_metrics``. Never raises — a failing call
        leaves the cache empty so ``_resolve_measure`` falls through to
        Steps 1+2 unchanged.
        """
        count = 0
        payload = await self._call(_BOOTSTRAP_CAP, {})
        count = self._load_payload(payload)
        if count == 0:
            payload = await self._call(_LIST_METRICS_CAP, {})
            count = self._load_payload(payload)
        if count == 0:
            payload = await self._call(_SEARCH_CAP, {"query": ""})
            count = self._load_payload(payload)

        self._warmed_at = time.monotonic()
        if count:
            log.info("CatalogueBootstrap: cached %d live catalogue metric(s)", count)
        else:
            log.warning("CatalogueBootstrap.warm() failed — using empty cache")

        if registry_hints and count > 0:
            for stale_id in self.unresolvable(registry_hints):
                log.warning(
                    "metric_registry stale: catalogue_metric '%s' not found in live catalogue "
                    "— update metric_registry.yaml to silence this warning",
                    stale_id,
                )
        return count

    async def refresh_if_stale(self) -> None:
        """Warm the cache if it has never been populated or the TTL has expired.

        Safe to call on every ``_resolve_measure`` invocation — the TTL check
        is a monotonic comparison so it costs nothing on the hot path.

        Note: does not pass ``registry_hints`` — startup staleness logging
        only happens on the first explicit ``warm()`` call (from
        ``build_hybrid_bundle`` after the first mission triggers warming).
        """
        if self.should_refresh():
            await self.warm()
