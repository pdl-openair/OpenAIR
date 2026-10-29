from __future__ import annotations

import pytest

from app.models.agent_schemas import AgentMessageOut
from app.models.schemas import ArtifactOut, CapabilityCallOut, TaskOut
from app.modules.system_agents import (
    build_presentation_agent_narrative,
    build_review_agent_narrative,
    build_summary_agent_narrative,
)
from app.modules.task_store import store


@pytest.fixture(autouse=True)
def isolated_store():
    original = store._tasks.copy()
    store._tasks.clear()
    try:
        yield
    finally:
        store._tasks.clear()
        store._tasks.update(original)


def _create_task() -> str:
    task = store.create_task(
        {
            "title": "Narrative task",
            "goalSummary": "Check user-facing narratives",
            "domain": "research_task",
            "currentStage": "problem_modeling",
            "status": "running",
            "ownerId": "owner_narrative",
            "workspaceId": "ws_narrative",
        }
    )
    return str(task["taskId"])


def test_user_facing_narratives_persist_and_validate_on_core_records() -> None:
    task_id = _create_task()

    message_narrative = build_summary_agent_narrative(
        "已经完成摘要。这里是较长的 Markdown 细节。",
        source_id="call_test",
        severity="success",
    )
    msg = store.append_agent_message(
        task_id,
        role="assistant",
        text="已经完成摘要。这里是较长的 Markdown 细节。",
        runtime="replay",
        user_facing_narrative=message_narrative,
    )

    call = store.add_capability_call(
        task_id,
        skill_id="summary_agent",
        status="running",
        runtime="replay",
        stage_key="problem_modeling",
    )
    assert call is not None
    call_narrative = build_presentation_agent_narrative(
        "产物已经生成。",
        source_type="capability_call",
        source_id=call["id"],
        artifact_refs=["artifact_test"],
        status="completed",
    )
    patched_call = store.patch_capability_call(
        task_id,
        call["id"],
        status="completed",
        result_summary="产物已经生成。",
        metadata={"userFacingNarrative": call_narrative},
    )

    artifact_narrative = build_presentation_agent_narrative(
        "这是一份可读摘要产物。",
        source_type="artifact",
        source_id="artifact_test",
        status="completed",
    )
    artifact = store.add_artifact(
        task_id,
        kind="agent_summary",
        title="可读摘要",
        summary="这是一份可读摘要产物。",
        related_stage="problem_modeling",
        extra_fields={
            "artifactState": "pending_review",
            "userFacingNarrative": artifact_narrative,
        },
    )
    assert artifact is not None

    reviewed = store.update_artifact_review_state(
        task_id,
        artifact["id"],
        artifact_state="verified_artifact",
        reviewer="openair_review_agent",
        note="复核通过，可以作为后续报告输入。",
        source="review_agent",
    )

    assert msg is not None
    assert patched_call is not None
    assert reviewed is not None
    assert AgentMessageOut.model_validate(msg).userFacingNarrative is not None
    assert CapabilityCallOut.model_validate(patched_call).userFacingNarrative is not None
    reviewed_out = ArtifactOut.model_validate(reviewed)
    assert reviewed_out.userFacingNarrative is not None
    assert reviewed_out.userFacingNarrative.summary.endswith("已通过复核。")
    assert TaskOut.model_validate(store.get_task(task_id)).artifacts[0].userFacingNarrative is not None


def test_review_agent_narrative_marks_revision_as_warning() -> None:
    narrative = build_review_agent_narrative(
        artifact_title="证据表",
        decision="rejected_or_needs_revision",
        note="缺少来源边界。",
        source_id="artifact_x",
    )

    assert narrative["summary"] == "证据表需要修改后再用。"
    assert narrative["severity"] == "warning"
    assert narrative["detailCollapsedByDefault"] is True
