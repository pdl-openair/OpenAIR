from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.modules import object_store
from app.modules.research_package_asset import (
    ResearchPackageAssetService,
    ResearchPackageConflictError,
    package_artifact_id,
)


FIXTURE = Path(__file__).parent / "fixtures" / "research_packages" / "ag4ge3s5_engineering_v02.yaml"


class MemoryArtifactRepository:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.write_count = 0

    def get_by_id(self, artifact_id: str) -> dict[str, Any] | None:
        row = self.rows.get(artifact_id)
        return copy.deepcopy(row) if row is not None else None

    def insert_from_dict_if_absent(
        self, task_id: str | None, artifact: dict[str, Any], task_title: str | None = None
    ) -> bool:
        artifact_id = str(artifact["id"])
        if artifact_id in self.rows:
            return False
        self.write_count += 1
        self.rows[artifact_id] = copy.deepcopy(artifact)
        return True


def _package() -> dict[str, Any]:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture()
def content_store(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    blobs: dict[str, bytes] = {}

    def put(data: bytes, *, content_type: str = "application/octet-stream") -> str:
        import hashlib

        digest = hashlib.sha256(data).hexdigest()
        blobs.setdefault(digest, data)
        return digest

    def get(digest: str) -> bytes:
        if digest not in blobs:
            raise FileNotFoundError(digest)
        return blobs[digest]

    monkeypatch.setattr(object_store, "put_content", put)
    monkeypatch.setattr(object_store, "get_content", get)
    return blobs


def test_round_trip_preserves_identity_sources_and_scientific_semantics(content_store: dict[str, bytes]) -> None:
    package = _package()
    source_repo = MemoryArtifactRepository()
    source_service = ResearchPackageAssetService(source_repo)

    saved = source_service.save(
        package,
        owner_id="owner-a",
        workspace_id="workspace-a",
        created_by="user-a",
        source_task_id="task-a",
    )
    exported = source_service.export_bytes(
        owner_id="owner-a",
        workspace_id="workspace-a",
        package_id=package["id"],
        version=package["version"],
    )

    target_repo = MemoryArtifactRepository()
    imported = ResearchPackageAssetService(target_repo).import_bytes(
        exported,
        owner_id="owner-b",
        workspace_id="workspace-b",
        created_by="user-b",
        format_hint="json",
    )

    assert imported.package.document == saved.package.document
    assert imported.package.package_id == saved.package.package_id
    assert imported.package.version == saved.package.version
    assert imported.package.spec_version == saved.package.spec_version
    assert imported.package.document["sources"] == saved.package.document["sources"]
    assert imported.package.document["method"] == saved.package.document["method"]
    assert imported.package.document["reference_cases"] == saved.package.document["reference_cases"]
    assert imported.package.document["applicability"] == saved.package.document["applicability"]
    assert imported.package.content_sha256 == saved.package.content_sha256
    assert imported.artifact_id != saved.artifact_id


def test_same_published_id_version_and_same_content_is_idempotent(content_store: dict[str, bytes]) -> None:
    package = _package()
    repo = MemoryArtifactRepository()
    service = ResearchPackageAssetService(repo)

    first = service.save(
        package,
        owner_id="owner",
        workspace_id="workspace",
        created_by="user",
    )
    second = service.save(
        copy.deepcopy(package),
        owner_id="owner",
        workspace_id="workspace",
        created_by="user",
    )

    assert second.artifact_id == first.artifact_id
    assert repo.write_count == 1


def test_same_published_id_version_cannot_be_overwritten(content_store: dict[str, bytes]) -> None:
    package = _package()
    repo = MemoryArtifactRepository()
    service = ResearchPackageAssetService(repo)
    service.save(package, owner_id="owner", workspace_id="workspace", created_by="user")

    changed = copy.deepcopy(package)
    changed["summary"] = "changed content under the same published version"

    with pytest.raises(ResearchPackageConflictError, match="research_package_version_conflict"):
        service.save(changed, owner_id="owner", workspace_id="workspace", created_by="user")


def test_changed_content_is_allowed_under_a_new_package_version(content_store: dict[str, bytes]) -> None:
    package = _package()
    repo = MemoryArtifactRepository()
    service = ResearchPackageAssetService(repo)
    first = service.save(package, owner_id="owner", workspace_id="workspace", created_by="user")

    changed = copy.deepcopy(package)
    changed["version"] = "0.2.1"
    changed["summary"] = "new published version"
    second = service.save(changed, owner_id="owner", workspace_id="workspace", created_by="user")

    assert second.artifact_id != first.artifact_id
    assert second.package.version == "0.2.1"
    assert repo.write_count == 2


def test_artifact_identity_is_stable_within_scope() -> None:
    kwargs = dict(owner_id="owner", workspace_id="workspace", package_id="package", version="1.0")
    assert package_artifact_id(**kwargs) == package_artifact_id(**kwargs)
    assert package_artifact_id(**kwargs) != package_artifact_id(**{**kwargs, "workspace_id": "other"})


def test_corrupt_persisted_content_fails_closed(content_store: dict[str, bytes]) -> None:
    package = _package()
    repo = MemoryArtifactRepository()
    service = ResearchPackageAssetService(repo)
    saved = service.save(package, owner_id="owner", workspace_id="workspace", created_by="user")

    digest = saved.package.content_sha256
    content_store[digest] = b"{}"

    with pytest.raises(ResearchPackageConflictError, match="research_package_content_unavailable_or_invalid"):
        service.read(
            owner_id="owner",
            workspace_id="workspace",
            package_id=package["id"],
            version=package["version"],
        )
