"""Regression tests for user_skills_service storage guards."""

from __future__ import annotations

import pytest

from app.modules import user_skills_service


def _forbid_minio_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        user_skills_service,
        "_ensure_minio",
        lambda: (_ for _ in ()).throw(AssertionError("minio client should not be touched")),
    )


def test_list_skills_returns_empty_when_minio_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(user_skills_service, "is_minio_enabled", lambda: False)
    _forbid_minio_access(monkeypatch)
    assert user_skills_service.list_skills("user_demo") == []


def test_get_skill_returns_none_when_minio_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(user_skills_service, "is_minio_enabled", lambda: False)
    _forbid_minio_access(monkeypatch)
    assert user_skills_service.get_skill("user_demo", "demo-skill") is None


def test_delete_skill_returns_false_when_minio_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(user_skills_service, "is_minio_enabled", lambda: False)
    _forbid_minio_access(monkeypatch)
    assert user_skills_service.delete_skill("user_demo", "demo-skill") is False


def test_upload_skill_fails_fast_when_minio_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(user_skills_service, "is_minio_enabled", lambda: False)
    _forbid_minio_access(monkeypatch)
    with pytest.raises(ValueError, match="storage is disabled"):
        user_skills_service.upload_skill("user_demo", "demo-skill", [])
