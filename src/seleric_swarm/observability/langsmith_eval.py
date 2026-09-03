from __future__ import annotations

import json
from typing import Any

from seleric_swarm.config.settings import Settings
from seleric_swarm.observability.tracing import redact_mapping


def maybe_create_experiment(name: str, metadata: dict[str, Any], settings: Settings) -> str | None:
    """Best-effort LangSmith experiment id. Never raises; never used in the live request path."""
    if not settings.langsmith_tracing or not settings.langsmith_api_key:
        return None
    try:
        from langsmith import Client

        client = Client(api_key=settings.langsmith_api_key, api_url=settings.langsmith_endpoint)
        project = client.read_project(project_name=settings.langsmith_project)
        return f"{project.name}:{name}:{json.dumps(redact_mapping(metadata), sort_keys=True)[:80]}"
    except Exception:
        return None
