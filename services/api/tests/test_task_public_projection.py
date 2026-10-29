from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest
from app.config import settings
from app.main import app
from app.models.research_orchestration import ResearchTaskV12
from app.modules.research_orchestration_repository import _extract_contract_payload
from app.modules.research_progress_projection import project_task_progress
from app.modules.task_public_projection import project_public_task_read_only
from app.routes import tasks as tasks_route
from fastapi.testclient import TestClient

client = TestClient(app)
FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)


def _canonical_public_task() -> dict:
    canonical = json.loads(FIXTURE.read_text(encoding="utf-8"))["researchTask"]
    canonical["artifacts"][0]["validatorResult"] = {
        "status": "failed",
        "artifactContract": "evidence_summary_v1",
        "artifactSchemaDigest": "a" * 64,
        "contentSha256": "b" * 64,
        "safeErrorCode": "artifact_schema_invalid",
        "safeFieldPaths": ["/claims/0/sourceRef", "not-a-json-pointer"],
        "rawResponse": "must never enter the public Task response",
        "resultProtocol": {
            "version": "result-protocol.v1",
            "status": "degraded",
            "accepted_items": [{"path": "queries", "count": 7}],
            "quarantined_items": [{"path": "queries[7]", "reason": "invalid_query"}],
            "repaired_items": [],
            "warnings": ["one query was skipped"],
            "coverage": {"accepted_query_count": 7},
        },
        "artifactQuality": "degraded",
        "warnings": ["one query was skipped"],
    }
    return {
        "ownerId": "owner-public-projection",
        "workspaceId": "workspace-public-projection",
        "createdBy": "actor-public-projection",
        "title": "Canonical research task",
        "currentStage": "solution_design",
        "resultSource": "live",
        "createdAt": "2026-07-28T00:00:00Z",
        "updatedAt": "2026-07-28T00:00:08Z",
        "stages": [
            "problem_modeling",
            "solution_design",
            "execution_validation",
            "insight_generation",
        ],
        **canonical,
    }


class _EmptyArtifactRepository:
    def list_by_scope(self, *_args, **_kwargs) -> tuple[list[dict], int]:
        return [], 0

    def summary_visible_by_tasks(self, *_args, **_kwargs) -> dict:
        return {}


def _catalog_artifact_row(artifact_id: str) -> dict:
    return {
        "id": artifact_id,
        "taskId": "task_contract_full",
        "sourceTaskId": "task_contract_full",
        "ownerId": "owner-public-projection",
        "workspaceId": "workspace-public-projection",
        "createdBy": "actor-public-projection",
        "kind": "evidence_summary",
        "title": f"Visible catalog Artifact {artifact_id}",
        "createdAt": "2026-07-28T00:00:07Z",
        "artifactState": "usable",
        "artifactVisibility": "user",
    }


def _public_artifact_refs(task: dict) -> set[str]:
    """Collect the public reference contract, excluding scientific authority.

    Artifact.sourceRefs is mixed lineage; known Artifact IDs in that field are
    checked separately, so material/source identities are not misclassified.
    """
    refs: set[str] = set()
    reference_lists = {
        "artifactRefs", "inputArtifactRefs", "outputArtifactRefs",
        "needsReviewArtifactRefs", "linkedArtifactIds", "linkedArtifacts",
        "relatedArtifactIds", "relatedArtifactRefs",
    }
    reference_values = {"artifactId", "sourceArtifactId", "resultArtifactId"}

    def visit(value: object) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key in {"finalResultAuthority", "finalResult", "researchDesignVersions"}:
                    continue
                if key in reference_lists:
                    refs.update(ref for ref in item or [] if isinstance(ref, str))
                elif key in reference_values and isinstance(item, str):
                    refs.add(item)
                elif key == "sourceId" and value.get("sourceType") == "artifact":
                    if isinstance(item, str):
                        refs.add(item)
                else:
                    visit(item)

    visit(task)
    return refs


def test_public_task_wires_revision_bound_durable_research_history() -> None:
    persisted = _canonical_public_task()
    persisted["artifacts"][0]["sourceResultId"] = "result-accepted"
    persisted["results"] = [
        {
            "resultId": "result-accepted",
            "workUnitId": "wu_contract_full",
            "capabilityCallId": "call_contract_full",
            "storageType": "inline",
            "storageScope": "task",
            "storageRef": "result:result-accepted",
            "contentSha256": "c" * 64,
            "mimeType": "application/json",
            "checkStatus": "passed",
            "checkResult": {"status": "passed"},
            "sourceRefs": ["call_contract_full"],
            "artifactRefs": ["artifact_contract_full"],
            "scientificStateVersions": [1],
            "createdAt": "2026-07-28T00:00:07Z",
        }
    ]
    persisted["scientificStates"] = [
        {
            "scientificStateVersion": 1,
            "researchDesignVersion": 1,
            "scientificFacts": {"accepted": True},
            "executionState": {},
            "sourceRefs": ["result-accepted"],
            "createdAt": "2026-07-28T00:00:08Z",
        }
    ]
    canonical = ResearchTaskV12.model_validate(_extract_contract_payload(persisted))
    persisted["progressProjection"] = project_task_progress(
        canonical,
        revision=canonical.revision,
    ).progress_projection

    public = project_public_task_read_only(
        persisted,
        visible_artifact_ids={"artifact_contract_full"},
    ).model_dump(mode="json", by_alias=True)

    projection = public["progressProjection"]
    assert projection["revision"] == public["revision"]
    history = projection["researchProcessHistory"]
    capability = next(row for row in history if row["eventType"] == "research_tool_summary")
    assert capability["activityKey"] == hashlib.sha256(b"call_contract_full").hexdigest()[:24]
    accepted = next(
        row for row in history if row["activityKey"] == "accepted-result:result-accepted"
    )
    assert accepted["resultRefs"] == ["result-accepted"]
    assert accepted["scientificStateVersions"] == [1]
    assert accepted["artifactRefs"] == ["artifact_contract_full"]


