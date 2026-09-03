from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from seleric_swarm.paths import repo_root

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


class PromptLangSmithMeta(BaseModel):
    prompt_name: str
    dataset: str | None = None


class PromptSpec(BaseModel):
    id: str
    version: str
    agent_id: str
    agent_version: str
    workflow: str
    model: str
    temperature: float = 0.0
    max_tokens: int = 800
    output_schema: str | None = None
    langsmith: PromptLangSmithMeta | None = None
    system: str
    user_template: str

    def allowed_variables(self) -> set[str]:
        return set(_VAR_RE.findall(self.user_template))

    def render_user(self, variables: dict[str, Any]) -> str:
        allowed = self.allowed_variables()
        extra = set(variables) - allowed
        if extra:
            raise ValueError(f"Prompt {self.id} v{self.version} received unknown variables: {sorted(extra)}")
        missing = allowed - set(variables)
        if missing:
            raise ValueError(f"Prompt {self.id} v{self.version} missing variables: {sorted(missing)}")

        def repl(match: re.Match[str]) -> str:
            key = match.group(1)
            value = variables[key]
            return "" if value is None else str(value)

        return _VAR_RE.sub(repl, self.user_template)


class PromptRegistry:
    def __init__(self, prompts_dir: str | Path, versions_path: str | Path) -> None:
        root = repo_root()
        self.prompts_dir = (root / prompts_dir).resolve() if not Path(prompts_dir).is_absolute() else Path(prompts_dir)
        self.versions_path = (
            (root / versions_path).resolve() if not Path(versions_path).is_absolute() else Path(versions_path)
        )
        self._pinned: dict[str, str] = self._load_pins()

    def _load_pins(self) -> dict[str, str]:
        data = yaml.safe_load(self.versions_path.read_text(encoding="utf-8")) or {}
        return {str(k): str(v) for k, v in data.items()}

    def pinned_version(self, prompt_id: str) -> str:
        if prompt_id not in self._pinned:
            raise KeyError(f"No pinned version for prompt {prompt_id}")
        return self._pinned[prompt_id]

    def load(self, prompt_id: str, version: str | None = None) -> PromptSpec:
        ver = version or self.pinned_version(prompt_id)
        agent_key, name = prompt_id.split(".", 1)
        path = self.prompts_dir / agent_key / f"{name}.v{ver}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Prompt file not found: {path}")
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        spec = PromptSpec.model_validate(payload)
        if spec.id != prompt_id:
            raise ValueError(f"Prompt id mismatch: file={spec.id} requested={prompt_id}")
        if str(spec.version) != str(ver):
            raise ValueError(f"Prompt version mismatch: file={spec.version} requested={ver}")
        return spec
