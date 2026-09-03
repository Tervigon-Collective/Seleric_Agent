from pathlib import Path
import yaml


class AgentRegistry:
    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self._agents = self._load()

    def _load(self) -> list[dict]:
        data = yaml.safe_load(self.config_path.read_text())
        return data.get("agents", [])

    def find_by_capability(self, capability: str) -> list[dict]:
        return [a for a in self._agents if capability in a.get("capabilities", [])]

    def get(self, agent_id: str) -> dict | None:
        return next((a for a in self._agents if a.get("id") == agent_id), None)