def test_api_interruption_retains_only_repository_visible_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted = _canonical_public_task()
    visible = deepcopy(persisted["artifacts"][0])
    visible.update({
        "artifactState": "usable",
        "version": 1,
    })
    excluded_ids = {
        "hidden-artifact",
        "internal-artifact",
        "diagnostic-artifact",
        "audit-artifact",
        "developer-artifact",
    }
    excluded = []
    for artifact_id in sorted(excluded_ids):
        artifact = deepcopy(visible)
        artifact["artifactId"] = artifact_id
        excluded.append(artifact)
    persisted["artifacts"] = [visible, *excluded]
    all_canonical_ids = [row["artifactId"] for row in persisted["artifacts"]]
    persisted["workUnits"][0]["inputArtifactRefs"] = all_canonical_ids
    persisted["workUnits"][0]["inputArtifactSnapshots"] = []
    persisted["capabilityCalls"][0]["inputRefs"] = ["material_contract", *all_canonical_ids]
    persisted["capabilityCalls"][0]["outputArtifactRefs"] = all_canonical_ids
    persisted["status"] = "failed"
    persisted["stopReceipt"] = {
        "taskId": persisted["taskId"],
        "status": "stopped",
        "active": True,
        "controlAction": "stop",
        "stoppedAt": "2026-07-28T00:00:09Z",
    }
    persisted["workUnits"][0].update({
        "status": "failed",
        "terminalReasonCode": "provider_configuration",
        "finishedAt": "2026-07-28T00:00:08Z",
    })
    persisted["substageRuntimeStates"][0].update({
        "status": "blocked_recoverable",
        "statusReasonCode": "provider_configuration",
    })

    class Repository:
        def get_task_for_public_read(self, task_id: str) -> dict:
            assert task_id == persisted["taskId"]
            return persisted

    class VisibleArtifactRepository:
        def list_by_scope(self, *_args, **_kwargs) -> tuple[list[dict], int]:
            rows = [
                _catalog_artifact_row("artifact_contract_full"),
                _catalog_artifact_row("catalog-only-visible"),
            ]
            return rows, len(rows)

    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "TaskRepository", Repository)
    monkeypatch.setattr(tasks_route, "ArtifactRepository", VisibleArtifactRepository)

    response = tasks_route.get_task(
        persisted["taskId"],
        identity={
            "ownerId": persisted["ownerId"],
            "workspaceId": persisted["workspaceId"],
            "createdBy": persisted["createdBy"],
        },
    )

    body = response.model_dump(mode="json", by_alias=True)
    interruption = body["progressProjection"]["interruption"]
    assert interruption["retainedResults"]["artifacts"] == [{
        "artifactId": "artifact_contract_full",
        "artifactState": "usable",
        "version": 1,
    }]
    assert interruption["controlFact"]["kind"] == "user_stop"

    public_ids = {row["id"] for row in body["artifacts"]}
    assert public_ids == {"artifact_contract_full", "catalog-only-visible"}
    visible_refs = {"artifact_contract_full"}
    assert body["stageDetails"][1]["artifactRefs"] == ["artifact_contract_full"]
    assert body["stageDetails"][1]["subStages"][0]["artifactRefs"] == ["artifact_contract_full"]
    assert set(body["workUnits"][0]["inputArtifactRefs"]) == visible_refs
    assert set(body["workUnits"][0]["outputArtifactRefs"]) == visible_refs
    assert set(body["capabilityCalls"][0]["artifactRefs"]) == visible_refs
    assert set(body["capabilityCalls"][0]["inputArtifactRefs"]) == visible_refs
    assert set(body["capabilityCalls"][0]["outputArtifactRefs"]) == visible_refs
    assert _public_artifact_refs(body) <= public_ids

    serialized = json.dumps(body)
    for artifact_id in excluded_ids:
        assert artifact_id not in serialized


def test_taskout_artifacts_and_refs_fail_closed_when_visibility_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted = _canonical_public_task()
    persisted["status"] = "failed"
    persisted["workUnits"][0].update({
        "status": "failed",
        "terminalReasonCode": "provider_configuration",
        "finishedAt": "2026-07-28T00:00:08Z",
    })
    persisted["substageRuntimeStates"][0].update({
        "status": "blocked_recoverable",
        "statusReasonCode": "provider_configuration",
    })

    class Repository:
        def get_task_for_public_read(self, task_id: str) -> dict:
            assert task_id == persisted["taskId"]
            return persisted

    class BrokenArtifactRepository:
        def list_by_scope(self, *_args, **_kwargs) -> tuple[list[dict], int]:
            raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "TaskRepository", Repository)
    monkeypatch.setattr(tasks_route, "ArtifactRepository", BrokenArtifactRepository)

    response = tasks_route.get_task(
        persisted["taskId"],
        identity={
            "ownerId": persisted["ownerId"],
            "workspaceId": persisted["workspaceId"],
            "createdBy": persisted["createdBy"],
        },
    )
    body = response.model_dump(mode="json", by_alias=True)

    assert body["artifacts"] == []
    assert _public_artifact_refs(body) == set()
    assert body["progressProjection"]["interruption"]["retainedResults"]["artifacts"] == []
    assert "artifact_contract_full" not in json.dumps(body)


def test_json_visibility_authority_excludes_non_user_catalog_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.task_public_projection import project_public_task

    monkeypatch.setattr(settings, "persistence_backend", "json")
    authority_source = {
        "artifacts": [
            {"artifactId": "artifact_contract_full", "artifactVisibility": "user"},
            {"artifactId": "hidden", "artifactVisibility": "hidden"},
            {"artifactId": "internal", "audience": "internal"},
            {"artifactId": "diagnostic", "visibility": "diagnostic"},
            {"artifactId": "audit", "artifactVisibility": "audit"},
            {"artifactId": "developer", "artifactVisibility": "developer"},
        ]
    }

    visible_ids = tasks_route._visible_artifact_ids_for_task(authority_source)
    assert visible_ids == frozenset({"artifact_contract_full"})

    persisted = _canonical_public_task()
    visible = persisted["artifacts"][0]
    hidden = deepcopy(visible)
    hidden.update({
        "artifactId": "hidden",
        "sourceCapabilityCallId": None,
        "lineageKind": "direct_work_unit_output",
    })
    persisted["artifacts"] = [visible, hidden]
    persisted["capabilityCalls"][0]["outputArtifactRefs"] = [
        "artifact_contract_full",
        "hidden",
    ]

    body = project_public_task(
        persisted,
        visible_artifact_ids=visible_ids,
    ).model_dump(mode="json", by_alias=True)
    assert {row["id"] for row in body["artifacts"]} == {"artifact_contract_full"}
    assert _public_artifact_refs(body) == {"artifact_contract_full"}
    assert "hidden" not in json.dumps(body)


