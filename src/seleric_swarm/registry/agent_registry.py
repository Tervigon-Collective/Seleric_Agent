from pathlib import Path

import yaml

from seleric_swarm.paths import repo_root


class AgentRegistry:
    def __init__(self, config_path: str):
        path = Path(config_path)
        self.config_path = path if path.is_absolute() else repo_root() / path
        self._agents = self._load()

    def _load(self) -> list[dict]:
        data = yaml.safe_load(self.config_path.read_text())
        return data.get("agents", [])

    def find_by_capability(self, capability: str) -> list[dict]:
        return [a for a in self._agents if capability in a.get("capabilities", [])]

    def all_ids(self) -> list[str]:
        return [str(a["id"]) for a in self._agents if a.get("id")]

    def capabilities_of(self, agent_id: str) -> list[str]:
        agent = self.get(agent_id)
        return list(agent.get("capabilities", [])) if agent else []

    def get(self, agent_id: str) -> dict | None:
        return next((a for a in self._agents if a.get("id") == agent_id), None)

    def version(self, agent_id: str, default: str = "0.1.0") -> str:
        agent = self.get(agent_id)
        if not agent:
            return default
        return str(agent.get("version") or default)
