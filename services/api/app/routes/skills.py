"""Skills API routes — system public skills + user private skills CRUD.

Endpoints
---------
GET  /api/skills/system              — List system (public) skills
GET  /api/skills/user                — List current user's private skills
POST /api/skills/user                — Upload a private skill (multipart)
GET  /api/skills/user/{skill_name}   — Get detail of a private skill
DELETE /api/skills/user/{skill_name} — Delete a private skill
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.auth import RequestIdentity, resolve_request_identity
from app.models.skill_schemas import SystemSkillOut, UserSkillDetailOut, UserSkillOut
from app.modules.user_skills_service import (
    delete_skill,
    get_skill,
    list_skills,
    load_system_skills_registry,
    upload_skill,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/skills", tags=["skills"])

# TypedDict uses camelCase keys: ownerId, workspaceId, createdBy
_OWNER_ID_KEY = "ownerId"


def _invalidate_owner_warm_workers(owner_id: str) -> None:
    """Bump the owner's skill generation after a skill upload/delete.

    A keepalive worker caches the owner's user skills at bootstrap; without this, a
    mid-task skill change wouldn't be visible to subsequent turns until the worker
    expired/was evicted (it would keep running stale scripts). Bumping the gen forces a
    fresh launch on the next turn. No-op when keepalive is off; never fails the upload.
    """
    try:
        from app.execution.worker_pool import get_worker_pool

        get_worker_pool().invalidate_owner_skills(owner_id)
    except Exception:
        logger.warning("could not invalidate warm workers for owner=%s", owner_id, exc_info=True)


# ---------------------------------------------------------------------------
# System skills (public, read-only)
# ---------------------------------------------------------------------------

@router.get("/system", response_model=list[SystemSkillOut])
async def list_system_skills() -> list[dict[str, Any]]:
    """List all system (public) skills from the registry."""
    skills = load_system_skills_registry()
    return skills


# ---------------------------------------------------------------------------
# User skills (private, authenticated)
# ---------------------------------------------------------------------------

@router.get("/user", response_model=list[UserSkillOut])
async def list_user_skills(
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> list[dict[str, Any]]:
    """List the current user's private skills."""
    owner_id = identity.get(_OWNER_ID_KEY, "")
    if not owner_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return list_skills(owner_id)


@router.post("/user", response_model=UserSkillOut)
async def upload_user_skill(
    skill_name: str = Form(..., description="Skill directory name"),
    files: list[UploadFile] = File(..., description="Skill files (SKILL.md required)"),
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> dict[str, Any]:
    """Upload a new user-private skill.

    Accepts multipart form data with a ``skill_name`` field and one or more files.
    At minimum, ``SKILL.md`` must be included.
    """
    owner_id = identity.get(_OWNER_ID_KEY, "")
    if not owner_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        result = upload_skill(owner_id, skill_name, files)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("upload_user_skill failed")
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc

    _invalidate_owner_warm_workers(owner_id)
    return result


@router.get("/user/{skill_name}", response_model=UserSkillDetailOut)
async def get_user_skill(
    skill_name: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> dict[str, Any]:
    """Get details of a specific user-private skill."""
    owner_id = identity.get(_OWNER_ID_KEY, "")
    if not owner_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    result = get_skill(owner_id, skill_name)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    return result


@router.delete("/user/{skill_name}")
async def delete_user_skill(
    skill_name: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> dict[str, str]:
    """Delete a user-private skill."""
    owner_id = identity.get(_OWNER_ID_KEY, "")
    if not owner_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    deleted = delete_skill(owner_id, skill_name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    _invalidate_owner_warm_workers(owner_id)
    return {"status": "deleted", "skill_name": skill_name}
