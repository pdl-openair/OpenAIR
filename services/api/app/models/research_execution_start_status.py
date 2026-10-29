from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CanonicalStartState = Literal[
    "accepted", "running", "committed", "blocked", "failed",
    "not_committed", "unknown",
]
StartResponsibility = Literal["SYSTEM", "USER", "NONE", "TERMINAL"]


class ResearchExecutionStartStatusOut(BaseModel):
    """Canonical start authority for the current Task revision."""

    model_config = ConfigDict(extra="forbid", strict=True)

    taskId: str = Field(min_length=1)
    state: CanonicalStartState
    taskRevision: int = Field(ge=0)
    startAllowed: bool
    responsibility: StartResponsibility
    retryable: bool
    reasonSummary: str | None = Field(default=None, min_length=1)
    blockerRevision: int | None = Field(default=None, ge=0)
    executionDecisionId: str | None = Field(default=None, min_length=1)
    blockingReasonCode: str | None = Field(default=None, min_length=1)
    failureReasonCode: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_state_payload(self) -> "ResearchExecutionStartStatusOut":
        if self.state == "blocked":
            if (
                self.executionDecisionId is not None
                or self.blockerRevision is None
                or self.blockerRevision != self.taskRevision
                or self.blockingReasonCode is None
                or self.failureReasonCode is not None
                or self.startAllowed
                or self.responsibility == "NONE"
                or self.reasonSummary is None
            ):
                raise ValueError("blocked start status requires revision-bound blocker semantics")
            if self.responsibility in {"SYSTEM", "TERMINAL"} and self.retryable:
                raise ValueError("system or terminal blocker cannot be user-retryable")
            return self

        if self.blockerRevision is not None or self.blockingReasonCode is not None:
            raise ValueError("non-blocked start status cannot expose blocker semantics")

        if self.state == "unknown":
            if (
                self.executionDecisionId is not None
                or self.failureReasonCode is not None
                or self.startAllowed
                or self.responsibility != "SYSTEM"
                or self.retryable
                or self.reasonSummary is None
            ):
                raise ValueError("unknown start status must fail closed as system-owned")
            return self

        if self.state == "committed":
            if (
                self.executionDecisionId is None
                or self.failureReasonCode is not None
                or self.startAllowed
                or self.responsibility != "NONE"
                or self.retryable
                or self.reasonSummary is not None
            ):
                raise ValueError("committed start status has invalid canonical semantics")
            return self

        if self.state == "failed":
            if (
                self.executionDecisionId is not None
                or self.failureReasonCode is None
                or self.responsibility != "NONE"
                or self.reasonSummary is not None
                or self.retryable != self.startAllowed
            ):
                raise ValueError("failed start status has invalid canonical semantics")
            return self

        if (
            self.executionDecisionId is not None
            or self.failureReasonCode is not None
            or self.responsibility != "NONE"
            or self.reasonSummary is not None
        ):
            raise ValueError("non-terminal start status cannot expose terminal semantics")
        if self.state in {"accepted", "running"}:
            if self.startAllowed or self.retryable:
                raise ValueError("in-flight start status cannot allow another start")
        elif self.retryable != self.startAllowed:
            raise ValueError("not_committed retryability must match startAllowed")
        return self


class ResearchExecutionStartRequestStatusOut(BaseModel):
    """Receipt for one Start request; never the current Task start authority."""

    model_config = ConfigDict(extra="forbid", strict=True)

    taskId: str = Field(min_length=1)
    selectionRequestId: str = Field(min_length=1)
    state: CanonicalStartState
    taskRevision: int | None = Field(default=None, ge=0)
    executionDecisionId: str | None = Field(default=None, min_length=1)
    blockingReasonCode: str | None = Field(default=None, min_length=1)
    failureReasonCode: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_receipt_payload(self) -> "ResearchExecutionStartRequestStatusOut":
        if self.state == "committed":
            if (
                self.executionDecisionId is None
                or self.blockingReasonCode is not None
                or self.failureReasonCode is not None
            ):
                raise ValueError("committed start request requires decision identity")
        elif self.state == "blocked":
            if (
                self.executionDecisionId is not None
                or self.taskRevision is None
                or self.blockingReasonCode is None
                or self.failureReasonCode is not None
            ):
                raise ValueError("blocked start request requires request revision and reason")
        elif self.state == "failed":
            if (
                self.executionDecisionId is not None
                or self.blockingReasonCode is not None
                or self.failureReasonCode is None
            ):
                raise ValueError("failed start request requires failure reason")
        elif self.executionDecisionId is not None or self.blockingReasonCode is not None or self.failureReasonCode is not None:
            raise ValueError("non-terminal start request cannot expose terminal fields")
        return self