def test_get_canonical_task_projects_calls_artifacts_and_lineage_without_raw_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted = _canonical_public_task()
    before = deepcopy(persisted)

    class Repository:
        def get_task_for_public_read(self, task_id: str) -> dict:
            assert task_id == persisted["taskId"]
            return persisted

    class VisibleArtifactRepository:
        def list_by_scope(self, *_args, **_kwargs) -> tuple[list[dict], int]:
            return [{"id": "artifact_contract_full"}], 1

    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "TaskRepository", Repository)
    monkeypatch.setattr(tasks_route, "ArtifactRepository", VisibleArtifactRepository)

    response = client.get(
        f"/api/tasks/{persisted['taskId']}",
        headers={
            "X-OpenAIR-Owner-Id": persisted["ownerId"],
            "X-OpenAIR-Workspace-Id": persisted["workspaceId"],
            "X-OpenAIR-Created-By": persisted["createdBy"],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["taskId"], body["revision"], body["status"]) == (
        "task_contract_full",
        4,
        "running",
    )
    assert body["workUnits"][0]["workUnitId"] == "wu_contract_full"
    assert body["workUnits"][0]["executionDecisionId"] == "decision_contract_full"

    call = body["capabilityCalls"][0]
    assert (call["id"], call["workUnitId"], call["canonicalStatus"], call["status"]) == (
        "call_contract_full",
        "wu_contract_full",
        "succeeded",
        "completed",
    )
    assert call["outputArtifactRefs"] == ["artifact_contract_full"]

    artifact = body["artifacts"][0]
    assert (
        artifact["id"],
        artifact["workUnitId"],
        artifact["sourceCapabilityCallId"],
        artifact["lineageKind"],
    ) == (
        "artifact_contract_full",
        "wu_contract_full",
        "call_contract_full",
        "capability_call_output",
    )
    assert artifact["canonicalValidation"] == {
        "status": "failed",
        "artifactContract": "evidence_summary_v1",
        "artifactSchemaDigest": "a" * 64,
        "contentSha256": "b" * 64,
        "errorCode": "artifact_schema_invalid",
        "fieldPaths": ["/claims/0/sourceRef"],
    }
    assert artifact["resultProtocol"]["status"] == "degraded"
    assert artifact["resultProtocol"]["coverage"] == {"accepted_query_count": 7}
    assert artifact["artifactQuality"] == "degraded"
    assert artifact["warnings"] == ["one query was skipped"]
    assert "rawResponse" not in artifact
    assert "must never enter" not in response.text
    assert persisted == before


def test_get_historical_authority_uses_only_the_read_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.research_execution_decision_service import (
        reconcile_task_lifecycle_from_runtime,
    )
    from app.modules.research_orchestration_repository import _extract_contract_payload

    persisted = _canonical_public_task()
    persisted["status"] = "running"
    persisted["substageRuntimeStates"][0].update({
        "status": "completed",
        "statusReasonCode": None,
        "activeExecutionDecisionId": None,
        "updatedAt": "2026-08-20T00:00:00Z",
    })
    persisted["workUnits"][0].update({
        "status": "completed",
        "terminalReasonCode": None,
        "finishedAt": "2026-08-20T00:00:00Z",
    })
    persisted["artifacts"][0]["artifactState"] = "usable"
    persisted["artifacts"][0]["sourceCapabilityCallId"] = None
    persisted["artifacts"][0]["lineageKind"] = "direct_work_unit_output"
    completed = reconcile_task_lifecycle_from_runtime(
        ResearchTaskV12.model_validate(_extract_contract_payload(persisted)),
        occurred_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    ).model_dump(mode="json", by_alias=True)
    completed.update({
        "ownerId": persisted["ownerId"],
        "workspaceId": persisted["workspaceId"],
        "createdBy": persisted["createdBy"],
        "title": persisted["title"],
        "currentStage": persisted["currentStage"],
        "resultSource": persisted["resultSource"],
        "stages": persisted["stages"],
    })
    completed["finalResultAuthority"].pop("integrityContractVersion")
    result_artifact_refs = completed["finalResultAuthority"]["resultArtifactRefs"]
    before = deepcopy(completed)
    from app.modules.task_repository import _with_read_only_legacy_authority_projection

    read_projection = _with_read_only_legacy_authority_projection(completed)
    assert "integrityContractVersion" not in read_projection["finalResultAuthority"]
    assert completed == before

    class Repository:
        writes = 0

        def get_task_for_public_read(self, task_id: str) -> dict:
            assert task_id == completed["taskId"]
            return read_projection

        def get_task(self, _task_id: str) -> dict:
            raise AssertionError("GET must use the dedicated read adapter")

    class VisibleArtifactRepository:
        def list_by_scope(self, *_args, **_kwargs) -> tuple[list[dict], int]:
            rows = [{"id": artifact_id} for artifact_id in result_artifact_refs]
            return rows, len(rows)

    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "TaskRepository", Repository)
    monkeypatch.setattr(tasks_route, "ArtifactRepository", VisibleArtifactRepository)

    response = tasks_route.get_task(
        completed["taskId"],
        identity={
            "ownerId": completed["ownerId"],
            "workspaceId": completed["workspaceId"],
            "createdBy": completed["createdBy"],
        },
    )

    assert response.finalResultAuthority["integrityStatus"] == "legacy_unvalidated"
    assert response.finalResultAuthority["status"] == "current"
    assert response.finalResult is not None
    assert response.finalResult["finalResultId"] == response.finalResultAuthority["finalResultId"]
    assert completed == before
    assert "integrityContractVersion" not in read_projection["finalResultAuthority"]

    from app.modules.task_public_projection import project_public_task_read_only

    hidden_current_projection = project_public_task_read_only(
        completed,
        visible_artifact_ids=(),
    ).model_dump(mode="json", by_alias=True)
    assert hidden_current_projection["finalResultAuthority"] == response.finalResultAuthority
    assert hidden_current_projection["artifacts"] == []

    for artifact_state, expected_status in (
        ("failed", "stale"),
        ("needs_review", "needs_review"),
    ):
        changed = deepcopy(completed)
        for artifact in changed["artifacts"]:
            if artifact["artifactId"] in result_artifact_refs:
                artifact["artifactState"] = artifact_state
        visible_projection = project_public_task_read_only(
            changed,
            visible_artifact_ids=result_artifact_refs,
        ).model_dump(mode="json", by_alias=True)
        hidden_projection = project_public_task_read_only(
            changed,
            visible_artifact_ids=(),
        ).model_dump(mode="json", by_alias=True)
        assert visible_projection["finalResultAuthority"]["status"] == expected_status
        assert hidden_projection["finalResultAuthority"] == visible_projection["finalResultAuthority"]
        assert visible_projection["finalResult"] is None
        assert hidden_projection["artifacts"] == []
        assert {row["id"] for row in visible_projection["artifacts"]} >= set(result_artifact_refs)


def test_list_canonical_tasks_reads_postgres_and_uses_the_slim_public_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted = _canonical_public_task()

    class Repository:
        def load_all_tasks(self) -> list[dict]:
            return [persisted]

        def list_tasks_by_scope(self, owner_id: str, workspace_id: str, **_kwargs) -> list[dict]:
            return [
                row
                for row in [persisted]
                if row.get("ownerId") == owner_id
                and row.get("workspaceId") == workspace_id
            ]

        def list_tasks_by_scope_slim(self, owner_id: str, workspace_id: str, **kwargs) -> list[dict]:
            return self.list_tasks_by_scope(owner_id, workspace_id, **kwargs)

        def count_tasks_by_scope(self, owner_id: str, workspace_id: str) -> int:
            return len(self.list_tasks_by_scope(owner_id, workspace_id))

    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "TaskRepository", Repository)
    monkeypatch.setattr(tasks_route, "ArtifactRepository", _EmptyArtifactRepository)

    response = client.get(
        "/api/tasks",
        headers={
            "X-OpenAIR-Owner-Id": persisted["ownerId"],
            "X-OpenAIR-Workspace-Id": persisted["workspaceId"],
            "X-OpenAIR-Created-By": persisted["createdBy"],
        },
    )

    assert response.status_code == 200, response.text
    task = response.json()["tasks"][0]
    assert task["taskId"] == persisted["taskId"]
    assert task["capabilityCalls"] == []
    assert task["workUnits"] == []
    assert task["artifacts"] == []


