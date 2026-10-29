from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.research_orchestration import ResearchTaskV12
from app.modules.legacy_final_result_backfill import (
    finalize_legacy_completed_without_authority,
    project_legacy_final_result_backfill,
    run_completed_progress_projection_repair,
    run_legacy_completed_final_result_backfill,
)


NOW = datetime(2026, 8, 19, tzinfo=timezone.utc)
FIXTURE = Path(__file__).resolve().parents[3] / "packages" / "shared" / "test-fixtures" / "research-orchestration-v1.2-valid.json"


def _legacy_payload(*, authority: str) -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))["researchTask"]
    payload["status"] = "completed"
    payload["finishedAt"] = "2026-08-18T00:00:00Z"
    payload["workUnits"][0]["status"] = "completed"
    payload["workUnits"][0]["finishedAt"] = "2026-08-18T00:00:00Z"
    payload["artifacts"][0]["artifactState"] = "usable"
    payload["artifacts"][0]["sourceCapabilityCallId"] = None
    if authority == "missing":
        payload.pop("finalResultAuthority", None)
    else:
        payload["finalResultAuthority"] = None
    return payload


class _Repository:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.writes: list[dict] = []

    def list_legacy_completed_without_final_authority(self) -> list[dict]:
        return [deepcopy(row) for row in self.rows if "finalResultAuthority" not in row["payload"] or row["payload"]["finalResultAuthority"] is None]

    def persist_legacy_final_result_backfill(self, *, task_id: str, expected_revision: int, payload: dict) -> int:
        self.writes.append({"task_id": task_id, "expected_revision": expected_revision, "payload": deepcopy(payload)})
        for row in self.rows:
            if row["task_id"] == task_id:
                assert row["revision"] == expected_revision
                row["revision"] += 1
                row["payload"] = deepcopy(payload)
                return row["revision"]
        raise AssertionError("missing test row")

    def list_completed_with_stale_progress_projection(self) -> list[dict]:
        return [
            deepcopy(row)
            for row in self.rows
            if row["status"] == "completed"
            and isinstance(row["payload"].get("finalResultAuthority"), dict)
            and (
                not isinstance(row["payload"].get("progressProjection"), dict)
                or row["payload"]["progressProjection"].get("revision") != row["revision"]
            )
        ]

    def persist_progress_projection_repair(self, *, task_id: str, expected_revision: int, payload: dict) -> int:
        self.writes.append({"task_id": task_id, "expected_revision": expected_revision, "payload": deepcopy(payload)})
        for row in self.rows:
            if row["task_id"] == task_id:
                assert row["revision"] == expected_revision
                row["revision"] += 1
                row["payload"] = deepcopy(payload)
                return row["revision"]
        raise AssertionError("missing test row")


def _completed_authority_payload(*, revision: int, projection_revision: int | None) -> dict:
    finalized = finalize_legacy_completed_without_authority(
        persisted_status="completed", payload=_legacy_payload(authority="missing"), occurred_at=NOW
    )
    projected = project_legacy_final_result_backfill(finalized, revision=revision)
    payload = projected.model_dump(mode="json", by_alias=True)
    if projection_revision is None:
        payload.pop("progressProjection", None)
    else:
        payload["progressProjection"]["revision"] = projection_revision
    return payload


@pytest.mark.parametrize("authority", ("missing", "json_null"))
def test_legacy_completed_missing_or_json_null_authority_is_backfillable(authority: str) -> None:
    finalized = finalize_legacy_completed_without_authority(
        persisted_status="completed", payload=_legacy_payload(authority=authority), occurred_at=NOW
    )
    assert finalized.status.value == "completed"
    assert finalized.final_result_authority is not None
    assert finalized.final_result_authority.final_result_id is not None


def test_legacy_backfill_uses_existing_finalizer_without_new_scientific_claims() -> None:
    source = _legacy_payload(authority="missing")
    finalized = finalize_legacy_completed_without_authority(
        persisted_status="completed", payload=source, occurred_at=NOW
    )
    result = finalized.final_results[-1]
    usable = [row["artifactId"] for row in source["artifacts"] if row["artifactState"] == "usable"]
    assert result.evidence_refs == usable
    assert result.key_findings == [
        "The persisted ScientificState records validated outputs, but required criteria "
        "or execution gaps remain."
    ]
    assert result.disposition.value == "partial"
    assert result.direct_answer == (
        "Validated research outputs are available, but required completion criteria "
        "or execution gaps remain."
    )
    assert "runtime:sub_contract_full:reviewing" in result.gaps
    assert result.uncertainty == ["No new comparison, explanation, recommendation, or priority was inferred during finalization."]


