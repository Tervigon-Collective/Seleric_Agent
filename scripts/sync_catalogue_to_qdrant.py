"""One-off sync: mirror the live seleric-mcp catalogue into the local Qdrant
catalogue index (``toolsets/catalogue_index.py``).

Pulls the current metric, dimension, and brand catalogue over the same
``MCPGateway``/``SelericMCPTransport`` path the running agent uses (not a
hand-maintained copy), embeds each entry with ``text-embedding-3-small``
(``settings.qdrant_embedding_model``), and upserts it into Qdrant. Run this
manually (or on a cron/Task Scheduler) whenever the live catalogue changes;
``toolsets/semantic.py::search_semantics`` flags a match as stale in its
``ToolResult.warnings`` if it hasn't been refreshed in
``catalogue_index.STALE_AFTER_HOURS`` (24h by default).

Periodic scheduling is intentionally NOT wired into app startup here — this
is the one-off script; a recurring job (cron/Task Scheduler) is a follow-up.

Usage:
    python scripts/sync_catalogue_to_qdrant.py [--prune]

``--prune`` also deletes entries of each kind that the live catalogue no longer serves (e.g. the v1
metric ids retired by the semantic v2 hard cut), so the fallback index can never hand the agent an id
the MCP rejects. A kind whose live pull came back empty is never pruned.
"""

from __future__ import annotations

import asyncio
import argparse
from collections.abc import Callable
from typing import Any

from dotenv import load_dotenv

from seleric_swarm.bootstrap import _sync_settings_to_environ
from seleric_swarm.config.settings import get_settings
from seleric_swarm.paths import repo_root
from seleric_swarm.protocols.mcp.gateway import MCPGateway
from seleric_swarm.toolsets import catalogue_index

_AGENT_ID = "v3_agent"

# (kind, MCP capability, arguments, response-list-key, id-field, name-field)
# — the live seleric-mcp response shape differs per kind (verified against
# the running server 2026-09-21): metrics/dimensions use "display_name" and
# nest their rows under "matches"/"dimensions"; brands use "name" and nest
# under "brands". No local canonicalization of these fields, just reading
# them under their live-catalogue key names.
_SOURCES: list[tuple[str, str, str, str, str]] = [
    # metrics from catalogue_bootstrap (same queryable list as catalogue_list_metrics, plus the
    # semantic v2 fields): catalogue_list_metrics is rate-limited server-side (3 calls / 60 s, shared
    # with the /readyz probe), so a sync through it can come back as an error payload with 0 rows.
    ("metric", "seleric.catalogue_bootstrap", "metrics", "id", "display_name"),
    ("dimension", "seleric.catalogue_list_dimensions", "dimensions", "id", "display_name"),
    ("brand", "seleric.catalogue_list_brands", "brands", "id", "name"),
]


def _entries_from(payload: dict[str, Any], list_key: str) -> list[dict[str, Any]]:
    rows = payload.get(list_key)
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


async def _sync_kind(
    gateway: MCPGateway,
    client: Any,
    settings: Any,
    embedder: Callable[[str], list[float]],
    *,
    kind: str,
    capability: str,
    list_key: str,
    id_field: str,
    name_field: str,
) -> set[str]:
    payload = await gateway.call(agent_id=_AGENT_ID, capability=capability, arguments={})
    synced: set[str] = set()
    for entry in _entries_from(payload, list_key):
        catalogue_id = str(entry.get(id_field) or "").strip()
        if not catalogue_id:
            continue
        name = str(entry.get(name_field) or catalogue_id)
        description = str(entry.get("description") or "")
        aliases = list(entry.get("aliases") or [])
        text = catalogue_index.compose_embedding_text(name, description, aliases)
        catalogue_index.upsert_entry(
            client,
            settings.qdrant_collection,
            kind=kind,
            catalogue_id=catalogue_id,
            name=name,
            vector=embedder(text),
            description=description,
            aliases=aliases,
            unit=entry.get("unit"),
            full_definition=entry,
        )
        synced.add(catalogue_id)
    return synced


def _prune_kind(client: Any, collection: str, kind: str, keep: set[str]) -> list[str]:
    """Delete stored ``kind`` entries whose id the live catalogue no longer serves."""
    from qdrant_client.http import models as qmodels

    stored = {str(row.get("id")) for row in catalogue_index.list_catalogue(client, collection, kind=kind)}
    stale = sorted(stored - keep)
    if stale:
        client.delete(
            collection_name=collection,
            points_selector=qmodels.PointIdsList(points=[catalogue_index.point_id(kind, cid) for cid in stale]),
        )
    return stale


async def _sync(prune: bool = False) -> int:
    load_dotenv(repo_root() / ".env")
    settings = get_settings()
    _sync_settings_to_environ(settings)
    embedder = catalogue_index.build_embedder(settings)
    client = catalogue_index.build_client(settings)
    probe_vector = embedder("catalogue sync dimension probe")
    catalogue_index.ensure_collection(client, settings.qdrant_collection, len(probe_vector))

    gateway = MCPGateway(settings.mcp_config_path)
    synced = 0
    try:
        for kind, capability, list_key, id_field, name_field in _SOURCES:
            ids = await _sync_kind(
                gateway,
                client,
                settings,
                embedder,
                kind=kind,
                capability=capability,
                list_key=list_key,
                id_field=id_field,
                name_field=name_field,
            )
            print(f"  {kind}: {len(ids)} entries")
            synced += len(ids)
            if prune and ids:
                stale = _prune_kind(client, settings.qdrant_collection, kind, ids)
                print(f"  {kind}: pruned {len(stale)} stale" + (f" (e.g. {', '.join(stale[:5])})" if stale else ""))
    finally:
        await gateway.aclose()

    print(f"synced {synced} catalogue entries into '{settings.qdrant_collection}'")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Mirror the live seleric-mcp catalogue into Qdrant.")
    ap.add_argument("--prune", action="store_true", help="delete entries the live catalogue no longer serves")
    return asyncio.run(_sync(prune=ap.parse_args().prune))


if __name__ == "__main__":
    raise SystemExit(main())
