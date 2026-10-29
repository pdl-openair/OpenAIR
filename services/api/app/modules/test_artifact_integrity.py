import copy

import pytest
from fastapi import HTTPException

from app.auth import RequestIdentity
from app.modules.artifact_catalog import list_catalog_artifacts
from app.modules.artifact_repository import _row_to_dict
from app.modules.task_store import store
from app.routes.tasks import _has_reviewable_execution_output, advance_substage
from app.execution import agent_service


def _identity() -> RequestIdentity:
    return {
        "ownerId": "artifact-integrity-owner",
        "workspaceId": "artifact-integrity-workspace",
        "createdBy": "pytest",
    }


def _make_task() -> dict:
    identity = _identity()
    return store.create_task(
        {
            **identity,
            "title": "Artifact integrity regression",
            "goalSummary": "Verify full artifact persistence across stages.",
            "currentStage": "problem_modeling",
            "status": "running",
            "stages": ["problem_modeling", "solution_design"],
            "creationPlan": {
                "goalSummary": "Verify full artifact persistence across stages.",
                "domain": "research_task",
                "skillPack": "sdr",
                "readyToConfirm": True,
                "clarifications": [],
                "stages": [
                    {
                        "stageKey": "problem_modeling",
                        "substages": [
                            {"substage": "scope_definition"},
                            {"substage": "evidence_scan"},
                        ],
                    },
                    {
                        "stageKey": "solution_design",
                        "substages": [
                            {"substage": "method_plan"},
                        ],
                    },
                ],
            },
        }
    )


def _make_two_stage_task(*, execution_mode: str | None = None) -> dict:
    identity = _identity()
    payload = {
        **identity,
        "title": "Execution mode regression",
        "goalSummary": "Verify stage confirmation gates.",
        "currentStage": "problem_modeling",
        "status": "running",
        "stages": ["problem_modeling", "solution_design"],
        "creationPlan": {
            "goalSummary": "Verify stage confirmation gates.",
            "domain": "research_task",
            "skillPack": "sdr",
            "readyToConfirm": True,
            "clarifications": [],
            "stages": [
                {
                    "stageKey": "problem_modeling",
                    "substages": [{"substage": "scope_definition"}],
                },
                {
                    "stageKey": "solution_design",
                    "substages": [{"substage": "method_plan"}],
                },
            ],
        },
    }
    if execution_mode is not None:
        payload["executionMode"] = execution_mode
    return store.create_task(payload)


def test_stage_confirmation_mode_gates_cross_stage_advance() -> None:
    task = _make_two_stage_task()
    task_id = task["taskId"]
    try:
        result = store.advance_substage(task_id, "scope_definition")
        assert result == {
            "completed": False,
            "hasNext": True,
            "requiresConfirmation": True,
            "stageKey": "problem_modeling",
            "nextStageKey": "solution_design",
            "substageKey": "scope_definition",
            "nextSubstageKey": "method_plan",
            "nextSubstageLabel": "method_plan",
        }

        gated = store.get_task(task_id)
        assert gated["status"] == "awaiting_confirmation"
        assert gated["currentStage"] == "problem_modeling"

        resumed = store.advance_substage(task_id, "scope_definition", respect_stage_gate=False)
        assert "requiresConfirmation" not in resumed
        assert resumed["stageKey"] == "solution_design"
        assert resumed["substageKey"] == "method_plan"
        running = store.get_task(task_id)
        assert running["status"] == "running"
        assert running["currentStage"] == "solution_design"
    finally:
        store.delete_task(task_id)


def test_automatic_research_mode_does_not_gate_cross_stage_advance() -> None:
    task = _make_two_stage_task(execution_mode="automatic_research")
    task_id = task["taskId"]
    try:
        result = store.advance_substage(task_id, "scope_definition")
        assert result["hasNext"] is True
        assert result["stageKey"] == "solution_design"
        assert result["substageKey"] == "method_plan"
        assert "requiresConfirmation" not in result
        running = store.get_task(task_id)
        assert running["status"] == "running"
        assert running["currentStage"] == "solution_design"
    finally:
        store.delete_task(task_id)


