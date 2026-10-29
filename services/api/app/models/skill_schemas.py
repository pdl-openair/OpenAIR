"""Pydantic models for Skills API endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class UserSkillOut(BaseModel):
    """A user-private skill's metadata."""
    skill_name: str
    description: str = ""
    created_at: str = ""
    files: list[str] = []


class UserSkillDetailOut(BaseModel):
    """Full detail of a user-private skill."""
    skill_name: str
    description: str = ""
    created_at: str = ""
    files: list[str] = []
    skill_yaml: dict[str, Any] | None = None
    skill_md_preview: str = ""


class SystemSkillOut(BaseModel):
    """A system (public) skill from the registry."""
    skill_id: str
    claude_name: str = ""
    display_name: str = ""
    domain: str = ""
    stages: list[str] = []
    substages: list[str] = []
    skill_type: str = ""
    status: str = ""
    description: str = ""