def test_canonical_research_design_instance_identity_survives_public_plan_projection() -> None:
    from app.modules.task_public_projection import project_public_task

    persisted = json.loads(
        json.dumps(_canonical_public_task()).replace('"sub_contract_full"', '"instance-123"')
    )

    projected = project_public_task(persisted).model_dump(mode="json", by_alias=True)
    substages = projected["creationPlan"]["stages"][1]["substages"]

    assert substages[0]["instanceId"] == "instance-123"


def test_public_plan_projection_uses_display_labels_not_definition_keys() -> None:
    from app.modules.task_public_projection import (
        _user_facing_objective,
        _user_facing_substage_label,
        project_public_task,
    )

    persisted = _canonical_public_task()
    projected = project_public_task(persisted).model_dump(mode="json", by_alias=True)
    plan_stages = projected["creationPlan"]["stages"]
    all_substages = [row for stage in plan_stages for row in stage["substages"]]
    assert all_substages, "expected formal SubstageInstance projection"
    for row in all_substages:
        assert "planned_output" not in str(row["label"])
        assert not str(row["label"]).startswith("sdr.")
    assert all(stage.get("description") in (None, "") for stage in plan_stages)
    assert _user_facing_substage_label("reproducibility", pack="sdr") == "可复现性设计"
    assert _user_facing_substage_label("report_generation", pack="sdr") == "报告生成"
    assert (
        _user_facing_objective(
            "report_generation",
            "Assemble the completed research into the canonical final result.",
        )
        == "汇总已完成的研究结果，形成最终研究报告"
    )


def test_project_public_task_normalizes_legacy_artifact_states() -> None:
    from app.modules.task_public_projection import project_public_task

    legacy = {
        "taskId": "task_legacy_artifact_state",
        "ownerId": "owner-legacy",
        "workspaceId": "workspace-legacy",
        "createdBy": "owner-legacy",
        "title": "Legacy artifact states",
        "status": "running",
        "currentStage": "execution_validation",
        "resultSource": "live",
        "createdAt": "2026-08-01T00:00:00Z",
        "updatedAt": "2026-08-01T00:00:00Z",
        "stages": [
            "problem_modeling",
            "solution_design",
            "execution_validation",
            "insight_generation",
        ],
        "artifacts": [
            {
                "id": "art_raw",
                "taskId": "task_legacy_artifact_state",
                "kind": "research_note",
                "title": "Raw note",
                "createdAt": "2026-08-01T00:00:00Z",
                "artifactState": "raw_output",
            },
            {
                "id": "art_summary",
                "taskId": "task_legacy_artifact_state",
                "kind": "stage_summary",
                "title": "Stage summary",
                "createdAt": "2026-08-01T00:00:01Z",
                "artifactState": "stage_summary",
            },
        ],
        "finalResult": {
            "finalResultId": "artifact-derived-final",
            "directAnswer": "This ungoverned legacy field must not be public.",
        },
    }

    projected = project_public_task(
        legacy, visible_artifact_ids={"art_raw", "art_summary"}
    )
    body = projected.model_dump(mode="json", by_alias=True)
    states = {row["id"]: row["artifactState"] for row in body["artifacts"]}
    assert states["art_raw"] == "needs_review"
    assert states["art_summary"] == "partial_success"
    assert body["finalResultAuthority"] is None
    assert body["finalResult"] is None


def test_list_keeps_degraded_row_when_projection_is_corrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    corrupt = {
        "taskId": "task_corrupt_visible",
        "ownerId": "owner-corrupt",
        "workspaceId": "workspace-corrupt",
        "createdBy": "owner-corrupt",
        "title": "Corrupt but durable",
        "revision": 7,
        "status": "running",
        "currentStage": "problem_modeling",
        "stages": "not-a-list",
        "artifacts": [{"id": None}],
    }

    class Repository:
        def load_all_tasks(self) -> list[dict]:
            return [corrupt]

        def list_tasks_by_scope(self, owner_id: str, workspace_id: str, **_kwargs) -> list[dict]:
            return [
                row
                for row in [corrupt]
                if row.get("ownerId") == owner_id
                and row.get("workspaceId") == workspace_id
            ]

        def list_tasks_by_scope_slim(self, owner_id: str, workspace_id: str, **kwargs) -> list[dict]:
            return self.list_tasks_by_scope(owner_id, workspace_id, **kwargs)

        def count_tasks_by_scope(self, owner_id: str, workspace_id: str) -> int:
            return len(self.list_tasks_by_scope(owner_id, workspace_id))

    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "TaskRepository", Repository)
    monkeypatch.setattr(tasks_route, "ArtifactRepository", _EmptyArtifactRepository)

    response = client.get(
        "/api/tasks",
        headers={
            "X-OpenAIR-Owner-Id": "owner-corrupt",
            "X-OpenAIR-Workspace-Id": "workspace-corrupt",
        },
    )

    assert response.status_code == 200, response.text
    rows = response.json()["tasks"]
    assert len(rows) == 1
    assert rows[0]["taskId"] == "task_corrupt_visible"
    assert rows[0]["title"] == "Corrupt but durable"
    assert rows[0]["progressProjection"] == {
        "projectionError": "task_projection_unavailable",
        "retryable": True,
    }

def test_confirmation_plan_uses_chinese_stage_titles_and_query_specific_work() -> None:
    from app.modules.task_public_projection import (
        _confirmation_plan_action,
        _confirmation_stage_title,
    )

    assert _confirmation_stage_title("problem_modeling") == "问题建模"
    assert _confirmation_stage_title("solution_design") == "方案设计"
    assert _confirmation_stage_title("execution_validation") == "执行验证"
    assert _confirmation_stage_title("insight_generation") == "洞察生成"

    objective = (
        "基于当前目标、实验体系和有效依据形成候选方向。"
        "当前项目科研重点：围绕40% AlSc靶和Mo衬底，"
        "筛选最有信息增益的功率、氮氩比和沉积时间组合。。"
    )
    action = _confirmation_plan_action(objective)
    assert action == (
        "围绕40% AlSc靶和Mo衬底，"
        "筛选最有信息增益的功率、氮氩比和沉积时间组合。"
    )
    assert "当前项目科研重点" not in action

    free_planner_work = "按优先级先做最关键的少数组合，再根据结果决定是否展开剩余实验。"
    assert _confirmation_plan_action(free_planner_work) == free_planner_work


# Permanent owner matrix: every public projection mode obeys one supplied
# Artifact visibility authority. These IDs name test cases, not visibility policy.
_VISIBILITY_VISIBLE_ID = "artifact_contract_full"
_VISIBILITY_EXCLUDED_IDS = frozenset({
    "visibility-contract-hidden",
    "visibility-contract-internal",
    "visibility-contract-diagnostic",
    "visibility-contract-audit",
    "visibility-contract-developer",
})
_VISIBILITY_DANGLING_ID = "visibility-contract-dangling"
_VISIBILITY_CATALOG_ID = "visibility-contract-catalog-only"
_VISIBILITY_CATALOG_PEER_ID = "visibility-contract-catalog-peer"
_PUBLIC_PROJECTION_NAMES = ("project_public_task", "project_public_task_read_only")