def test_api_advance_requires_completed_execution_output() -> None:
    task = _make_two_stage_task()
    task_id = task["taskId"]
    try:
        with pytest.raises(HTTPException, match="no completed real capability call"):
            advance_substage(task_id, "scope_definition", _identity())

        current = store.get_task(task_id)
        assert current is not None
        substage = current["stageDetails"][0]["subStages"][0]
        call = store.add_capability_call(
            task_id,
            skill_id="scope_definition",
            status="completed",
            runtime="live",
            stage_key="problem_modeling",
            substage_instance_id=substage["subStageInstanceId"],
        )
        assert call is not None
        assert _has_reviewable_execution_output(store.get_task(task_id), "scope_definition") is False

        artifact = store.add_artifact(
            task_id,
            kind="stage_summary",
            title="Reviewable scope output",
            summary="A real substage output.",
            format="markdown",
            related_stage="problem_modeling",
            related_skill_id="scope_definition",
            substage_instance_id=substage["subStageInstanceId"],
            source_capability_call_id=call["id"],
            storage_type="inline",
            inline_content="# Reviewable scope output",
        )
        assert artifact is not None
        assert _has_reviewable_execution_output(store.get_task(task_id), "scope_definition") is True

        result = advance_substage(task_id, "scope_definition", _identity())
        assert result["requiresConfirmation"] is True
    finally:
        store.delete_task(task_id)


@pytest.mark.parametrize("runtime", ["replay", "fixture", "specialist_agent_runtime_gateway"])
def test_replay_fixture_or_control_output_cannot_unlock_advance(runtime: str) -> None:
    task = _make_two_stage_task()
    task_id = task["taskId"]
    try:
        current = store.get_task(task_id)
        assert current is not None
        substage = current["stageDetails"][0]["subStages"][0]
        call = store.add_capability_call(
            task_id,
            skill_id="scope_definition",
            status="completed",
            runtime=runtime,
            stage_key="problem_modeling",
            substage_instance_id=substage["subStageInstanceId"],
        )
        assert call is not None
        artifact = store.add_artifact(
            task_id,
            kind="stage_summary",
            title="Non-live scope output",
            summary="This output must not unlock progression.",
            format="markdown",
            related_stage="problem_modeling",
            related_skill_id="scope_definition",
            substage_instance_id=substage["subStageInstanceId"],
            source_capability_call_id=call["id"],
            storage_type="inline",
            inline_content="# Non-live scope output",
        )
        assert artifact is not None
        assert _has_reviewable_execution_output(store.get_task(task_id), "scope_definition") is False
        with pytest.raises(HTTPException, match="no completed real capability call"):
            advance_substage(task_id, "scope_definition", _identity())
    finally:
        store.delete_task(task_id)


@pytest.mark.asyncio
async def test_replay_chat_call_is_audited_as_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    task = _make_two_stage_task()
    task_id = task["taskId"]
    try:
        monkeypatch.setattr(agent_service.settings, "skill_exec_mode", "replay")
        events = [
            event
            async for event in agent_service.agent_chat_stream(
                task_id,
                "Summarize the current scope without making new claims.",
                skill_id="scope_definition",
                substage_key="scope_definition",
            )
        ]
        terminal = next(event for event in events if event.get("type") == "done")
        assert terminal["runtime"] == "replay"
        call = next(call for call in store.get_task(task_id)["capabilityCalls"] if call["id"] == terminal["capabilityCallId"])
        assert call["runtime"] == "replay"
        assert call["runMode"] == "replay"
        assert call["sourceMode"] == "replay"
    finally:
        store.delete_task(task_id)


