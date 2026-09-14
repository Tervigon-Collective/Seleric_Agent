from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from seleric_swarm.paths import repo_root


class ProfileLoader:
    """Loads config/business_state_profiles.yaml (frozen at Sprint 0)."""

    def __init__(self, config_path: str | Path = "config/business_state_profiles.yaml") -> None:
        root = repo_root()
        path = Path(config_path)
        if not path.is_absolute():
            path = root / path
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        self._profiles: dict[str, Any] = data.get("profiles") or {}

    def get(self, profile_id: str) -> dict[str, Any]:
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise KeyError(f"Unknown business_state profile: {profile_id}")
        return profile
