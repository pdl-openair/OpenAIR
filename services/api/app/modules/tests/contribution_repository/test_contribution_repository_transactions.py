"""Real PostgreSQL regression coverage for contribution review updates."""

from __future__ import annotations

import os
from uuid import uuid4
from urllib.parse import urlparse

import psycopg
import pytest

from app.config import settings
from app.modules.contribution_repository import ContributionRepository


@pytest.fixture
def postgres_contribution_repository(monkeypatch: pytest.MonkeyPatch):
    dsn = os.environ.get("OPENAIR_TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        pytest.skip("OPENAIR_TEST_POSTGRES_DSN is required for the PostgreSQL regression")
    parsed = urlparse(dsn)
    database = parsed.path.lstrip("/")
    if parsed.scheme not in {"postgres", "postgresql"} or not database:
        pytest.fail("OPENAIR_TEST_POSTGRES_DSN must be a PostgreSQL DSN")
    if "test" not in database.lower() and "qa" not in database.lower():
        pytest.fail("refusing contribution regression against a non-test PostgreSQL database")

    monkeypatch.setattr(settings, "postgres_host", parsed.hostname or "localhost")
    monkeypatch.setattr(settings, "postgres_port", parsed.port or 5432)
    monkeypatch.setattr(settings, "postgres_user", parsed.username or "postgres")
    monkeypatch.setattr(settings, "postgres_password", parsed.password or "")
    monkeypatch.setattr(settings, "postgres_database", database)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")

    repository = ContributionRepository()
    repository.bootstrap()
    owner_id = f"contribution-pg-regression-{uuid4().hex}"
    try:
        yield repository, owner_id
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(
                "DELETE FROM openair_contributions WHERE owner_id = %s",
                (owner_id,),
            )


def test_update_status_types_nullable_review_parameters(postgres_contribution_repository) -> None:
    repository, owner_id = postgres_contribution_repository
    created = repository.create(
        owner_id=owner_id,
        workspace_id="contribution-pg-regression-workspace",
        resource_type="skill",
        resource_ref=f"regression-skill-{uuid4().hex}",
        title="PostgreSQL contribution regression",
        system_check_status="passed",
    )

    reviewed = repository.update_status(
        created["contribution_id"],
        status="rejected",
        review_notes="initial review",
        reviewer_id="initial-reviewer",
    )
    assert reviewed is not None
    assert reviewed["reviewed_at"] is not None

    preserved = repository.update_status(
        created["contribution_id"],
        status="pending_public_review",
        review_notes=None,
        reviewer_id=None,
        clear_review=False,
    )
    assert preserved is not None
    assert preserved["review_notes"] == "initial review"
    assert preserved["reviewer_id"] == "initial-reviewer"
    assert preserved["reviewed_at"] is not None

    cleared = repository.update_status(
        created["contribution_id"],
        status="pending_public_review",
        review_notes=None,
        reviewer_id=None,
        clear_review=True,
    )
    assert cleared is not None
    assert cleared["status"] == "pending_public_review"
    assert cleared["review_notes"] is None
    assert cleared["reviewer_id"] is None
    assert cleared["reviewed_at"] is None

    updated = repository.update_status(
        created["contribution_id"],
        status="rejected",
        review_notes="actionable rejection reason",
        reviewer_id="admin-reviewer",
        clear_review=False,
    )
    assert updated is not None
    assert updated["status"] == "rejected"
    assert updated["review_notes"] == "actionable rejection reason"
    assert updated["reviewer_id"] == "admin-reviewer"
    assert updated["reviewed_at"] is not None
