from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RecoveryRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceRunId: str = Field(min_length=1, max_length=240)
    recoveryKind: Literal["retry", "replan"]
    expectedTaskRevision: int = Field(ge=0)
    messageIds: list[str] = Field(min_length=1, max_length=100)
    idempotencyKey: str = Field(min_length=1, max_length=256)
    planFingerprint: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator("sourceRunId", "idempotencyKey", "planFingerprint")
    @classmethod
    def strip_required_values(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("messageIds")
    @classmethod
    def validate_message_ids(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("messageIds must not contain blank values")
        if len(set(normalized)) != len(normalized):
            raise ValueError("messageIds must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_plan_fingerprint(self) -> "RecoveryRequestIn":
        if self.planFingerprint and any(char not in "0123456789abcdef" for char in self.planFingerprint):
            raise ValueError("planFingerprint must be a lowercase sha256 hex digest")
        return self


class PreservedFactsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceRunState: str
    sourceRunNumber: int
    preservedArtifactCount: int


class RecoveryRequestOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recoveryRequestId: str
    taskId: str
    sourceRunId: str
    recoveryKind: Literal["retry", "replan"]
    state: Literal["requested", "confirmed", "cancelled"]
    expectedTaskRevision: int
    taskRevision: int
    successorRunId: str | None
    replayed: bool
    allowedActions: list[Literal["confirm"]]
    preservedFacts: PreservedFactsOut