def _visibility_contract_task(mode: str) -> dict:
    """Create real canonical/legacy inputs without obtaining legacy from projection."""
    from app.models.schemas import TaskOut
    from app.modules.task_public_projection import _CANONICAL_REQUIRED_TASK_KEYS

    artifact_ids = [_VISIBILITY_VISIBLE_ID, *sorted(_VISIBILITY_EXCLUDED_IDS)]
    refs = [*artifact_ids, _VISIBILITY_DANGLING_ID]
    timestamp = "2026-07-28T00:00:07Z"
    if mode == "canonical":
        task = _canonical_public_task()
        assert _CANONICAL_REQUIRED_TASK_KEYS.issubset(task)
        assert task.get("finalResultAuthority") is None
        template = deepcopy(task["artifacts"][0])
        task["artifacts"] = []
        for artifact_id in artifact_ids:
            row = deepcopy(template)
            row.update({
                "artifactId": artifact_id,
                "artifactState": "usable",
                "sourceRefs": ["material_contract", "external-evidence-ref", *sorted(_VISIBILITY_EXCLUDED_IDS), _VISIBILITY_DANGLING_ID],
            })
            if artifact_id != _VISIBILITY_VISIBLE_ID:
                row["sourceResultId"] = None
            task["artifacts"].append(row)
        task["workUnits"][0]["inputArtifactRefs"] = refs
        task["workUnits"][0]["inputArtifactSnapshots"] = []
        task["capabilityCalls"][0]["inputRefs"] = ["material_contract", *refs]
        # Canonical validation correctly rejects dangling output refs; use actual
        # artifacts here and put the dangling input only on legal input fields.
        task["capabilityCalls"][0]["outputArtifactRefs"] = artifact_ids
    else:
        assert mode == "legacy"
        task = {
            "taskId": "task_contract_full",
            "ownerId": "owner-public-projection",
            "workspaceId": "workspace-public-projection",
            "createdBy": "actor-public-projection",
            "title": "Historical public visibility contract",
            "status": "running",
            "currentStage": "solution_design",
            "resultSource": "live",
            "createdAt": timestamp,
            "updatedAt": timestamp,
            "stages": ["problem_modeling", "solution_design", "execution_validation", "insight_generation"],
            "artifacts": [_catalog_artifact_row(artifact_id) for artifact_id in artifact_ids],
            "stageDetails": [{
                "stageKey": "solution_design", "status": "running", "artifactRefs": refs,
                "subStages": [{
                    "subStage": "evidence_summary", "status": "running", "artifactRefs": refs,
                }],
            }],
            "workUnits": [{
                "workUnitId": "legacy-work", "executionDecisionId": "legacy-decision",
                "assignmentId": "legacy-assignment", "subStageInstanceId": "legacy-instance",
                "responsibleActorId": "actor-public-projection", "executorType": "agent",
                "executorId": "legacy-executor", "status": "running", "dispatchedAt": timestamp,
                "inputArtifactRefs": refs, "outputArtifactRefs": refs,
            }],
            "capabilityCalls": [{
                "id": "legacy-call", "ownerId": "owner-public-projection",
                "workspaceId": "workspace-public-projection", "createdBy": "actor-public-projection",
                "name": "Historical capability", "status": "completed", "requestedAt": timestamp,
                "artifactRefs": refs, "inputArtifactRefs": refs, "outputArtifactRefs": refs,
                "artifactValidatorResults": [{
                    "evaluator": "recorded-validator", "artifactId": artifact_id,
                    "artifactState": "usable", "usable": True, "nextAction": "none",
                } for artifact_id in refs],
                "userFacingNarrative": {
                    "summary": "Recorded capability outcome", "sourceType": "artifact",
                    "sourceId": sorted(_VISIBILITY_EXCLUDED_IDS)[1],
                },
            }],
            "progressProjection": {"interruption": {
                "revision": 0, "kind": "system_failure", "summary": "Recorded interruption",
                "impact": "Current work is paused", "responsibility": "system",
                "responsibilityLabel": "System", "userAction": "No action required",
                "affectedSteps": [], "completedSteps": [], "controlFact": None, "diagnostics": [],
                "retainedResults": {
                    "artifacts": [{"artifactId": artifact_id, "artifactState": "usable", "version": 1}
                                  for artifact_id in refs],
                    "resultCount": 0, "summary": "Recorded outputs", "finalResultStatus": None,
                },
            }},
        }
        task["stageDetails"][0]["userFacingNarrative"] = {
            "summary": "Recorded stage", "sourceType": "artifact",
            "sourceId": sorted(_VISIBILITY_EXCLUDED_IDS)[4],
        }
        task["artifacts"][0]["sourceRefs"] = ["material_contract", "external-evidence-ref", *sorted(_VISIBILITY_EXCLUDED_IDS), _VISIBILITY_DANGLING_ID]
        assert not _CANONICAL_REQUIRED_TASK_KEYS.issubset(task), "fixture must enter the historical branch"

    material = {
        "materialId": "material_contract", "taskId": task["taskId"], "kind": "research_input",
        "source": {"type": "artifact", "artifactId": sorted(_VISIBILITY_EXCLUDED_IDS)[0]},
        "linkedArtifactIds": refs,
        "provenance": {"createdBy": task["createdBy"], "createdAt": timestamp},
    }
    trust = {
        "id": "trust-public-visibility", "name": "Recorded source", "category": "data",
        "executionStatus": "invoked_success", "trustContribution": "evidence",
        "usedFor": "Research input", "limitation": "Recorded boundary", "nextAction": "none",
        "relatedArtifactIds": refs, "sourceLabel": "Recorded source",
    }
    task["materials"] = [material]
    task["memoryRecords"] = [{
        "memoryId": "memory-public-visibility", "taskId": task["taskId"], "kind": "artifact_summary",
        "content": "Recorded memory", "linkedArtifacts": refs,
        "createdBy": task["createdBy"], "createdAt": timestamp,
    }]
    task["trustRecords"] = [trust]
    task["userVisibleEvents"] = [{
        "id": "event-public-visibility", "taskId": task["taskId"], "sourceType": "artifact",
        "sourceId": sorted(_VISIBILITY_EXCLUDED_IDS)[2], "visibility": "visible",
        "relatedArtifactRefs": refs,
        "narrative": {
            "summary": "Recorded output", "sourceType": "artifact",
            "sourceId": sorted(_VISIBILITY_EXCLUDED_IDS)[3],
        },
    }, {
        "id": "event-non-artifact-identity", "taskId": task["taskId"],
        "sourceType": "capability_call", "sourceId": "independent-call-identity",
        "visibility": "visible", "relatedArtifactRefs": refs,
        "narrative": {"summary": "Recorded call"},
    }]
    task["catalogArtifacts"] = {
        "count": len(refs), "items": [{"artifactId": artifact_id} for artifact_id in refs],
    }
    if mode == "legacy":
        task["artifacts"][0].update({
            "materialManifest": [deepcopy(material)], "trustRecords": [deepcopy(trust)],
            "reusedFrom": {"artifactId": sorted(_VISIBILITY_EXCLUDED_IDS)[4], "taskId": "source-task"},
            "userFacingNarrative": {
                "summary": "Recorded artifact", "sourceType": "artifact",
                "sourceId": sorted(_VISIBILITY_EXCLUDED_IDS)[0],
            },
        })
        # Validate the exact public schema up front; rejected or ignored made-up
        # fields must not give a visibility test a false sense of coverage.
        TaskOut.model_validate(task)
    return task


