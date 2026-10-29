"""Pydantic schemas for S04-01 platform governance (auth extras / contributions / audit)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field

ContributionStatus = Literal[
    "private_ready",
    "pending_public_review",
    "approved_public",
    "rejected",
    "withdrawn",
]
ContributionSystemCheckStatus = Literal["not_checked", "passed", "needs_attention"]
ContributionDomain = Literal["cs_research", "materials", "general"]
ContributionDiscoveryScene = Literal[
    "research_materials",
    "data_results",
    "experiment_computation",
    "research_quality",
]


class PasswordForgotRequest(BaseModel):
    email: EmailStr


class PasswordForgotResponse(BaseModel):
    message: str
    """Dev-only: raw reset token when OPENAIR_ENV=development (no mailer)."""
    resetToken: str | None = None


class PasswordResetRequest(BaseModel):
    token: str = Field(min_length=10, max_length=256)
    newPassword: str = Field(min_length=6, max_length=128)


class LogoutResponse(BaseModel):
    status: str = "ok"


class UserPrefsUpdateRequest(BaseModel):
    quotaAlertThresholdPct: int | None = Field(default=None, ge=1, le=100)
    displayName: str | None = Field(default=None, max_length=100)


class UserPrefsResponse(BaseModel):
    userId: str
    displayName: str | None = None
    quotaAlertThresholdPct: int = 80
    role: str
    isActive: bool = True


class AdminSetActiveRequest(BaseModel):
    isActive: bool
    reason: str = Field(min_length=1, max_length=500)


class AdminUserRow(BaseModel):
    userId: str
    email: str
    username: str
    displayName: str | None = None
    role: str
    isActive: bool
    quotaTier: str
    createdAt: str | None = None


class ContributionCreateRequest(BaseModel):
    resourceType: Literal["skill", "resource"] = "skill"
    resourceRef: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    domain: ContributionDomain = "general"
    discoveryScene: ContributionDiscoveryScene = "research_materials"
    """When true, submit for public review immediately; else keep private_ready."""
    requestPublic: bool = False


class ContributionReviewRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reviewNotes: str = Field(default="", max_length=2000)


class ContributionOut(BaseModel):
    contributionId: str
    ownerId: str
    workspaceId: str | None = None
    resourceType: str
    resourceRef: str
    title: str
    description: str
    domain: ContributionDomain = "general"
    discoveryScene: ContributionDiscoveryScene = "research_materials"
    status: ContributionStatus
    systemCheckStatus: ContributionSystemCheckStatus = "not_checked"
    systemCheckIssues: list[str] = Field(default_factory=list)
    reviewNotes: str | None = None
    reviewerId: str | None = None
    reviewedAt: str | None = None
    catalogResourceId: str | None = None
    releaseVersion: str | None = None
    publishedAt: str | None = None
    createdAt: str | None = None
    updatedAt: str | None = None


class AuditLogOut(BaseModel):
    auditId: str
    action: str
    actorId: str | None = None
    targetId: str | None = None
    outcome: str
    detail: dict[str, Any] = Field(default_factory=dict)
    ip: str | None = None
    createdAt: str | None = None


class HeavyQueueStatus(BaseModel):
    active: int
    waiting: int
    globalLimit: int
    perUserLimit: int


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        try:
            import json

            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


def contribution_from_row(row: dict[str, Any]) -> ContributionOut:
    return ContributionOut(
        contributionId=str(row["contribution_id"]),
        ownerId=str(row["owner_id"]),
        workspaceId=str(row["workspace_id"]) if row.get("workspace_id") else None,
        resourceType=str(row.get("resource_type") or "skill"),
        resourceRef=str(row["resource_ref"]),
        title=str(row.get("title") or ""),
        description=str(row.get("description") or ""),
        domain=row.get("domain") or "general",  # type: ignore[arg-type]
        discoveryScene=row.get("discovery_scene") or "research_materials",  # type: ignore[arg-type]
        status=row.get("status") or "private_ready",  # type: ignore[arg-type]
        systemCheckStatus=row.get("system_check_status") or "not_checked",  # type: ignore[arg-type]
        systemCheckIssues=_string_list(row.get("system_check_issues")),
        reviewNotes=str(row["review_notes"]) if row.get("review_notes") is not None else None,
        reviewerId=str(row["reviewer_id"]) if row.get("reviewer_id") else None,
        reviewedAt=str(row["reviewed_at"]) if row.get("reviewed_at") else None,
        catalogResourceId=str(row["catalog_resource_id"]) if row.get("catalog_resource_id") else None,
        releaseVersion=str(row["release_version"]) if row.get("release_version") else None,
        publishedAt=str(row["published_at"]) if row.get("published_at") else None,
        createdAt=str(row["created_at"]) if row.get("created_at") else None,
        updatedAt=str(row["updated_at"]) if row.get("updated_at") else None,
    )


def audit_from_row(row: dict[str, Any]) -> AuditLogOut:
    detail = row.get("detail") or {}
    if not isinstance(detail, dict):
        detail = {}
    return AuditLogOut(
        auditId=str(row["audit_id"]),
        action=str(row["action"]),
        actorId=str(row["actor_id"]) if row.get("actor_id") else None,
        targetId=str(row["target_id"]) if row.get("target_id") else None,
        outcome=str(row.get("outcome") or "success"),
        detail=detail,
        ip=str(row["ip"]) if row.get("ip") else None,
        createdAt=str(row["created_at"]) if row.get("created_at") else None,
    )