def test_artifacts_append_and_stage_substage_refs_merge_into_catalog() -> None:
    task = _make_task()
    task_id = task["taskId"]
    try:
        fresh = store.get_task(task_id)
        substages = [
            (detail["stageKey"], sub)
            for detail in fresh["stageDetails"]
            for sub in detail.get("subStages", [])
        ]
        assert len(substages) == 3

        created_ids: list[str] = []
        for idx, (stage_key, substage) in enumerate(substages, start=1):
            call = store.add_capability_call(
                task_id,
                skill_id=f"integrity_skill_{idx}",
                status="completed",
                runtime="pytest",
                stage_key=stage_key,
                substage_instance_id=substage["subStageInstanceId"],
            )
            artifact = store.add_artifact(
                task_id,
                kind="stage_summary",
                title=f"Artifact {idx}",
                summary=f"Substage artifact {idx}",
                format="markdown",
                related_stage=stage_key,
                related_skill_id=substage["subStage"],
                substage_instance_id=substage["subStageInstanceId"],
                source_capability_call_id=call["id"],
                storage_type="inline",
                inline_content=f"# Artifact {idx}",
            )
            created_ids.append(artifact["id"])

        payload = store.get_task(task_id)
        assert [artifact["id"] for artifact in payload["artifacts"]] == created_ids
        assert all(artifact["taskId"] == task_id for artifact in payload["artifacts"])
        assert all(artifact["sourceTaskId"] == task_id for artifact in payload["artifacts"])
        assert all(artifact["relatedStage"] for artifact in payload["artifacts"])
        assert all(artifact["subStageInstanceId"] for artifact in payload["artifacts"])
        assert [artifact["substage"] for artifact in payload["artifacts"]] == [
            substage["subStage"] for _, substage in substages
        ]
        assert all(artifact["sourceCapabilityCallId"] for artifact in payload["artifacts"])
        assert all("artifactState" in artifact and "artifactType" in artifact for artifact in payload["artifacts"])
        assert all(artifact["artifactState"] == "partial_success" for artifact in payload["artifacts"])
        assert all(artifact["humanGate"]["requiresScopeExpansionConfirmation"] is True for artifact in payload["artifacts"])
        assert payload["executionMode"] == "stage_confirmation"

        store.save_stage_result(
            task_id,
            "problem_modeling",
            {
                "artifactRefs": [created_ids[0]],
                "subStages": [
                    {
                        **payload["stageDetails"][0]["subStages"][0],
                        "artifactRefs": [created_ids[0]],
                        "summary": "updated without dropping refs",
                    }
                ],
            },
        )
        store.save_stage_result(
            task_id,
            "problem_modeling",
            {
                "artifactRefs": [created_ids[1]],
                "subStages": [
                    {
                        **payload["stageDetails"][0]["subStages"][0],
                        "artifactRefs": [created_ids[1]],
                    }
                ],
            },
        )

        payload = store.get_task(task_id)
        problem_stage = next(d for d in payload["stageDetails"] if d["stageKey"] == "problem_modeling")
        solution_stage = next(d for d in payload["stageDetails"] if d["stageKey"] == "solution_design")
        assert problem_stage["artifactRefs"] == created_ids[:2]
        assert solution_stage["artifactRefs"] == [created_ids[2]]
        assert problem_stage["subStages"][0]["artifactRefs"] == created_ids[:2]
        assert problem_stage["subStages"][1]["artifactRefs"] == [created_ids[1]]
        assert solution_stage["subStages"][0]["artifactRefs"] == [created_ids[2]]

        rows, total = list_catalog_artifacts(_identity(), source_task_id=task_id, page_size=10)
        assert total == 3
        assert {row["id"] for row in rows} == set(created_ids)
        assert all(row["sourceTaskId"] == task_id for row in rows)
    finally:
        store.delete_task(task_id)


def test_artifact_repository_row_preserves_stage_substage_metadata() -> None:
    row = {
        "artifact_id": "artifact_repo_roundtrip",
        "owner_id": "owner",
        "workspace_id": "workspace",
        "created_by": "pytest",
        "kind": "stage_summary",
        "origin": "generated",
        "title": "Repository row",
        "summary": "summary",
        "created_at": "2026-07-10T00:00:00Z",
        "format": "markdown",
        "related_stage": "problem_modeling",
        "related_skill_id": "scope_definition",
        "source_capability_call_id": "call_1",
        "source_execution_run_id": "run_1",
        "source_mode": "live",
        "storage_type": "inline",
        "storage_ref": "# body",
        "external_provider": None,
        "external_uri": None,
        "content_size": 6,
        "source_task_id": "task_repo_roundtrip",
        "source_task_title": "Task",
        "metadata": {
            "taskId": "task_repo_roundtrip",
            "stage": "problem_modeling",
            "subStageInstanceId": "sub_1",
            "substage": "scope_definition",
            "artifactState": "ready",
            "artifactType": "stage_summary",
            "sourceMode": "live",
            "sourceOutputPath": "workspace/task_repo_roundtrip/output.md",
            "evidenceRefs": [{"id": "ev_1"}],
            "trustRecords": [{"id": "trust_1"}],
        },
    }

    artifact = _row_to_dict(row)

    assert artifact["taskId"] == "task_repo_roundtrip"
    assert artifact["sourceTaskId"] == "task_repo_roundtrip"
    assert artifact["relatedStage"] == "problem_modeling"
    assert artifact["stage"] == "problem_modeling"
    assert artifact["subStageInstanceId"] == "sub_1"
    assert artifact["substage"] == "scope_definition"
    assert artifact["artifactState"] == "usable"
    assert artifact["artifactType"] == "stage_summary"
    assert artifact["sourceExecutionRunId"] == "run_1"
    assert artifact["sourceMode"] == "live"
    assert artifact["sourceOutputPath"] == "workspace/task_repo_roundtrip/output.md"
    assert artifact["inlineContent"] == "# body"
    assert artifact["evidenceRefs"] == [{"id": "ev_1"}]
    assert artifact["trustRecords"] == [{"id": "trust_1"}]