def _assert_public_visibility_contract(
    body: dict, *, authority_ids: set[str], expected_ids: set[str], forbidden_ids: set[str],
) -> None:
    public_ids = {row["id"] for row in body["artifacts"]}
    assert public_ids == expected_ids
    assert len(body["artifacts"]) == len(public_ids), "catalog merge must not duplicate an Artifact"
    assert _public_artifact_refs(body) <= public_ids <= authority_ids
    serialized = json.dumps(body, ensure_ascii=False, sort_keys=True)
    for artifact_id in forbidden_ids:
        assert artifact_id not in serialized, f"Artifact ID leaked in serialized TaskOut: {artifact_id}"
    if body.get("catalogArtifacts") is not None:
        summary = body["catalogArtifacts"]
        assert summary["count"] == len(summary["items"])
    assert next(event for event in body["userVisibleEvents"]
                if event["id"] == "event-non-artifact-identity")["sourceId"] == "independent-call-identity"


@pytest.mark.parametrize("mode", ["canonical", "legacy"])
@pytest.mark.parametrize("projection_name", _PUBLIC_PROJECTION_NAMES)
@pytest.mark.parametrize("authority_mode", ["visible", "empty", "omitted"])
def test_public_artifact_visibility_contract_matrix(
    mode: str, projection_name: str, authority_mode: str,
) -> None:
    from app.modules import task_public_projection

    source = _visibility_contract_task(mode)
    before = deepcopy(source)
    supplied = {_VISIBILITY_VISIBLE_ID} if authority_mode == "visible" else set()
    kwargs = {} if authority_mode == "omitted" else {"visible_artifact_ids": frozenset(supplied)}
    body = getattr(task_public_projection, projection_name)(source, **kwargs).model_dump(mode="json", by_alias=True)
    _assert_public_visibility_contract(
        body, authority_ids=supplied, expected_ids=supplied,
        forbidden_ids=set(_VISIBILITY_EXCLUDED_IDS) | {_VISIBILITY_DANGLING_ID}
        | ({_VISIBILITY_VISIBLE_ID} if not supplied else set()),
    )
    if supplied:
        assert _public_artifact_refs(body) == supplied, "visible references must survive"
        assert body["artifacts"][0]["sourceRefs"] == ["material_contract", "external-evidence-ref"]
    else:
        assert _public_artifact_refs(body) == set()
        interruption = (body.get("progressProjection") or {}).get("interruption")
        if interruption is not None:
            assert interruption["retainedResults"]["artifacts"] == []
    assert source == before, "public filtering must not mutate persistence or scientific truth"


@pytest.mark.parametrize("mode", ["canonical", "legacy"])
@pytest.mark.parametrize("projection_name", _PUBLIC_PROJECTION_NAMES)
def test_authority_membership_alone_cannot_create_dangling_public_refs(
    mode: str, projection_name: str,
) -> None:
    from app.modules import task_public_projection

    authority_ids = {_VISIBILITY_VISIBLE_ID, _VISIBILITY_DANGLING_ID}
    body = getattr(task_public_projection, projection_name)(
        _visibility_contract_task(mode), visible_artifact_ids=authority_ids,
    ).model_dump(mode="json", by_alias=True)
    _assert_public_visibility_contract(
        body, authority_ids=authority_ids, expected_ids={_VISIBILITY_VISIBLE_ID},
        forbidden_ids=set(_VISIBILITY_EXCLUDED_IDS) | {_VISIBILITY_DANGLING_ID},
    )


def _install_public_visibility_repository(
    monkeypatch: pytest.MonkeyPatch, source: dict, *, rows: list[dict], unavailable: bool = False,
) -> None:
    class Repository:
        def get_task_for_public_read(self, task_id: str) -> dict:
            assert task_id == source["taskId"]
            return source

    class ArtifactRepository:
        def list_by_scope(self, owner_id: str, workspace_id: str, **kwargs) -> tuple[list[dict], int]:
            assert (owner_id, workspace_id, kwargs["source_task_id"]) == (
                source["ownerId"], source["workspaceId"], source["taskId"],
            )
            if unavailable:
                raise RuntimeError("visibility authority unavailable")
            return deepcopy(rows), len(rows)

    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "TaskRepository", Repository)
    monkeypatch.setattr(tasks_route, "ArtifactRepository", ArtifactRepository)


@pytest.mark.parametrize("mode", ["canonical", "legacy"])
@pytest.mark.parametrize("projection_name", [*_PUBLIC_PROJECTION_NAMES, "get_task"])
@pytest.mark.parametrize("authority_mode", ["visible", "empty", "unavailable"])
def test_formal_task_callers_share_the_repository_visibility_authority(
    monkeypatch: pytest.MonkeyPatch, mode: str, projection_name: str, authority_mode: str,
) -> None:
    source = _visibility_contract_task(mode)
    before = deepcopy(source)
    visible = {_VISIBILITY_VISIBLE_ID} if authority_mode == "visible" else set()
    _install_public_visibility_repository(
        monkeypatch, source, rows=[_catalog_artifact_row(artifact_id) for artifact_id in visible],
        unavailable=authority_mode == "unavailable",
    )
    if projection_name == "get_task":
        response = tasks_route.get_task(source["taskId"], identity={
            "ownerId": source["ownerId"], "workspaceId": source["workspaceId"],
            "createdBy": source["createdBy"],
        })
    else:
        response = getattr(tasks_route, projection_name)(source)
    _assert_public_visibility_contract(
        response.model_dump(mode="json", by_alias=True), authority_ids=visible, expected_ids=visible,
        forbidden_ids=set(_VISIBILITY_EXCLUDED_IDS) | {_VISIBILITY_DANGLING_ID}
        | ({_VISIBILITY_VISIBLE_ID} if not visible else set()),
    )
    assert source == before


