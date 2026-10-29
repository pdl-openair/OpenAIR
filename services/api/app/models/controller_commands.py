from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ControllerCommandIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["supplement_input"]
    text: str = Field(min_length=1, max_length=12000)
    expectedTaskRevision: int = Field(ge=0)


class ControllerCommandOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commandId: str
    messageId: str
    taskId: str
    kind: Literal["supplement_input"]
    state: Literal["received", "applied", "rejected", "failed"]
    userMessage: str
    submittedTaskRevision: int
    appliedExecutionDecisionId: str | None = None
    appliedWorkUnitId: str | None = None
    terminalReasonCode: str | None = None
    submittedAt: str
    updatedAt: str
