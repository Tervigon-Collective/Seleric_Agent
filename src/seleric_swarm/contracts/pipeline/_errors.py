"""Stage error envelope for pipeline contracts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StageError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: str
    code: str
    message: str
    retryable: bool = False
