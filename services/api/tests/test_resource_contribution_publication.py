from __future__ import annotations

import io
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from app.models.governance_schemas import ContributionCreateRequest, ContributionReviewRequest
from app.modules import user_skills_service

PSYCOPG_STUBBED = False
try:
    import psycopg  # noqa: F401
except ModuleNotFoundError:
    PSYCOPG_STUBBED = True
    psycopg_stub = types.ModuleType("psycopg")
    psycopg_stub.connect = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("PostgreSQL is not used by this focused JSON repository test")
    )
    psycopg_rows_stub = types.ModuleType("psycopg.rows")
    psycopg_rows_stub.dict_row = object()
    sys.modules["psycopg"] = psycopg_stub
    sys.modules["psycopg.rows"] = psycopg_rows_stub

from app.modules.contribution_repository import JsonContributionRepository

try:
    import app.auth  # noqa: F401
except ModuleNotFoundError:
    auth_stub = types.ModuleType("app.auth")
    auth_stub.RequestIdentity = dict
    auth_stub.require_admin = lambda: ADMIN if "ADMIN" in globals() else {"ownerId": "admin_demo"}
    auth_stub.resolve_request_identity = lambda: USER if "USER" in globals() else {"ownerId": "user_demo"}
    sys.modules["app.auth"] = auth_stub

if PSYCOPG_STUBBED:
    audit_stub = types.ModuleType("app.modules.audit_repository")
    audit_stub.record_audit = lambda *args, **kwargs: None
    sys.modules["app.modules.audit_repository"] = audit_stub

from app.routes import contributions as contribution_routes


USER = {"ownerId": "user_demo", "workspaceId": "workspace_demo", "role": "user"}
ADMIN = {"ownerId": "admin_demo", "workspaceId": None, "role": "admin"}


def _passed_check() -> dict[str, Any]:
    return {
        "status": "passed",
        "issues": [],
        "name": "Evidence helper",
        "description": "Organises evidence records.",
        "input": ["research_question"],
        "output": ["evidence_table"],
        "files": ["SKILL.md", "input.schema.json", "output.schema.json"],
    }


