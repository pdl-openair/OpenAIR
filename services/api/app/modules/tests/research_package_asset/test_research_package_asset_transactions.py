"""Real PostgreSQL proof for the Research Package v0.2 asset lifecycle.

The test uses only an explicitly named test/qa database. Package bytes use the
local content-addressed object store so the database/asset contract can be
verified without depending on MinIO availability.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest
import yaml

from app.config import settings
from app.modules.research_package_asset import (
    ResearchPackageAssetService,
    ResearchPackageConflictError,
)


FIXTURE = (
    Path(__file__).parents[4]
    / "tests"
    / "fixtures"
    / "research_packages"
    / "ag4ge3s5_engineering_v02.yaml"
)


def _test_dsn() -> str:
    dsn = os.environ.get("OPENAIR_TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        pytest.skip("OPENAIR_TEST_POSTGRES_DSN is required for the PostgreSQL Research Package test")

    parsed = urlparse(dsn)
    database = parsed.path.lstrip("/")
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not database:
        pytest.fail("OPENAIR_TEST_POSTGRES_DSN must be a complete PostgreSQL DSN")

    lowered = database.lower()
    if "test" not in lowered and "qa" not in lowered:
        pytest.fail("Research Package integration database name must contain 'test' or 'qa'")
    if any(marker in lowered for marker in ("prod", "production", "develop", "development")):
        pytest.fail("refusing Research Package integration test against a non-test PostgreSQL database")
    return dsn


def _configure_postgres(monkeypatch: pytest.MonkeyPatch, dsn: str) -> None:
    parsed = urlparse(dsn)
    monkeypatch.setattr(settings, "postgres_host", parsed.hostname or "localhost")
    monkeypatch.setattr(settings, "postgres_port", parsed.port or 5432)
    monkeypatch.setattr(settings, "postgres_user", unquote(parsed.username or "postgres"))
    monkeypatch.setattr(settings, "postgres_password", unquote(parsed.password or ""))
    monkeypatch.setattr(settings, "postgres_database", parsed.path.lstrip("/"))
    monkeypatch.setattr(settings, "persistence_backend", "postgres")


def _bootstrap(dsn: str) -> None:
    from tests.integration._production_postgres_bootstrap import bootstrap_production_postgres_schema

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            bootstrap_production_postgres_schema(cursor)


@pytest.fixture
def package_postgres(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    dsn = _test_dsn()
    _configure_postgres(monkeypatch, dsn)
    monkeypatch.setattr(settings, "artifact_storage", "local")
    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    _bootstrap(dsn)

    owner_prefix = f"rp-r2-{uuid4().hex}"
    try:
        yield dsn, owner_prefix
    finally:
        with psycopg.connect(dsn) as connection:
            connection.execute(
                "DELETE FROM openair_artifacts WHERE owner_id LIKE %s",
                (f"{owner_prefix}%",),
            )


def _package() -> dict:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


def test_real_postgres_roundtrip_and_immutable_version_fence(package_postgres) -> None:
    dsn, owner_prefix = package_postgres
    package = _package()
    source_owner = f"{owner_prefix}-source"
    target_owner = f"{owner_prefix}-target"

    source_service = ResearchPackageAssetService()
    saved = source_service.save(
        package,
        owner_id=source_owner,
        workspace_id="workspace-source",
        created_by="research-package-test",
    )
    exported = source_service.export_bytes(
        owner_id=source_owner,
        workspace_id="workspace-source",
        package_id=package["id"],
        version=package["version"],
    )

    target_service = ResearchPackageAssetService()
    imported = target_service.import_bytes(
        exported,
        owner_id=target_owner,
        workspace_id="workspace-target",
        created_by="research-package-test",
        format_hint="json",
    )
    reread = target_service.read(
        owner_id=target_owner,
        workspace_id="workspace-target",
        package_id=package["id"],
        version=package["version"],
    )

    assert imported.package.document == saved.package.document
    assert reread.package.document == saved.package.document
    assert imported.package.content_sha256 == saved.package.content_sha256
    assert reread.package.content_sha256 == saved.package.content_sha256
    assert imported.artifact_id != saved.artifact_id

    changed_same_version = copy.deepcopy(package)
    changed_same_version["summary"] = "attempted replacement under the published version"
    with pytest.raises(ResearchPackageConflictError, match="research_package_version_conflict"):
        source_service.save(
            changed_same_version,
            owner_id=source_owner,
            workspace_id="workspace-source",
            created_by="research-package-test",
        )

    with psycopg.connect(dsn) as connection:
        with pytest.raises(psycopg.errors.CheckViolation, match="research_package_version_immutable"):
            connection.execute(
                "UPDATE openair_artifacts SET summary = %s WHERE artifact_id = %s",
                ("attempted silent overwrite", saved.artifact_id),
            )
        connection.rollback()

    changed = copy.deepcopy(package)
    changed["version"] = "0.2.1"
    changed["summary"] = "new published version"
    next_version = source_service.save(
        changed,
        owner_id=source_owner,
        workspace_id="workspace-source",
        created_by="research-package-test",
    )
    assert next_version.package.version == "0.2.1"
    assert next_version.artifact_id != saved.artifact_id
