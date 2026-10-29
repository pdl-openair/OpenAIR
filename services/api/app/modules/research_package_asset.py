"""Research Package asset lifecycle on top of canonical Artifact storage."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from app.modules import object_store
from app.modules.artifact_content import load_artifact_binary
from app.modules.research_package_protocol import (
    ResearchPackageValidationError,
    ValidatedResearchPackage,
    load_and_validate_research_package,
    validate_research_package,
)


RESEARCH_PACKAGE_KIND = "research_package"
RESEARCH_PACKAGE_FORMAT = "json"
RESEARCH_PACKAGE_MIME_TYPE = "application/json"


class ResearchPackageConflictError(RuntimeError):
    """The same published package identity already exists with different bytes."""


class ResearchPackageNotFoundError(KeyError):
    pass


class _ArtifactRepository(Protocol):
    def get_by_id(self, artifact_id: str) -> dict[str, Any] | None: ...

    def insert_from_dict_if_absent(
        self, task_id: str | None, artifact: dict[str, Any], task_title: str | None = None
    ) -> bool: ...


@dataclass(frozen=True)
class ResearchPackageAsset:
    artifact_id: str
    package: ValidatedResearchPackage
    source_task_id: str | None


class ResearchPackageAssetService:
    """Persist immutable package versions without introducing a second asset store."""

    def __init__(self, repository: _ArtifactRepository | None = None) -> None:
        if repository is None:
            # Keep the protocol/round-trip core importable without PostgreSQL;
            # production persistence still resolves to the canonical ArtifactRepository.
            from app.modules.artifact_repository import ArtifactRepository

            repository = ArtifactRepository()
        self._repository = repository

    def save(
        self,
        package: dict[str, Any],
        *,
        owner_id: str,
        workspace_id: str,
        created_by: str,
        source_task_id: str | None = None,
        origin: str = "generated",
    ) -> ResearchPackageAsset:
        validated = validate_research_package(package)
        artifact_id = package_artifact_id(
            owner_id=owner_id,
            workspace_id=workspace_id,
            package_id=validated.package_id,
            version=validated.version,
        )
        existing = self._repository.get_by_id(artifact_id)
        if existing is not None:
            return self._existing_asset(
                existing,
                expected=validated,
                owner_id=owner_id,
                workspace_id=workspace_id,
            )

        content_sha256 = object_store.put_content(
            validated.canonical_bytes,
            content_type=f"{RESEARCH_PACKAGE_MIME_TYPE}; charset=utf-8",
        )
        if content_sha256 != validated.content_sha256:
            raise RuntimeError("research_package_content_store_hash_mismatch")

        now = datetime.now(timezone.utc).isoformat()
        artifact = {
            "id": artifact_id,
            "ownerId": owner_id,
            "workspaceId": workspace_id,
            "createdBy": created_by,
            "kind": RESEARCH_PACKAGE_KIND,
            "artifactType": RESEARCH_PACKAGE_KIND,
            "origin": origin,
            "title": validated.document["name"],
            "summary": validated.document["summary"],
            "format": RESEARCH_PACKAGE_FORMAT,
            "mimeType": RESEARCH_PACKAGE_MIME_TYPE,
            "storageType": "object",
            "contentSha256": content_sha256,
            "contentSize": len(validated.canonical_bytes),
            "sourceTaskId": source_task_id,
            "createdAt": now,
            "updatedAt": now,
            "metadata": {
                "researchPackageId": validated.package_id,
                "researchPackageVersion": validated.version,
                "researchPackageSpecVersion": validated.spec_version,
                "researchPackageContentSha256": content_sha256,
            },
        }
        self._repository.insert_from_dict_if_absent(source_task_id, artifact)
        persisted = self._repository.get_by_id(artifact_id)
        if persisted is None:
            raise RuntimeError("research_package_write_not_persisted")
        return self._existing_asset(
            persisted,
            expected=validated,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )

    def import_bytes(
        self,
        raw: bytes,
        *,
        owner_id: str,
        workspace_id: str,
        created_by: str,
        format_hint: str | None = None,
        source_task_id: str | None = None,
    ) -> ResearchPackageAsset:
        validated = load_and_validate_research_package(raw, format_hint=format_hint)
        return self.save(
            validated.document,
            owner_id=owner_id,
            workspace_id=workspace_id,
            created_by=created_by,
            source_task_id=source_task_id,
            origin="imported",
        )

    def read(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        package_id: str,
        version: str,
    ) -> ResearchPackageAsset:
        artifact_id = package_artifact_id(
            owner_id=owner_id,
            workspace_id=workspace_id,
            package_id=package_id,
            version=version,
        )
        artifact = self._repository.get_by_id(artifact_id)
        if artifact is None:
            raise ResearchPackageNotFoundError("research_package_not_found")
        return self._existing_asset(
            artifact,
            expected_identity=(package_id, version),
            owner_id=owner_id,
            workspace_id=workspace_id,
        )

    def export_bytes(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        package_id: str,
        version: str,
    ) -> bytes:
        return self.read(
            owner_id=owner_id,
            workspace_id=workspace_id,
            package_id=package_id,
            version=version,
        ).package.canonical_bytes

    def _existing_asset(
        self,
        artifact: dict[str, Any],
        *,
        owner_id: str,
        workspace_id: str,
        expected: ValidatedResearchPackage | None = None,
        expected_identity: tuple[str, str] | None = None,
    ) -> ResearchPackageAsset:
        if artifact.get("ownerId") != owner_id or artifact.get("workspaceId") != workspace_id:
            raise ResearchPackageNotFoundError("research_package_not_found")
        if artifact.get("kind") != RESEARCH_PACKAGE_KIND:
            raise ResearchPackageConflictError("research_package_artifact_identity_conflict")

        task_id = str(artifact.get("sourceTaskId") or "")
        try:
            raw, _source_mode = load_artifact_binary(
                task_id,
                artifact,
                allow_replay_fallback=False,
            )
            loaded = load_and_validate_research_package(raw, format_hint=RESEARCH_PACKAGE_FORMAT)
        except Exception as exc:
            if isinstance(exc, (FileNotFoundError, ValueError, ResearchPackageValidationError)):
                raise ResearchPackageConflictError(
                    "research_package_content_unavailable_or_invalid"
                ) from exc
            raise

        stored_hash = str(artifact.get("contentSha256") or "").strip().lower()
        if stored_hash != loaded.content_sha256:
            raise ResearchPackageConflictError("research_package_persisted_hash_mismatch")

        identity = expected_identity or (
            expected.package_id if expected is not None else loaded.package_id,
            expected.version if expected is not None else loaded.version,
        )
        if (loaded.package_id, loaded.version) != identity:
            raise ResearchPackageConflictError("research_package_identity_content_mismatch")
        if expected is not None and expected.content_sha256 != loaded.content_sha256:
            raise ResearchPackageConflictError("research_package_version_conflict")

        return ResearchPackageAsset(
            artifact_id=str(artifact["id"]),
            package=loaded,
            source_task_id=artifact.get("sourceTaskId"),
        )


def package_artifact_id(*, owner_id: str, workspace_id: str, package_id: str, version: str) -> str:
    """Stable Artifact identity for one published package version in one workspace."""
    identity = json.dumps(
        [owner_id, workspace_id, package_id, version],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"rp_{hashlib.sha256(identity).hexdigest()}"
