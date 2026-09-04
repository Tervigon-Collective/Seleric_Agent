from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentContext:
    mission_id: str
    task_id: str
    question: str
    mission_lead: str
    evidence_refs: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


class SwarmAgent(ABC):
    agent_id: str

    @abstractmethod
    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        raise NotImplementedError
