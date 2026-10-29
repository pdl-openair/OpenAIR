"""User contribution + public-publish review flow (S04-01).

Private use starts at upload. Public publication requires a bounded system check,
human review, and an immutable object-storage snapshot before catalog admission.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import RequestIdentity, require_admin, resolve_request_identity
from app.models.governance_schemas import (
    ContributionCreateRequest,
    ContributionOut,
    ContributionReviewRequest,
    contribution_from_row,
)
from app.modules.audit_repository import record_audit
from app.modules.contribution_repository import get_contribution_repository
from app.modules.user_skills_service import (
    delete_published_skill_snapshot,
    get_skill,
    publish_user_skill_snapshot,
    validate_user_skill_for_publication,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["contributions"])


def _skill_check(owner_id: str, resource_ref: str) -> dict[str, Any]:
    check = validate_user_skill_for_publication(owner_id, resource_ref)
    return {
        "status": str(check.get("status") or "needs_attention"),
        "issues": [str(item) for item in check.get("issues") or []],
    }


@router.get("/contributions/me", response_model=list[ContributionOut])
def list_my_contributions(
    identity: RequestIdentity = Depends(resolve_request_identity),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ContributionOut]:
    rows = get_contribution_repository().list_for_owner(identity["ownerId"], limit=limit, offset=offset)
    return [contribution_from_row(r) for r in rows]


@router.post("/contributions", response_model=ContributionOut, status_code=status.HTTP_201_CREATED)
def create_contribution(
    body: ContributionCreateRequest,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ContributionOut:
    """Register one uploaded resource and record its current publication readiness."""
    repo = get_contribution_repository()
    existing = repo.find_for_owner_resource(identity["ownerId"], body.resourceType, body.resourceRef)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "contribution_exists",
                "message": "该资源已经进入贡献流程，请在现有记录中继续操作",
                "contributionId": existing["contribution_id"],
            },
        )

    if body.resourceType == "skill":
        skill = get_skill(identity["ownerId"], body.resourceRef)
        if skill is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="未找到已上传的资源，请先上传后再提交贡献",
            )
        check = _skill_check(identity["ownerId"], body.resourceRef)
    else:
        check = {"status": "not_checked", "issues": ["首版仅支持上传技能的公开贡献"]}

    can_submit = body.resourceType == "skill" and check["status"] == "passed"
    status_value = "pending_public_review" if body.requestPublic and can_submit else "private_ready"
    row = repo.create(
        owner_id=identity["ownerId"],
        workspace_id=identity.get("workspaceId"),
        resource_type=body.resourceType,
        resource_ref=body.resourceRef,
        title=body.title,
        description=body.description,
        domain=body.domain,
        discovery_scene=body.discoveryScene,
        status=status_value,
        system_check_status=check["status"],
        system_check_issues=check["issues"],
    )
    record_audit(
        "contribution_register",
        actor_id=identity["ownerId"],
        target_id=row["contribution_id"],
        outcome="success",
        detail={
            "status": status_value,
            "resourceType": body.resourceType,
            "resourceRef": body.resourceRef,
            "systemCheckStatus": check["status"],
        },
    )
    return contribution_from_row(row)


@router.post("/contributions/{contribution_id}/submit-public", response_model=ContributionOut)
def submit_for_public(
    contribution_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ContributionOut:
    """Re-check the current private bytes before moving into human review."""
    repo = get_contribution_repository()
    row = repo.get(contribution_id)
    if not row or row.get("owner_id") != identity["ownerId"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="contribution not found")
    if row.get("status") not in ("private_ready", "rejected", "withdrawn"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"cannot submit from status={row.get('status')}",
        )
    if row.get("resource_type") != "skill":
        raise HTTPException(status_code=409, detail="首版仅支持上传技能的公开贡献")

    check = _skill_check(identity["ownerId"], str(row["resource_ref"]))
    next_status = "pending_public_review" if check["status"] == "passed" else str(row["status"])
    updated = repo.update_status(
        contribution_id,
        status=next_status,
        system_check_status=check["status"],
        system_check_issues=check["issues"],
        clear_review=next_status == "pending_public_review",
    )
    record_audit(
        "contribution_submit_public",
        actor_id=identity["ownerId"],
        target_id=contribution_id,
        outcome="success" if next_status == "pending_public_review" else "blocked",
        detail={"systemCheckStatus": check["status"], "issues": check["issues"]},
    )
    return contribution_from_row(updated)  # type: ignore[arg-type]


@router.post("/contributions/{contribution_id}/withdraw", response_model=ContributionOut)
def withdraw_contribution(
    contribution_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ContributionOut:
    repo = get_contribution_repository()
    row = repo.get(contribution_id)
    if not row or row.get("owner_id") != identity["ownerId"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="contribution not found")
    if row.get("status") not in ("pending_public_review", "private_ready"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="cannot withdraw in current status")
    updated = repo.update_status(contribution_id, status="withdrawn")
    return contribution_from_row(updated)  # type: ignore[arg-type]


@router.get("/contributions/public", response_model=list[ContributionOut])
def list_public_catalog(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ContributionOut]:
    """Approved contributions that also carry an admitted catalog identity."""
    rows = get_contribution_repository().list_public_catalog(limit=limit, offset=offset)
    return [contribution_from_row(r) for r in rows if r.get("catalog_resource_id")]


@router.get("/admin/contributions/pending", response_model=list[ContributionOut])
def list_pending_reviews(
    _admin: RequestIdentity = Depends(require_admin),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ContributionOut]:
    rows = get_contribution_repository().list_by_status("pending_public_review", limit=limit, offset=offset)
    return [contribution_from_row(r) for r in rows]


@router.post("/admin/contributions/{contribution_id}/review", response_model=ContributionOut)
def review_contribution(
    contribution_id: str,
    body: ContributionReviewRequest,
    identity: RequestIdentity = Depends(require_admin),
) -> ContributionOut:
    repo = get_contribution_repository()
    row = repo.get(contribution_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="contribution not found")
    if row.get("status") != "pending_public_review":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="not pending review")

    if body.decision == "reject":
        notes = body.reviewNotes.strip()
        if not notes:
            raise HTTPException(status_code=400, detail="驳回时必须填写明确的修改意见")
        updated = repo.update_status(
            contribution_id,
            status="rejected",
            review_notes=notes,
            reviewer_id=identity["ownerId"],
        )
        record_audit(
            "contribution_review",
            actor_id=identity["ownerId"],
            target_id=contribution_id,
            outcome="success",
            detail={"decision": "reject", "status": "rejected"},
        )
        return contribution_from_row(updated)  # type: ignore[arg-type]

    if row.get("resource_type") != "skill":
        raise HTTPException(status_code=409, detail="首版仅支持技能贡献进入资源目录")

    check = _skill_check(str(row["owner_id"]), str(row["resource_ref"]))
    if check["status"] != "passed":
        repo.update_status(
            contribution_id,
            status="pending_public_review",
            system_check_status=check["status"],
            system_check_issues=check["issues"],
        )
        record_audit(
            "contribution_review",
            actor_id=identity["ownerId"],
            target_id=contribution_id,
            outcome="blocked",
            detail={"decision": "approve", "issues": check["issues"]},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "system_check_failed",
                "message": "资源在审核期间发生变化，系统检查未通过",
                "issues": check["issues"],
            },
        )

    catalog_resource_id = f"contribution:{contribution_id}"
    try:
        manifest = publish_user_skill_snapshot(
            str(row["owner_id"]),
            str(row["resource_ref"]),
            contribution_id,
        )
        release_version = str(manifest.pop("releaseVersion"))
        updated = repo.update_status(
            contribution_id,
            status="approved_public",
            review_notes=body.reviewNotes.strip() or None,
            reviewer_id=identity["ownerId"],
            system_check_status="passed",
            system_check_issues=[],
            catalog_resource_id=catalog_resource_id,
            release_version=release_version,
            published_manifest=manifest,
            mark_published=True,
        )
        if updated is None:
            raise RuntimeError("contribution disappeared during publication")
    except Exception as exc:
        delete_published_skill_snapshot(contribution_id)
        logger.exception("contribution publication failed: %s", contribution_id)
        raise HTTPException(status_code=500, detail="发布快照失败，贡献仍保持待审核状态") from exc

    from app.modules.resource_catalog import clear_resource_catalog_cache

    clear_resource_catalog_cache()
    record_audit(
        "contribution_review",
        actor_id=identity["ownerId"],
        target_id=contribution_id,
        outcome="success",
        detail={
            "decision": "approve",
            "status": "approved_public",
            "catalogResourceId": catalog_resource_id,
            "releaseVersion": release_version,
        },
    )
    return contribution_from_row(updated)