def test_legacy_backfill_invalid_capability_evidence_becomes_an_honest_gap() -> None:
    source = _legacy_payload(authority="missing")
    artifact = source["artifacts"][0]
    call = source["capabilityCalls"][0]
    artifact["sourceCapabilityCallId"] = call["capabilityCallId"]
    call.update({"status": "failed", "outputArtifactRefs": [artifact["artifactId"]]})

    finalized = finalize_legacy_completed_without_authority(
        persisted_status="completed", payload=source, occurred_at=NOW
    )
    result = finalized.final_results[-1]

    assert result.disposition.value == "evidence_gap"
    assert result.evidence_refs == []
    assert finalized.final_result_authority is not None
    assert finalized.final_result_authority.result_artifact_refs == []


def test_legacy_backfill_is_exactly_once_and_dry_run_does_not_write() -> None:
    repository = _Repository([
        {"task_id": "task_legacy_missing", "status": "completed", "revision": 4, "payload": _legacy_payload(authority="missing")},
        {"task_id": "task_legacy_null", "status": "completed", "revision": 8, "payload": _legacy_payload(authority="json_null")},
    ])
    preview = run_legacy_completed_final_result_backfill(repository=repository, dry_run=True, occurred_at=NOW)
    assert (preview.scanned, preview.eligible, preview.written) == (2, 2, 0)
    assert repository.writes == []

    applied = run_legacy_completed_final_result_backfill(repository=repository, dry_run=False, occurred_at=NOW)
    assert (applied.scanned, applied.eligible, applied.written) == (2, 2, 2)
    assert len(repository.writes) == 2
    assert all(ResearchTaskV12.model_validate(row["payload"]).status.value == "completed" for row in repository.rows)
    assert all(len(ResearchTaskV12.model_validate(row["payload"]).scientific_states) == 1 for row in repository.rows)
    assert all(row["payload"]["revision"] == row["revision"] for row in repository.rows)
    assert all(row["payload"]["progressProjection"]["revision"] == row["revision"] for row in repository.rows)

    replay = run_legacy_completed_final_result_backfill(repository=repository, dry_run=False, occurred_at=NOW)
    assert (replay.scanned, replay.eligible, replay.written) == (0, 0, 0)
    assert len(repository.writes) == 2


def test_new_completed_task_with_null_authority_remains_invalid() -> None:
    with pytest.raises(ValidationError, match="validated Final Result authority"):
        ResearchTaskV12.model_validate(_legacy_payload(authority="json_null"))


def test_completed_task_with_existing_authority_is_not_modified() -> None:
    payload = _legacy_payload(authority="missing")
    finalized = finalize_legacy_completed_without_authority(
        persisted_status="completed", payload=payload, occurred_at=NOW
    ).model_dump(mode="json", by_alias=True)
    repository = _Repository([
        {"task_id": "task_current", "status": "completed", "revision": 5, "payload": finalized},
    ])
    summary = run_legacy_completed_final_result_backfill(repository=repository, dry_run=False, occurred_at=NOW)
    assert (summary.scanned, summary.eligible, summary.written) == (0, 0, 0)
    assert repository.writes == []


def test_completed_authority_stale_projection_repair_changes_only_projection_and_revision() -> None:
    payload = _completed_authority_payload(revision=5, projection_revision=4)
    payload["progressProjection"]["taskProgress"]["status"] = "running"
    before = deepcopy(payload)
    repository = _Repository([{"task_id": "task_stale", "status": "completed", "revision": 5, "payload": payload}])
    expected_projection = project_legacy_final_result_backfill(
        ResearchTaskV12.model_validate(before), revision=6
    ).progress_projection

    preview = run_completed_progress_projection_repair(repository=repository, dry_run=True)
    assert (preview.scanned, preview.eligible, preview.written) == (1, 1, 0)
    assert repository.writes == []

    applied = run_completed_progress_projection_repair(repository=repository, dry_run=False)
    row = repository.rows[0]
    assert (applied.scanned, applied.eligible, applied.written) == (1, 1, 1)
    assert row["revision"] == row["payload"]["revision"] == row["payload"]["progressProjection"]["revision"] == 6
    assert row["payload"]["progressProjection"] == expected_projection
    for key in ("scientificStates", "completionDecisions", "finalResults", "finalResultAuthority", "researchDesignVersions"):
        assert row["payload"][key] == before[key]

    replay = run_completed_progress_projection_repair(repository=repository, dry_run=True)
    assert (replay.scanned, replay.eligible, replay.written) == (0, 0, 0)


def test_completed_authority_matching_projection_repair_is_noop() -> None:
    payload = _completed_authority_payload(revision=5, projection_revision=5)
    repository = _Repository([{"task_id": "task_current", "status": "completed", "revision": 5, "payload": payload}])
    summary = run_completed_progress_projection_repair(repository=repository, dry_run=False)
    assert (summary.scanned, summary.eligible, summary.written) == (0, 0, 0)
    assert repository.writes == []
