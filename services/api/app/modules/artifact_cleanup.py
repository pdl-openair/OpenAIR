"""Durable cleanup consumer for orphaned Artifact storage side effects."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from app.modules.artifact_repository import ArtifactRepository
from app.modules.artifact_write import artifact_write_service


@dataclass(frozen=True)
class ArtifactCleanupSweepResult:
    claimed: int
    completed: int
    retried: int
    failed: int


class ArtifactCleanupService:
    def __init__(self, repository: ArtifactRepository | None = None) -> None:
        self._repository = repository or ArtifactRepository()

    def reconcile_pending(
        self,
        *,
        batch_size: int = 8,
        lease_seconds: int = 60,
        max_attempts: int = 8,
    ) -> ArtifactCleanupSweepResult:
        lease_owner = f"artifact-cleanup:{uuid4().hex}"
        rows = self._repository.claim_storage_cleanup(
            lease_owner=lease_owner,
            lease_seconds=lease_seconds,
            batch_size=batch_size,
        )
        def _delete_claimed(task_id: str, storage_type: str, *identity: str) -> None:
            # Production ArtifactRepository always supplies (scope, ref).  Accept the
            # legacy three-argument callback shape only for older repository doubles;
            # the production repository itself never invokes it for external rows
            # whose durable bucket scope is missing.
            if len(identity) == 1:
                artifact_write_service.delete_durable_bytes(task_id, storage_type, identity[0])
                return
            if len(identity) != 2:
                raise TypeError("artifact_cleanup_identity_invalid")
            storage_scope, storage_ref = identity
            artifact_write_service.delete_durable_bytes(
                task_id, storage_type, storage_ref, storage_scope=storage_scope
            )

        completed = retried = failed = 0
        for row in rows:
            cleanup_id = str(row["cleanup_id"])
            try:
                self._repository.execute_storage_cleanup_claim(
                    cleanup_id=cleanup_id,
                    lease_owner=lease_owner,
                    delete_callback=_delete_claimed,
                )
                completed += 1
            except Exception as exc:  # noqa: BLE001
                state = self._repository.fail_storage_cleanup_claim(
                    cleanup_id=cleanup_id,
                    lease_owner=lease_owner,
                    error=f"{type(exc).__name__}: {exc}",
                    max_attempts=max_attempts,
                )
                if state == "failed":
                    failed += 1
                else:
                    retried += 1
        return ArtifactCleanupSweepResult(
            claimed=len(rows),
            completed=completed,
            retried=retried,
            failed=failed,
        )
