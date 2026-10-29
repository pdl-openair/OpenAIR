"""Agent / Claude Code interaction API models."""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.schemas import UserFacingNarrativeOut

logger = logging.getLogger(__name__)


class ConfirmationOptionOut(BaseModel):
    id: str
    label: str
    value: str | None = None
    requiresNote: bool = False
    style: Literal["primary", "default", "danger"] = "default"


class ConfirmationRequestOut(BaseModel):
    id: str
    title: str | None = None
    prompt: str
    kind: str = "approval"
    status: str = "pending"
    blocking: bool = True
    recommendedOptionId: str | None = None
    options: list[ConfirmationOptionOut] = Field(default_factory=list)
    artifactRefs: list[str] = Field(default_factory=list)
    relatedSkillId: str | None = None
    relatedCallId: str | None = None
    resolvedAction: str | None = None
    selectedOption: str | None = None
    note: str | None = None
    resolvedAt: str | None = None
    userFacingNarrative: UserFacingNarrativeOut | None = None


class AgentMessageOut(BaseModel):
    id: str
    role: Literal["user", "assistant", "system"]
    text: str
    at: str
    runtime: str | None = None
    messageType: str = "chat"
    authorLabel: str | None = None
    controllerCommandId: str | None = None
    capabilityCallId: str | None = None
    artifactRefs: list[str] = Field(default_factory=list)
    confirmationRequests: list[ConfirmationRequestOut] = Field(default_factory=list)
    userFacingNarrative: UserFacingNarrativeOut | None = None


class AgentMessageListOut(BaseModel):
    messages: list[AgentMessageOut]
    runtime: str = "none"
    sessionId: str | None = None
    ownerId: str
    workspaceId: str
    createdBy: str
    # E1 cursor pagination (postgres table read). hasMore + nextBeforeSeq let the
    # client page backwards in time (?before=<nextBeforeSeq>). Defaults keep the
    # shape backward-compatible for the JSON-store branch (unpaginated).
    hasMore: bool = False
    nextBeforeSeq: int | None = None


class AgentChatIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: str = Field(min_length=1)
    skillId: str | None = None
    substageKey: str | None = None
    executionMode: Literal[
        "route_only",
        "draft_only",
        "evidence_bound",
        "retrieval_bound",
        "full_execution",
        "critique_only",
    ] | None = None
    inputPolicy: Literal["queue", "merge", "cancel", "wait"] | None = None

    # Known canonical fields used by this model.
    _KNOWN_FIELDS = frozenset({"message", "skillId", "substageKey", "executionMode", "inputPolicy"})
    # Legacy aliases that are normalized into canonical fields.
    _LEGACY_ALIASES = frozenset({"text", "prompt", "content", "skill_id", "substage_key", "subStageKey"})

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_fields(cls, raw: object) -> object:
        """Normalize legacy field names to canonical fields.

        Priority for message fallback: text > prompt > content (first truthy wins).
        Priority for substageKey fallback: substage_key > subStageKey.
        """
        if not isinstance(raw, dict):
            return raw
        data = dict(raw)

        # Log fields that will be silently discarded by extra="ignore" for diagnostics.
        if logger.isEnabledFor(logging.DEBUG):
            known = cls._KNOWN_FIELDS | cls._LEGACY_ALIASES
            discarded = {k for k in data if k not in known}
            if discarded:
                logger.debug("AgentChatIn: discarding unknown fields: %s", discarded)

        if not data.get("message"):
            for key in ("text", "prompt", "content"):
                value = data.get(key)
                if value:
                    data["message"] = value
                    break
        if data.get("skillId") is None and data.get("skill_id") is not None:
            data["skillId"] = data.get("skill_id")
        if data.get("substageKey") is None:
            if data.get("substage_key") is not None:
                data["substageKey"] = data.get("substage_key")
            elif data.get("subStageKey") is not None:
                data["substageKey"] = data.get("subStageKey")
        return data


class AgentChatOut(BaseModel):
    reply: str
    runtime: str
    messages: list[AgentMessageOut]
    capabilityCallId: str | None = None
    createdArtifactIds: list[str] = Field(default_factory=list)
    error: str | None = None
    executionMode: str | None = None
    targetAgent: str | None = None
    contextPackId: str | None = None
    userInputPolicy: str | None = None


class ConfirmationResolveIn(BaseModel):
    action: str = Field(min_length=1)
    note: str | None = None
    selectedOption: str | None = None


class ConfirmationResolveOut(BaseModel):
    id: str
    status: str
    resolvedAction: str | None = None
    selectedOption: str | None = None
    note: str | None = None
    resolvedAt: str | None = None


class ExecuteSkillIn(BaseModel):
    skillId: str | None = None


class ExecuteSkillOut(BaseModel):
    callId: str
    status: str
    summary: str
    runtime: str
    error: str | None = None


class SystemAgentRunIn(BaseModel):
    agentId: Literal[
        "openair_summary_agent",
        "openair_review_agent",
        "openair_presentation_guidance_agent",
    ]
    sourceText: str | None = None
    artifactId: str | None = None
    criteria: str | None = None
    audience: str | None = None
    outputFormat: str | None = None
    reviewDecision: Literal["approved", "needs_revision"] | None = None


class SystemAgentRunOut(BaseModel):
    callId: str
    status: str
    summary: str
    runtime: str
    artifactIds: list[str] = Field(default_factory=list)
    reviewedArtifactId: str | None = None
    error: str | None = None


class CapabilityCallListOut(BaseModel):
    calls: list[dict]