def test_artifact_repository_row_uses_validator_result_state_and_issues() -> None:
    row = {
        "artifact_id": "artifact_repo_critic",
        "owner_id": "owner",
        "workspace_id": "workspace",
        "created_by": "pytest",
        "kind": "research_note",
        "origin": "generated",
        "title": "Repository critic row",
        "summary": "summary",
        "created_at": "2026-07-10T00:00:00Z",
        "format": "markdown",
        "related_stage": "problem_modeling",
        "related_skill_id": "scope_definition",
        "source_capability_call_id": "call_critic",
        "storage_type": "inline",
        "storage_ref": "# body",
        "external_provider": None,
        "external_uri": None,
        "content_size": 6,
        "source_task_id": "task_repo_critic",
        "source_task_title": "Task",
        "metadata": {
            "taskId": "task_repo_critic",
            "validatorResult": {
                "contractVersion": "artifact-evaluator.v1",
                "evaluator": "critic_agent",
                "artifactId": "artifact_repo_critic",
                "sourceCapabilityCallId": "call_critic",
                "artifactState": "insufficient_materials",
                "usable": False,
                "missingInputs": ["source_record"],
                "issues": [
                    {
                        "code": "missing_source_record",
                        "severity": "blocker",
                        "message": "Missing source record.",
                    }
                ],
                "nextAction": "supplement_materials",
            },
        },
    }

    artifact = _row_to_dict(row)

    assert artifact["artifactState"] == "insufficient_materials"
    assert artifact["validatorResult"]["evaluator"] == "critic_agent"
    assert artifact["validatorResult"]["missingInputs"] == ["source_record"]
    assert artifact["validatorResult"]["issues"][0]["code"] == "missing_source_record"
    assert artifact["humanGate"]["canConfirmPass"] is False


class _FakeArtifactRepository:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, dict, str | None]] = []

    def upsert_from_dict(self, task_id: str, artifact: dict, task_title: str | None = None) -> None:
        self.upserts.append((task_id, copy.deepcopy(artifact), task_title))

    def remove(self, artifact_id: str) -> None:
        pass


def test_storage_patch_remirrors_artifact_repository() -> None:
    previous_repo = store._artifact_repository
    fake_repo = _FakeArtifactRepository()
    store.enable_artifact_repository(fake_repo)
    task = _make_task()
    task_id = task["taskId"]
    try:
        payload = store.get_task(task_id)
        substage = payload["stageDetails"][0]["subStages"][0]
        artifact = store.add_artifact(
            task_id,
            kind="stage_summary",
            title="Patch storage mirror",
            summary=None,
            format="markdown",
            related_stage="problem_modeling",
            related_skill_id=substage["subStage"],
            substage_instance_id=substage["subStageInstanceId"],
        )

        assert fake_repo.upserts[-1][1].get("storageType") is None

        agent_service._patch_artifact_storage(
            task_id,
            artifact["id"],
            storage_type="inline",
            inline_content="# patched body",
        )

        mirrored = fake_repo.upserts[-1][1]
        assert mirrored["id"] == artifact["id"]
        assert mirrored["storageType"] == "inline"
        assert mirrored["inlineContent"] == "# patched body"
        assert mirrored["subStageInstanceId"] == substage["subStageInstanceId"]
        assert mirrored["substage"] == substage["subStage"]
        assert mirrored["artifactState"] == "partial_success"
        assert mirrored["artifactType"] == "stage_summary"
    finally:
        store.delete_task(task_id)
        store._artifact_repository = previous_repo


def test_artifact_review_state_updates_task_and_catalog_mirror() -> None:
    previous_repo = store._artifact_repository
    fake_repo = _FakeArtifactRepository()
    store.enable_artifact_repository(fake_repo)
    task = _make_task()
    task_id = task["taskId"]
    try:
        artifact = store.add_artifact(
            task_id,
            kind="research_note",
            title="Review lifecycle artifact",
            summary="Needs review before formal use.",
            format="markdown",
            related_stage="problem_modeling",
            related_skill_id="scope_definition",
            storage_type="inline",
            inline_content="# draft",
            extra_fields={"artifactState": "pending_review"},
        )
        assert artifact["artifactState"] == "needs_review"

        reviewed = store.update_artifact_review_state(
            task_id,
            artifact["id"],
            artifact_state="usable",
            reviewer="pytest-reviewer",
            note="Looks consistent with source material.",
            source="user",
        )

        assert reviewed is not None
        assert reviewed["artifactState"] == "usable"
        assert reviewed["reviewedBy"] == "pytest-reviewer"
        assert reviewed["trustRecords"][-1]["trustContribution"] == "evidence"

        payload = store.get_task(task_id)
        persisted = next(a for a in payload["artifacts"] if a["id"] == artifact["id"])
        assert persisted["artifactState"] == "usable"
        assert persisted["trustRecords"][-1]["sourceLabel"] == "User review"

        mirrored = fake_repo.upserts[-1][1]
        assert mirrored["id"] == artifact["id"]
        assert mirrored["artifactState"] == "usable"
        assert mirrored["trustRecords"][-1]["trustContribution"] == "evidence"
    finally:
        store.delete_task(task_id)
        store._artifact_repository = previous_repo
