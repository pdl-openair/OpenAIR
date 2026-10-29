"""Explicit backfill for pre-authority completed ResearchTask aggregates.

This module is deliberately not part of the normal read or runtime write path.
It accepts only raw persisted rows whose task status is completed and whose
``finalResultAuthority`` is absent or JSON null, then uses the existing
fact-only finalizer to make the aggregate satisfy the current strict contract.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import ValidationError

from app.models.research_orchestration import ResearchDesignStatus, ResearchTaskV12
from app.modules.research_finalization import finalize_from_validated_facts
from app.modules.research_progress_projection import project_task_progress
from app.modules.research_orchestration_repository import _extract_contract_payload


class LegacyFinalResultBackfillError(ValueError):
    """A selected row is not a narrowly supported legacy-unfinalized task."""


class LegacyFinalResultBackfillRepository(Protocol):
    def list_legacy_completed_without_final_authority(self) -> list[dict[str, Any]]: ...

    def persist_legacy_final_result_backfill(
        self, *, task_id: str, expected_revision: int, payload: dict[str, Any]
    ) -> int: ...

    def list_completed_with_stale_progress_projection(self) -> list[dict[str, Any]]: ...

    def persist_progress_projection_repair(
        self, *, task_id: str, expected_revision: int, payload: dict[str, Any]
    ) -> int: ...


@dataclass(frozen=True)
class LegacyFinalResultBackfillSummary:
    scanned: int
    eligible: int
    written: int
    task_ids: tuple[str, ...]


@dataclass(frozen=True)
class ProgressProjectionRepairSummary:
    scanned: int
    eligible: int
    written: int
    task_ids: tuple[str, ...]


def _missing_final_result_authority(payload: dict[str, Any]) -> bool:
    return "finalResultAuthority" not in payload or payload["finalResultAuthority"] is None


def finalize_legacy_completed_without_authority(
    *,
    persisted_status: str,
    payload: dict[str, Any],
    occurred_at: datetime,
) -> ResearchTaskV12:
    """Adapt one pre-invariant persisted row to the existing finalizer.

    The temporary running state exists only while loading the historical
    contract in memory.  The returned aggregate restores the durable completed
    state and is strictly validated before a caller can persist it.
    """
    if persisted_status != "completed" or payload.get("status") != "completed":
        raise LegacyFinalResultBackfillError("legacy backfill requires persisted completed status")
    if not _missing_final_result_authority(payload):
        raise LegacyFinalResultBackfillError("legacy backfill requires missing or JSON-null authority")

    historical_finished_at = payload.get("finishedAt")
    contract = _extract_contract_payload(deepcopy(payload))
    contract.update({"status": "running", "finishedAt": None, "finalResultAuthority": None})
    historical_task = ResearchTaskV12.model_validate(contract)
    if historical_task.active_research_design_ref is None:
        raise LegacyFinalResultBackfillError("legacy backfill requires an active ResearchDesign")
    design = next(
        (
            row
            for row in historical_task.research_design_versions
            if row.version == historical_task.active_research_design_ref.version
        ),
        None,
    )
    if design is None or design.status is not ResearchDesignStatus.CONFIRMED:
        raise LegacyFinalResultBackfillError("legacy backfill requires an active confirmed ResearchDesign")

    finalized = finalize_from_validated_facts(historical_task, design, occurred_at=occurred_at)
    completed = finalized.model_dump(mode="json", by_alias=True)
    completed.update({"status": "completed", "finishedAt": historical_finished_at})
    return ResearchTaskV12.model_validate(completed)


def project_legacy_final_result_backfill(
    finalized_task: ResearchTaskV12, *, revision: int
) -> ResearchTaskV12:
    """Create the durable progress projection for the same CAS revision."""
    return project_task_progress(finalized_task.model_copy(update={"revision": revision}), revision=revision)


def run_legacy_completed_final_result_backfill(
    *,
    repository: LegacyFinalResultBackfillRepository,
    dry_run: bool,
    occurred_at: datetime | None = None,
) -> LegacyFinalResultBackfillSummary:
    """Scan and explicitly backfill eligible rows; dry-run never writes."""
    timestamp = occurred_at or datetime.now(timezone.utc)
    rows = repository.list_legacy_completed_without_final_authority()
    task_ids: list[str] = []
    written = 0
    for row in rows:
        task_id = str(row["task_id"])
        finalized = finalize_legacy_completed_without_authority(
            persisted_status=str(row["status"]), payload=row["payload"], occurred_at=timestamp
        )
        task_ids.append(task_id)
        if dry_run:
            continue
        expected_revision = int(row["revision"])
        projected = project_legacy_final_result_backfill(
            finalized, revision=expected_revision + 1
        )
        payload = deepcopy(row["payload"])
        payload.update(projected.model_dump(mode="json", by_alias=True))
        repository.persist_legacy_final_result_backfill(
            task_id=task_id, expected_revision=expected_revision, payload=payload
        )
        written += 1
    return LegacyFinalResultBackfillSummary(
        scanned=len(rows), eligible=len(task_ids), written=written, task_ids=tuple(task_ids)
    )


def run_completed_progress_projection_repair(
    *,
    repository: LegacyFinalResultBackfillRepository,
    dry_run: bool,
) -> ProgressProjectionRepairSummary:
    """Repair only stale completed-task projections with an existing authority.

    The projection is regenerated from validated durable facts.  No finalization
    or scientific records are created, and the authority/design payload is kept
    byte-for-byte from the persisted row.
    """
    rows = repository.list_completed_with_stale_progress_projection()
    task_ids: list[str] = []
    written = 0
    for row in rows:
        expected_revision = int(row["revision"])
        contract = _extract_contract_payload(deepcopy(row["payload"]))
        try:
            task = ResearchTaskV12.model_validate(contract)
        except ValidationError:
            # The SQL predicate is intentionally only structural; an invalid
            # authority aggregate is outside this repair's narrow authority.
            continue
        if task.status.value != "completed" or task.final_result_authority is None:
            continue
        if task.revision != expected_revision:
            continue

        next_revision = expected_revision + 1
        projected = project_task_progress(task, revision=next_revision)
        projection = projected.progress_projection
        if projection is None:
            raise LegacyFinalResultBackfillError("progress projection repair produced no projection")
        task_ids.append(str(row["task_id"]))
        if dry_run:
            continue

        payload = deepcopy(row["payload"])
        payload["revision"] = next_revision
        payload["progressProjection"] = projection
        repository.persist_progress_projection_repair(
            task_id=str(row["task_id"]), expected_revision=expected_revision, payload=payload
        )
        written += 1
    return ProgressProjectionRepairSummary(
        scanned=len(rows), eligible=len(task_ids), written=written, task_ids=tuple(task_ids)
    )
