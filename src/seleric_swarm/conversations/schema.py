"""Generate the public JSON Schemas from canonical Pydantic contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from seleric_swarm.conversations.contracts import (
    ActivityEvent,
    ApprovalDecisionEvent,
    ApprovalRequest,
    Artifact,
    Attachment,
    MemoryItem,
    MemoryPreference,
    Message,
    MessagePart,
    Principal,
    RollbackRecord,
    Run,
    RunAttempt,
    SearchResult,
    Thread,
    ThreadParticipant,
    ThreadSummary,
)

CONVERSATION_CONTRACTS: tuple[type[BaseModel], ...] = (
    Principal,
    Thread,
    ThreadParticipant,
    Message,
    MessagePart,
    Run,
    RunAttempt,
    ActivityEvent,
    Attachment,
    Artifact,
    ThreadSummary,
    MemoryItem,
    MemoryPreference,
    SearchResult,
    ApprovalRequest,
    ApprovalDecisionEvent,
    RollbackRecord,
)


def conversation_json_schema() -> dict[str, object]:
    """Return versioned schemas without duplicating contract enums or fields."""

    return {
        "schema_version": "1.0.0",
        "contracts": {
            model.__name__: model.model_json_schema(mode="serialization")
            for model in CONVERSATION_CONTRACTS
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Seleric conversation JSON Schema")
    parser.add_argument("--output", type=Path, help="Write schema JSON to this path")
    args = parser.parse_args()
    rendered = json.dumps(conversation_json_schema(), indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