@pytest.mark.parametrize("mode", ["canonical", "legacy"])
@pytest.mark.parametrize("source_has_artifacts", [False, True])
def test_catalog_only_visible_artifacts_survive_source_filter_and_duplicate_merge(
    monkeypatch: pytest.MonkeyPatch, mode: str, source_has_artifacts: bool,
) -> None:
    source = _visibility_contract_task(mode)
    if not source_has_artifacts:
        source["artifacts"] = []
        if mode == "canonical":
            source["results"] = []
            source["agentInteractionRecords"] = []
            source["workUnits"][0]["inputArtifactRefs"] = []
            source["workUnits"][0]["inputArtifactSnapshots"] = []
            source["capabilityCalls"][0]["outputArtifactRefs"] = []
            for runtime in source["substageRuntimeStates"]:
                runtime["artifactRefs"] = []
    before = deepcopy(source)
    expected_ids = {_VISIBILITY_CATALOG_ID, _VISIBILITY_CATALOG_PEER_ID}
    if source_has_artifacts:
        expected_ids.add(_VISIBILITY_VISIBLE_ID)
    rows = [_catalog_artifact_row(artifact_id) for artifact_id in sorted(expected_ids)]
    by_id = {row["id"]: row for row in rows}
    excluded = sorted(_VISIBILITY_EXCLUDED_IDS)
    by_id[_VISIBILITY_CATALOG_ID].update({
        "reusedFrom": {"artifactId": excluded[0], "taskId": "source-task"},
        "validatorResult": {
            "evaluator": "recorded-validator", "artifactId": excluded[1],
            "artifactState": "usable", "usable": True, "nextAction": "none",
        },
        "userFacingNarrative": {
            "summary": "Recorded catalog output", "sourceType": "artifact", "sourceId": excluded[2],
        },
    })
    by_id[_VISIBILITY_CATALOG_PEER_ID].update({
        "reusedFrom": {"artifactId": _VISIBILITY_CATALOG_ID, "taskId": source["taskId"]},
        "validatorResult": {
            "evaluator": "recorded-validator", "artifactId": _VISIBILITY_CATALOG_PEER_ID,
            "artifactState": "usable", "usable": True, "nextAction": "none",
        },
        "userFacingNarrative": {
            "summary": "Related catalog output", "sourceType": "artifact", "sourceId": _VISIBILITY_CATALOG_ID,
        },
    })
    rows.append(deepcopy(rows[0]))  # Duplicate catalog row must remain one public object.
    _install_public_visibility_repository(monkeypatch, source, rows=rows)
    body = tasks_route.get_task(source["taskId"], identity={
        "ownerId": source["ownerId"], "workspaceId": source["workspaceId"],
        "createdBy": source["createdBy"],
    }).model_dump(mode="json", by_alias=True)
    _assert_public_visibility_contract(
        body, authority_ids=expected_ids, expected_ids=expected_ids,
        forbidden_ids=set(_VISIBILITY_EXCLUDED_IDS) | {_VISIBILITY_DANGLING_ID}
        | ({_VISIBILITY_VISIBLE_ID} if not source_has_artifacts else set()),
    )
    catalog_only = next(row for row in body["artifacts"] if row["id"] == _VISIBILITY_CATALOG_ID)
    assert catalog_only["title"] == _catalog_artifact_row(_VISIBILITY_CATALOG_ID)["title"]
    assert catalog_only["reusedFrom"] is None
    assert catalog_only["validatorResult"]["artifactId"] is None
    assert catalog_only["userFacingNarrative"]["sourceId"] is None
    peer = next(row for row in body["artifacts"] if row["id"] == _VISIBILITY_CATALOG_PEER_ID)
    assert peer["reusedFrom"]["artifactId"] == _VISIBILITY_CATALOG_ID
    assert peer["validatorResult"]["artifactId"] == _VISIBILITY_CATALOG_PEER_ID
    assert peer["userFacingNarrative"]["sourceId"] == _VISIBILITY_CATALOG_ID
    if source_has_artifacts:
        existing = next(row for row in body["artifacts"] if row["id"] == _VISIBILITY_VISIBLE_ID)
        assert existing["kind"] == ("execution_output" if mode == "canonical" else "evidence_summary")
    assert source == before



def _assert_public_list_visibility_contract(body: dict, expected_ids: set[str]) -> None:
    """List summaries are the explicit catalog-only exception to detail closure."""
    assert body["artifacts"] == []
    summary = body["catalogArtifacts"]
    assert summary["count"] == len(expected_ids)
    assert {row["artifactId"] for row in summary["items"]} == expected_ids
    without_catalog_summary = {**body, "catalogArtifacts": None}
    assert _public_artifact_refs(without_catalog_summary) == set()
    forbidden = set(_VISIBILITY_EXCLUDED_IDS) | {_VISIBILITY_DANGLING_ID}
    if _VISIBILITY_VISIBLE_ID not in expected_ids:
        forbidden.add(_VISIBILITY_VISIBLE_ID)
    serialized = json.dumps(body, ensure_ascii=False, sort_keys=True)
    for artifact_id in forbidden:
        assert artifact_id not in serialized


@pytest.mark.parametrize("mode", ["canonical", "legacy"])
def test_slim_public_task_projection_closes_retained_artifact_refs(mode: str) -> None:
    from app.modules.task_public_projection import project_public_task_list_item

    source = _visibility_contract_task(mode)
    source["progressProjection"] = deepcopy(_visibility_contract_task("legacy")["progressProjection"])
    before = deepcopy(source)
    body = project_public_task_list_item(source).model_dump(mode="json", by_alias=True)
    assert body["artifacts"] == []
    assert _public_artifact_refs(body) == set()
    assert body["progressProjection"]["interruption"]["retainedResults"]["artifacts"] == []
    for artifact_id in {*_VISIBILITY_EXCLUDED_IDS, _VISIBILITY_VISIBLE_ID, _VISIBILITY_DANGLING_ID}:
        assert artifact_id not in json.dumps(body)
    assert source == before


@pytest.mark.parametrize("mode", ["canonical", "legacy"])
@pytest.mark.parametrize("authority_mode", ["visible", "empty", "unavailable"])
def test_list_missing_postgres_summary_cannot_reopen_source_visibility(
    monkeypatch: pytest.MonkeyPatch, mode: str, authority_mode: str,
) -> None:
    source = _visibility_contract_task(mode)
    before = deepcopy(source)
    visible = {_VISIBILITY_VISIBLE_ID} if authority_mode == "visible" else set()

    class Repository:
        def count_tasks_by_scope(self, *_args, **_kwargs) -> int:
            return 1

        def list_tasks_by_scope_slim(self, *_args, **_kwargs) -> list[dict]:
            return [source]

    class ArtifactRepository:
        def summary_visible_by_tasks(self, *_args, **_kwargs) -> dict:
            return {}  # Missing summary must not turn into unfiltered source rows.

        def list_by_scope(self, *_args, **_kwargs) -> tuple[list[dict], int]:
            if authority_mode == "unavailable":
                raise RuntimeError("catalog authority unavailable")
            rows = [_catalog_artifact_row(artifact_id) for artifact_id in visible]
            return rows, len(rows)

    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "TaskRepository", Repository)
    monkeypatch.setattr(tasks_route, "ArtifactRepository", ArtifactRepository)
    response = tasks_route.list_tasks(identity={
        "ownerId": source["ownerId"], "workspaceId": source["workspaceId"], "createdBy": source["createdBy"],
    })
    assert len(response.tasks) == 1
    _assert_public_list_visibility_contract(response.tasks[0].model_dump(mode="json", by_alias=True), visible)
    assert source == before