def test_contribution_moves_from_private_check_to_review_and_catalog(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = JsonContributionRepository(tmp_path)
    repo.bootstrap()
    audits: list[tuple[str, str]] = []

    monkeypatch.setattr(contribution_routes, "get_contribution_repository", lambda: repo)
    monkeypatch.setattr(contribution_routes, "get_skill", lambda owner_id, resource_ref: {"skill_name": resource_ref})
    monkeypatch.setattr(contribution_routes, "validate_user_skill_for_publication", lambda *_: _passed_check())
    monkeypatch.setattr(
        contribution_routes,
        "publish_user_skill_snapshot",
        lambda *_: {
            "snapshotPrefix": "published-skills/ctrb_demo/",
            "files": ["SKILL.md"],
            "name": "Evidence helper",
            "description": "Organises evidence records.",
            "input": ["research_question"],
            "output": ["evidence_table"],
            "releaseVersion": "sha256:abc123",
        },
    )
    monkeypatch.setattr(contribution_routes, "delete_published_skill_snapshot", lambda *_: None)
    monkeypatch.setattr(
        contribution_routes,
        "record_audit",
        lambda action, **kwargs: audits.append((action, str(kwargs.get("outcome")))),
    )
    catalog_stub = types.ModuleType("app.modules.resource_catalog")
    catalog_stub.clear_resource_catalog_cache = lambda: None
    monkeypatch.setitem(sys.modules, "app.modules.resource_catalog", catalog_stub)

    created = contribution_routes.create_contribution(
        ContributionCreateRequest(
            resourceRef="evidence-helper",
            title="证据整理助手",
            description="整理研究问题对应的证据记录",
            domain="general",
            discoveryScene="research_materials",
        ),
        USER,  # type: ignore[arg-type]
    )
    assert created.status == "private_ready"
    assert created.systemCheckStatus == "passed"

    submitted = contribution_routes.submit_for_public(created.contributionId, USER)  # type: ignore[arg-type]
    assert submitted.status == "pending_public_review"

    approved = contribution_routes.review_contribution(
        created.contributionId,
        ContributionReviewRequest(decision="approve", reviewNotes="结构清楚"),
        ADMIN,  # type: ignore[arg-type]
    )
    assert approved.status == "approved_public"
    assert approved.catalogResourceId == f"contribution:{created.contributionId}"
    assert approved.releaseVersion == "sha256:abc123"
    assert approved.publishedAt is not None
    assert ("contribution_review", "success") in audits


def test_failed_system_check_stays_private_and_reject_requires_notes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = JsonContributionRepository(tmp_path)
    repo.bootstrap()
    check = {
        "status": "needs_attention",
        "issues": ["SKILL.md frontmatter 缺少 description"],
        "name": "bad",
        "description": "",
        "input": [],
        "output": [],
        "files": ["SKILL.md"],
    }
    monkeypatch.setattr(contribution_routes, "get_contribution_repository", lambda: repo)
    monkeypatch.setattr(contribution_routes, "get_skill", lambda *_: {"skill_name": "bad"})
    monkeypatch.setattr(contribution_routes, "validate_user_skill_for_publication", lambda *_: check)
    monkeypatch.setattr(contribution_routes, "record_audit", lambda *args, **kwargs: None)

    created = contribution_routes.create_contribution(
        ContributionCreateRequest(
            resourceRef="bad",
            title="待完善资源",
            domain="general",
            discoveryScene="research_quality",
            requestPublic=True,
        ),
        USER,  # type: ignore[arg-type]
    )
    assert created.status == "private_ready"
    assert created.systemCheckStatus == "needs_attention"
    assert created.systemCheckIssues == check["issues"]

    blocked = contribution_routes.submit_for_public(created.contributionId, USER)  # type: ignore[arg-type]
    assert blocked.status == "private_ready"

    pending = repo.update_status(created.contributionId, status="pending_public_review")
    assert pending is not None
    with pytest.raises(HTTPException) as exc:
        contribution_routes.review_contribution(
            created.contributionId,
            ContributionReviewRequest(decision="reject", reviewNotes=""),
            ADMIN,  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 400


def test_catalog_source_projects_approved_contributions_without_false_tryable() -> None:
    source_path = Path(__file__).resolve().parents[1] / "app" / "modules" / "resource_catalog.py"
    source = source_path.read_text(encoding="utf-8")
    compile(source, str(source_path), "exec")
    projector = source[
        source.index("def _project_approved_contributions") : source.index("def _is_catalog_visible")
    ]
    catalog_loader = source[source.index("def _build_catalog_resources") : source.index("def _catalog_resources")]

    assert "list_public_catalog" in projector
    assert "ResourceAvailability.CONFIGURATION_REQUIRED" in projector
    assert "execution_blocked=True" in projector
    assert "durable_executable=False" in projector
    assert "execution_entry=None" in projector
    assert "用户公开贡献" in projector
    assert "OpenAIR 社区" in projector
    assert "_project_approved_contributions()" in catalog_loader
    assert "Public catalog contains duplicate resource IDs" in catalog_loader



@dataclass
class _Object:
    object_name: str
    content_type: str = "application/octet-stream"


class _Response(io.BytesIO):
    def release_conn(self) -> None:
        return None


class _FakeMinio:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = dict(objects)

    def list_objects(self, bucket: str, *, prefix: str, recursive: bool) -> list[_Object]:
        return [_Object(key) for key in sorted(self.objects) if key.startswith(prefix)]

    def get_object(self, bucket: str, object_key: str) -> _Response:
        return _Response(self.objects[object_key])

    def put_object(
        self,
        bucket: str,
        object_key: str,
        *,
        data: Any,
        length: int,
        content_type: str,
    ) -> None:
        self.objects[object_key] = data.read(length)

    def remove_object(self, bucket: str, object_key: str) -> None:
        self.objects.pop(object_key, None)


def test_publication_freezes_bytes_and_structural_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    private_prefix = "user-skills/user_demo/evidence-helper/"
    skill_md = b"""---\nname: Evidence helper\ndescription: Organises evidence records.\n---\nUse only the supplied records.\n"""
    fake = _FakeMinio(
        {
            f"{private_prefix}SKILL.md": skill_md,
            f"{private_prefix}input.schema.json": b'{"type":"object","required":["research_question"]}',
            f"{private_prefix}output.schema.json": b'{"type":"object","required":["evidence_table"]}',
        }
    )
    monkeypatch.setattr(user_skills_service, "is_minio_enabled", lambda: True)
    monkeypatch.setattr(user_skills_service, "_ensure_minio", lambda: (fake, "openair"))

    check = user_skills_service.validate_user_skill_for_publication("user_demo", "evidence-helper")
    assert check["status"] == "passed"
    assert check["input"] == ["research_question"]
    assert check["output"] == ["evidence_table"]

    manifest = user_skills_service.publish_user_skill_snapshot(
        "user_demo",
        "evidence-helper",
        "ctrb_123",
    )
    public_key = "published-skills/ctrb_123/SKILL.md"
    assert fake.objects[public_key] == skill_md
    assert manifest["releaseVersion"].startswith("sha256:")

    fake.objects[f"{private_prefix}SKILL.md"] = b"changed after approval"
    assert fake.objects[public_key] == skill_md


def test_resource_contribution_migration_has_up_and_down_contract() -> None:
    migrations = Path(__file__).resolve().parents[1] / "migrations"
    up = (migrations / "20260806_02_resource_contribution_catalog.sql").read_text(encoding="utf-8")
    down = (migrations / "20260806_02_resource_contribution_catalog_down.sql").read_text(encoding="utf-8")
    for column in (
        "system_check_status",
        "catalog_resource_id",
        "release_version",
        "published_manifest",
    ):
        assert column in up
        assert column in down
    assert "idx_contributions_catalog_resource" in up
    assert "DELETE FROM openair_schema_migrations" in down
