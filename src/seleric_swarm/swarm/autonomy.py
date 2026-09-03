"""Agent autonomy boundaries (architecture sec. 39).

Level 6 (execute business action) is DISABLED for the whole prototype; it needs
separate authorization. The caps below are advisory metadata the orchestrator and
governance layer can enforce.
"""

from __future__ import annotations

from enum import IntEnum


class AutonomyLevel(IntEnum):
    READ_ARTIFACT = 0
    REQUEST_EVIDENCE = 1
    INVOKE_SPECIALIST = 2
    CREATE_SUBTASK = 3
    PROPOSE_HANDOFF = 4
    PROPOSE_INTERVENTION = 5
    EXECUTE_ACTION = 6


# Max autonomy per agent *class*. Business-action execution stays off everywhere.
AUTONOMY_CAPS: dict[str, AutonomyLevel] = {
    "coordinator": AutonomyLevel.PROPOSE_HANDOFF,
    "domain": AutonomyLevel.PROPOSE_HANDOFF,
    "strategy": AutonomyLevel.PROPOSE_INTERVENTION,
    "specialist": AutonomyLevel.CREATE_SUBTASK,
}

EXECUTION_ENABLED = False


def cap_for(agent_class: str) -> AutonomyLevel:
    return AUTONOMY_CAPS.get(agent_class, AutonomyLevel.INVOKE_SPECIALIST)


def allowed(agent_class: str, level: AutonomyLevel) -> bool:
    if level >= AutonomyLevel.EXECUTE_ACTION and not EXECUTION_ENABLED:
        return False
    return level <= cap_for(agent_class)