@pytest.mark.parametrize("has_visible", [True, False])
def test_json_legacy_task_list_uses_the_real_catalog_visibility_policy(
    monkeypatch: pytest.MonkeyPatch, has_visible: bool,
) -> None:
    source = _visibility_contract_task("legacy")
    for artifact in source["artifacts"]:
        artifact["artifactVisibility"] = (
            "user" if artifact["id"] == _VISIBILITY_VISIBLE_ID and has_visible else
            "hidden" if artifact["id"] == _VISIBILITY_VISIBLE_ID else artifact["id"].rsplit("-", 1)[-1]
        )
    before = deepcopy(source)
    monkeypatch.setattr(settings, "persistence_backend", "json")
    monkeypatch.setattr(tasks_route.store, "list_tasks", lambda: [source])
    expected = {_VISIBILITY_VISIBLE_ID} if has_visible else set()
    assert tasks_route._visible_artifact_ids_for_task(source) == frozenset(expected)
    response = tasks_route.list_tasks(identity={
        "ownerId": source["ownerId"], "workspaceId": source["workspaceId"], "createdBy": source["createdBy"],
    })
    assert len(response.tasks) == 1
    _assert_public_list_visibility_contract(response.tasks[0].model_dump(mode="json", by_alias=True), expected)
    assert source == before


@pytest.mark.parametrize("has_visible", [True, False])
def test_json_canonical_list_consumes_the_formal_membership_boundary(
    monkeypatch: pytest.MonkeyPatch, has_visible: bool,
) -> None:
    # ArtifactV12 forbids extra visibility fields. Inject the already-resolved
    # authority at its route boundary instead of inventing canonical policy data.
    source = _visibility_contract_task("canonical")
    before = deepcopy(source)
    expected = {_VISIBILITY_VISIBLE_ID} if has_visible else set()
    authority_reads: list[str] = []

    def authority(task: dict) -> frozenset[str]:
        authority_reads.append(task["taskId"])
        return frozenset(expected)

    monkeypatch.setattr(settings, "persistence_backend", "json")
    monkeypatch.setattr(tasks_route.store, "list_tasks", lambda: [source])
    monkeypatch.setattr(tasks_route, "_visible_artifact_ids_for_task", authority)
    response = tasks_route.list_tasks(identity={
        "ownerId": source["ownerId"], "workspaceId": source["workspaceId"], "createdBy": source["createdBy"],
    })
    assert authority_reads == [source["taskId"]]
    assert len(response.tasks) == 1
    _assert_public_list_visibility_contract(response.tasks[0].model_dump(mode="json", by_alias=True), expected)
    assert source == before


@pytest.mark.parametrize("mode", ["canonical", "legacy"])
def test_list_catalog_summary_preserves_catalog_only_visible_artifacts(
    monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    source = _visibility_contract_task(mode)
    before = deepcopy(source)

    class Repository:
        def count_tasks_by_scope(self, *_args, **_kwargs) -> int:
            return 1

        def list_tasks_by_scope_slim(self, *_args, **_kwargs) -> list[dict]:
            return [source]

    class ArtifactRepository:
        def summary_visible_by_tasks(self, *_args, **_kwargs) -> dict:
            return {source["taskId"]: {"count": 1, "items": [{
                "artifactId": _VISIBILITY_CATALOG_ID, "title": "Visible catalog-only result",
            }]}}

        def list_by_scope(self, *_args, **_kwargs) -> tuple[list[dict], int]:
            raise AssertionError("a formal visible summary must keep its existing catalog authority")

    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(tasks_route, "TaskRepository", Repository)
    monkeypatch.setattr(tasks_route, "ArtifactRepository", ArtifactRepository)
    response = tasks_route.list_tasks(identity={
        "ownerId": source["ownerId"], "workspaceId": source["workspaceId"], "createdBy": source["createdBy"],
    })
    _assert_public_list_visibility_contract(
        response.tasks[0].model_dump(mode="json", by_alias=True), {_VISIBILITY_CATALOG_ID},
    )
    assert source == before


@pytest.mark.parametrize("mode", ["canonical", "legacy"])
def test_mixed_lineage_filters_typed_dangling_artifacts_and_preserves_external_sources(mode: str) -> None:
    from app.modules.task_public_projection import project_public_task

    source = _visibility_contract_task(mode)
    before = deepcopy(source)
    assert _VISIBILITY_DANGLING_ID in source["workUnits"][0]["inputArtifactRefs"]
    assert all((row.get("artifactId") or row.get("id")) != _VISIBILITY_DANGLING_ID
               for row in source["artifacts"])
    body = project_public_task(
        source, visible_artifact_ids={_VISIBILITY_VISIBLE_ID},
    ).model_dump(mode="json", by_alias=True)
    assert body["artifacts"][0]["sourceRefs"] == ["material_contract", "external-evidence-ref"]
    assert _VISIBILITY_DANGLING_ID not in json.dumps(body)
    assert source == before


@pytest.mark.parametrize("identity_location", ["hidden_reused_from", "retained_results", "catalog_summary"])
def test_legacy_lineage_uses_hidden_and_pending_artifact_identity_before_filtering(
    identity_location: str,
) -> None:
    from app.modules.task_public_projection import project_public_task_read_only

    source = _visibility_contract_task("legacy")
    missing_id = "missing-upstream-artifact"
    source["artifacts"][0]["sourceRefs"].append(missing_id)
    if identity_location == "hidden_reused_from":
        hidden = next(row for row in source["artifacts"] if row["id"] in _VISIBILITY_EXCLUDED_IDS)
        hidden["reusedFrom"] = {"artifactId": missing_id, "taskId": "upstream-task"}
    elif identity_location == "retained_results":
        source["progressProjection"]["interruption"]["retainedResults"]["artifacts"].append({
            "artifactId": missing_id, "artifactState": "usable", "version": 1,
        })
    else:
        assert identity_location == "catalog_summary"
        source["catalogArtifacts"]["items"].append({"artifactId": missing_id})
        source["catalogArtifacts"]["count"] += 1
    before = deepcopy(source)
    body = project_public_task_read_only(
        source, visible_artifact_ids={_VISIBILITY_VISIBLE_ID},
    ).model_dump(mode="json", by_alias=True)
    _assert_public_visibility_contract(
        body, authority_ids={_VISIBILITY_VISIBLE_ID}, expected_ids={_VISIBILITY_VISIBLE_ID},
        forbidden_ids=set(_VISIBILITY_EXCLUDED_IDS) | {_VISIBILITY_DANGLING_ID, missing_id},
    )
    assert body["artifacts"][0]["sourceRefs"] == ["material_contract", "external-evidence-ref"]
    assert source == before
