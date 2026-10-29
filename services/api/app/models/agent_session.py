"""Pydantic models for plaza AgentSession (independent of ResearchDesign)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


AgentSessionStatus = Literal["ready", "running", "waiting_input", "completed", "failed"]
AgentMessageRole = Literal["system", "user", "assistant"]
AgentSessionKind = Literal["agent", "skill"]


class AgentSessionStepOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stepKey: str
    pack: str
    stage: str
    substage: str
    role: str | None = None
    supportedRole: str | None = None
    artifactContract: str | None = None
    enabled: bool = True


class AgentSessionMessageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messageId: str
    role: AgentMessageRole
    content: str
    createdAt: str


class AgentSessionArtifactOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifactId: str
    title: str
    stepKey: str | None = None
    artifactContract: str | None = None
    runId: str | None = None
    summary: str
    body: dict[str, Any] = Field(default_factory=dict)
    downloadAvailable: bool = False
    createdAt: str


class AgentSessionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionId: str
    title: str
    status: AgentSessionStatus
    resourceId: str
    resourceVersion: str
    agentId: str
    agentDisplayName: str
    sessionKind: AgentSessionKind = "agent"
    activeStepKey: str | None
    steps: list[AgentSessionStepOut]
    messages: list[AgentSessionMessageOut]
    artifacts: list[AgentSessionArtifactOut]
    createdAt: str
    updatedAt: str


class AgentSessionCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resourceId: str = Field(min_length=1)


class AgentSessionMessageCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=8000)


class AgentSessionSummaryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionId: str
    title: str
    status: AgentSessionStatus
    resourceId: str
    agentId: str
    agentDisplayName: str
    sessionKind: AgentSessionKind = "agent"
    messageCount: int = 0
    artifactCount: int = 0
    createdAt: str
    updatedAt: str


class AgentSessionListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AgentSessionSummaryOut]
