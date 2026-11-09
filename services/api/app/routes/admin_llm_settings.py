"""Admin LLM gateway settings: view, save (hot-apply) and test connection."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import RequestIdentity, require_admin
from app.modules import llm_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/llm-settings", tags=["admin-llm-settings"])


class LlmSettingsUpdateRequest(BaseModel):
    anthropicBaseUrl: str | None = None
    anthropicApiKey: str | None = Field(default=None, max_length=512)
    anthropicAuthToken: str | None = Field(default=None, max_length=512)
    claudeModel: str | None = None
    providerBaseUrl: str | None = None
    providerApiKey: str | None = Field(default=None, max_length=512)
    providerModel: str | None = None
    plannerModel: str | None = None


class LlmSettingsTestRequest(BaseModel):
    # Test-before-save support: probe with candidate values without persisting.
    anthropicBaseUrl: str | None = None
    anthropicApiKey: str | None = Field(default=None, max_length=512)
    anthropicAuthToken: str | None = Field(default=None, max_length=512)
    claudeModel: str | None = None
    providerBaseUrl: str | None = None
    providerApiKey: str | None = Field(default=None, max_length=512)
    providerModel: str | None = None
    plannerModel: str | None = None


@router.get("")
def get_llm_settings(
    _admin: RequestIdentity = Depends(require_admin),
) -> dict[str, Any]:
    return llm_settings.masked_view()


@router.put("")
def update_llm_settings(
    body: LlmSettingsUpdateRequest,
    _admin: RequestIdentity = Depends(require_admin),
) -> dict[str, Any]:
    payload = body.model_dump(exclude_none=True)
    try:
        changed = llm_settings.save(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if changed:
        logger.info(
            "admin updated llm settings fields: %s",
            ", ".join(sorted(field for field in changed if changed[field])),
        )
    return {"changed": sorted(changed), "settings": llm_settings.masked_view()}


@router.post("/test")
def test_llm_settings(
    body: LlmSettingsTestRequest | None = None,
    _admin: RequestIdentity = Depends(require_admin),
) -> dict[str, Any]:
    overrides = body.model_dump(exclude_none=True) if body is not None else None
    return llm_settings.test_connection(overrides)
