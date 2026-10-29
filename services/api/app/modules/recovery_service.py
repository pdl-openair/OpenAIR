from __future__ import annotations

from app.config import settings
from app.modules.recovery_repository import RecoveryError, RecoveryRepository


class RecoveryService:
    def __init__(self, repository: RecoveryRepository | None = None):
        self._repository = repository or RecoveryRepository()

    @staticmethod
    def _require_postgres() -> None:
        if settings.persistence_backend != "postgres":
            raise RecoveryError(503, "recovery_requires_postgres")

    def create(self, **_kwargs: object) -> dict:
        raise RecoveryError(410, "legacy_recovery_read_only")

    def confirm(self, **_kwargs: object) -> dict:
        raise RecoveryError(410, "legacy_recovery_read_only")

    def get(self, *, task_id: str, request_id: str, owner_id: str, workspace_id: str) -> dict:
        self._require_postgres()
        return self._repository.get(task_id=task_id, request_id=request_id, owner_id=owner_id, workspace_id=workspace_id)

    def discover(
        self,
        *,
        task_id: str,
        source_run_id: str,
        expected_task_revision: int,
        owner_id: str,
        workspace_id: str,
    ) -> dict:
        self._require_postgres()
        return self._repository.discover(
            task_id=task_id,
            source_run_id=source_run_id,
            expected_task_revision=expected_task_revision,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
