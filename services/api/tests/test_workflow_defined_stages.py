from fastapi.testclient import TestClient

from app.main import app
from app.modules.task_store import store


client = TestClient(app)

def _research_start_confirmation_payload() -> dict:
    return {
        "researchGoalConfirmed": True,
        "existingMaterialsConfirmed": True,
        "missingMaterialsDecision": "accept_limitations",
        "nonCitableBoundariesAccepted": True,
        "nextStepLimitationsConfirmed": True,
    }


SDR_STAGE_KEYS = [
    "problem_modeling",
    "solution_design",
    "execution_validation",
    "insight_generation",
]

EXECUTABLE_SUBSTAGE_BY_STAGE = {
    "problem_modeling": "question_refinement",
    "solution_design": "variable_definition",
    "execution_validation": "evidence_extraction",
    "insight_generation": "report_generation",
}


def _editable_plan(task: dict) -> dict:
    plan = task["creationPlan"]
    stages_by_key = {stage["stageKey"]: stage for stage in plan["stages"]}
    return {
        "expectedRevision": task.get("revision", 0),
        "goalSummary": plan["goalSummary"],
        "understanding": {
            "evidenceBoundaries": (plan.get("understanding") or {}).get("evidenceBoundaries") or [],
        },
        "clarifications": plan.get("clarifications") or [],
        "stages": [
            {
                "stageKey": stage_key,
                "title": (stages_by_key.get(stage_key) or {}).get("title") or stage_key,
                "description": (stages_by_key.get(stage_key) or {}).get("description") or stage_key,
                "substages": [
                    {
                        "substage": row["substage"],
                        "label": row.get("label") or row["substage"],
                        "action": row.get("action") or f"Execute {row['substage']}",
                        "expectedArtifact": row.get("expectedArtifact") or f"Output of {row['substage']}",
                    }
                    for row in (stages_by_key.get(stage_key) or {}).get("substages") or []
                ] or [{
                    "substage": EXECUTABLE_SUBSTAGE_BY_STAGE[stage_key],
                    "label": stage_key,
                    "action": f"Execute {stage_key}",
                    "expectedArtifact": f"Output of {stage_key}",
                }],
            }
            for stage_key in SDR_STAGE_KEYS
        ],
    }


def _two_stage_plan() -> dict:
    return {
        "goalSummary": "Validate a minimal workflow-defined task.",
        "domain": "research_task",
        "skillPack": "sdr",
        "clarifications": [],
        "stages": [
            {
                "stageKey": "problem_modeling",
                "description": "Frame the task.",
                "subStages": [],
                "substages": [{"substage": "research_intake", "skills": ["research-start"]}],
            },
            {
                "stageKey": "insight_generation",
                "description": "Summarize the result.",
                "subStages": [],
                "substages": [{"substage": "next_step_confirmation", "skills": ["quality-audit"]}],
            },
        ],
        "readyToConfirm": True,
    }


def _create_two_stage_task() -> dict:
    res = client.post(
        "/api/tasks",
        json={
            "title": "Two-stage workflow task",
            "goalSummary": "Validate a minimal workflow-defined task.",
            "status": "running",
            "creationStatus": "confirmed",
            "currentStage": "problem_modeling",
            "creationPlan": _two_stage_plan(),
        },
    )
    assert res.status_code == 201
    return res.json()


def _three_stage_plan() -> dict:
    return {
        "goalSummary": "Validate repeated stage confirmation gates.",
        "domain": "research_task",
        "skillPack": "sdr",
        "clarifications": [],
        "stages": [
            {
                "stageKey": "problem_modeling",
                "description": "Frame the task.",
                "subStages": [],
                "substages": [{"substage": "research_intake", "skills": ["research-start"]}],
            },
            {
                "stageKey": "solution_design",
                "description": "Design the next step.",
                "subStages": [],
                "substages": [{"substage": "workflow_design", "skills": ["task-breakdown"]}],
            },
            {
                "stageKey": "insight_generation",
                "description": "Summarize the result.",
                "subStages": [],
                "substages": [{"substage": "next_step_confirmation", "skills": ["quality-audit"]}],
            },
        ],
        "readyToConfirm": True,
    }


def _create_three_stage_task() -> dict:
    res = client.post(
        "/api/tasks",
        json={
            "title": "Three-stage workflow task",
            "goalSummary": "Validate repeated stage confirmation gates.",
            "status": "running",
            "creationStatus": "confirmed",
            "currentStage": "problem_modeling",
            "creationPlan": _three_stage_plan(),
        },
    )
    assert res.status_code == 201
    return res.json()


def _stage(body: dict, stage_key: str) -> dict:
    return next(stage for stage in body["stageDetails"] if stage["stageKey"] == stage_key)


def _record_live_output(task_id: str, stage_key: str) -> None:
    task = store.get_task(task_id)
    assert task is not None
    substage = next(row for row in _stage(task, stage_key)["subStages"] if row["status"] == "running")
    call = store.add_capability_call(
        task_id,
        skill_id=substage["subStage"],
        status="completed",
        runtime="live",
        stage_key=stage_key,
        substage_instance_id=substage["subStageInstanceId"],
    )
    assert call is not None
    artifact = store.add_artifact(
        task_id,
        kind="stage_summary",
        title=f"Reviewable output for {substage['subStage']}",
        related_stage=stage_key,
        related_skill_id=substage["subStage"],
        substage_instance_id=substage["subStageInstanceId"],
        source_capability_call_id=call["id"],
        storage_type="inline",
        inline_content="# Reviewable live output",
    )
    assert artifact is not None


def test_workflow_defined_stages_do_not_expand_to_default_stage_order():
    task = _create_two_stage_task()
    task_id = task["taskId"]

    assert task["stages"] == ["problem_modeling", "insight_generation"]
    assert [
        entry["key"]
        for stage in task["stageCatalog"]
        for entry in stage["allowedSubStages"]
    ] == ["research_intake", "next_step_confirmation"]
    assert [stage["stageKey"] for stage in task["stageDetails"]] == [
        "problem_modeling",
        "insight_generation",
    ]
    assert task["stageDetails"][0]["subStages"][0]["subStage"] == "research_intake"
    assert task["stageDetails"][0]["subStages"][0]["status"] == "running"
    assert task["stageDetails"][1]["subStages"][0]["subStage"] == "next_step_confirmation"
    assert task["stageDetails"][1]["subStages"][0]["status"] == "not_started"
    assert "solution_design" not in task["stages"]
    assert "execution_validation" not in task["stages"]

    res = client.get(f"/api/tasks/{task_id}")
    assert res.status_code == 200
    returned = res.json()
    assert returned["stages"] == ["problem_modeling", "insight_generation"]
    assert [stage["stageKey"] for stage in returned["stageDetails"]] == [
        "problem_modeling",
        "insight_generation",
    ]


def test_direct_workflow_task_advances_to_stage_confirmation_gate_then_resumes():
    task = _create_two_stage_task()
    task_id = task["taskId"]

    _record_live_output(task_id, "problem_modeling")
    res = client.post(f"/api/tasks/{task_id}/advance-substage")
    assert res.status_code == 200
    gated = res.json()
    assert gated["hasNext"] is True
    assert gated["requiresConfirmation"] is True
    assert gated["stageKey"] == "problem_modeling"
    assert gated["nextStageKey"] == "insight_generation"
    assert gated["substageKey"] == "research_intake"
    assert gated["nextSubstageKey"] == "next_step_confirmation"

    returned = client.get(f"/api/tasks/{task_id}")
    assert returned.status_code == 200
    body = returned.json()
    assert body["status"] == "awaiting_confirmation"
    assert body["currentStage"] == "problem_modeling"
    assert body["stageDetails"][0]["status"] == "completed"
    assert body["stageDetails"][0]["subStages"][0]["status"] == "completed"
    assert body["stageDetails"][1]["status"] == "not_started"
    assert body["stageDetails"][1]["subStages"][0]["status"] == "not_started"

    resume_status = client.patch(f"/api/tasks/{task_id}/status", json={"status": "running"})
    assert resume_status.status_code == 200
    assert resume_status.json()["stageGateResumeApproved"] is True

    resumed = client.post(f"/api/tasks/{task_id}/advance-substage")
    assert resumed.status_code == 200
    resumed_body = resumed.json()
    assert resumed_body["hasNext"] is True
    assert "requiresConfirmation" not in resumed_body
    assert resumed_body["stageKey"] == "insight_generation"
    assert resumed_body["substageKey"] == "next_step_confirmation"

    returned = client.get(f"/api/tasks/{task_id}")
    assert returned.status_code == 200
    body = returned.json()
    assert body["status"] == "running"
    assert body["currentStage"] == "insight_generation"
    assert body["stageDetails"][1]["status"] == "running"
    assert body["stageDetails"][1]["subStages"][0]["status"] == "running"


def test_regular_status_resume_does_not_bypass_stage_confirmation_gate():
    task = _create_two_stage_task()
    task_id = task["taskId"]

    _record_live_output(task_id, "problem_modeling")
    paused = client.patch(f"/api/tasks/{task_id}/status", json={"status": "awaiting_confirmation"})
    assert paused.status_code == 200

    resumed = client.patch(f"/api/tasks/{task_id}/status", json={"status": "running"})
    assert resumed.status_code == 200
    assert resumed.json()["stageGateResumeApproved"] is False

    advanced = client.post(f"/api/tasks/{task_id}/advance-substage")
    assert advanced.status_code == 200
    assert advanced.json()["requiresConfirmation"] is True


def test_running_task_without_active_substage_blocks_advance_and_mode_switch():
    task = _create_two_stage_task()
    task_id = task["taskId"]
    substage_id = _stage(task, "problem_modeling")["subStages"][0]["subStageInstanceId"]
    store.patch_substage_instance(task_id, substage_id, status="completed")

    advanced = client.post(f"/api/tasks/{task_id}/advance-substage")
    assert advanced.status_code == 409
    assert "no active substage found" not in advanced.text
    assert "No active substage is available" in advanced.json()["detail"]

    mode_switch = client.patch(
        f"/api/tasks/{task_id}/execution-mode",
        json={"executionMode": "manual_step"},
    )
    assert mode_switch.status_code == 409
    assert "no active substage found" not in mode_switch.text
    assert "No active substage is available" in mode_switch.json()["detail"]


def test_stage_gate_resume_is_consumed_before_next_stage_gate():
    task = _create_three_stage_task()
    task_id = task["taskId"]

    _record_live_output(task_id, "problem_modeling")
    first_gate = client.post(f"/api/tasks/{task_id}/advance-substage")
    assert first_gate.status_code == 200
    assert first_gate.json()["requiresConfirmation"] is True
    assert first_gate.json()["nextStageKey"] == "solution_design"

    direct_resume = client.post(f"/api/tasks/{task_id}/advance-substage")
    assert direct_resume.status_code == 409

    resume_status = client.patch(f"/api/tasks/{task_id}/status", json={"status": "running"})
    assert resume_status.status_code == 200
    entered_solution = client.post(f"/api/tasks/{task_id}/advance-substage")
    assert entered_solution.status_code == 200
    assert entered_solution.json()["stageKey"] == "solution_design"
    assert entered_solution.json()["substageKey"] == "workflow_design"

    _record_live_output(task_id, "solution_design")
    second_gate = client.post(f"/api/tasks/{task_id}/advance-substage")
    assert second_gate.status_code == 200
    assert second_gate.json()["requiresConfirmation"] is True
    assert second_gate.json()["stageKey"] == "solution_design"
    assert second_gate.json()["nextStageKey"] == "insight_generation"

    blocked_without_second_resume = client.post(f"/api/tasks/{task_id}/advance-substage")
    assert blocked_without_second_resume.status_code == 409


def test_stage_result_api_persists_stage_guard_metadata():
    task = _create_two_stage_task()
    task_id = task["taskId"]

    payload = {
        "label": "Frame and qualify",
        "summary": "Problem framing is ready for review.",
        "entryCondition": "User supplied a research goal.",
        "completionCondition": "Scope, evidence needs, and risks are explicit.",
        "lockedReason": "Waiting for human review.",
        "primaryAction": "Review the scoped problem statement.",
        "canSkip": False,
        "canReview": True,
        "canLock": True,
        "status": "running",
    }
    res = client.post(f"/api/tasks/{task_id}/stages/problem_modeling/result", json=payload)
    assert res.status_code == 200

    returned = client.get(f"/api/tasks/{task_id}")
    assert returned.status_code == 200
    stage = next(
        stage
        for stage in returned.json()["stageDetails"]
        if stage["stageKey"] == "problem_modeling"
    )
    for key, value in payload.items():
        assert stage[key] == value


def test_direct_draft_task_uses_trimmed_execution_graph_shape():
    goal = "Study solid electrolyte interface stability and plan follow-up experiments."
    res = client.post("/api/tasks/draft", json={"goalSummary": goal})
    assert res.status_code == 201
    task = res.json()

    plan = task["creationPlan"]
    assert task["status"] == "draft"
    assert task["creationStatus"] == "awaiting_confirmation"
    assert task["skillPack"] == "sdr"
    plan_stage_keys = [stage["stageKey"] for stage in plan["stages"]]
    assert plan_stage_keys
    assert plan_stage_keys == task["stages"]
    assert [stage["stageKey"] for stage in task["stageDetails"]] == plan_stage_keys
    assert all(stage in SDR_STAGE_KEYS for stage in plan_stage_keys)

    contextual_descriptions = []
    for stage in plan["stages"]:
        assert stage["description"]
        contextual_descriptions.append(stage["description"].lower())
        substage_keys = [row["substage"] for row in stage["substages"]]
        assert substage_keys
        assert stage["subStages"] == substage_keys
    assert any("solid electrolyte" in description for description in contextual_descriptions)

    catalog_by_stage = {
        row["stageKey"]: [entry["key"] for entry in row["allowedSubStages"]]
        for row in task["stageCatalog"]
    }
    details_by_stage = {
        row["stageKey"]: [entry["subStage"] for entry in row["subStages"]]
        for row in task["stageDetails"]
    }
    for stage in plan["stages"]:
        stage_key = stage["stageKey"]
        expected = [row["substage"] for row in stage["substages"]]
        assert catalog_by_stage[stage_key] == expected
        assert details_by_stage[stage_key] == expected

    flattened_substages = [
        substage
        for stage_substages in details_by_stage.values()
        for substage in stage_substages
    ]
    assert 2 <= len(flattened_substages) < 28
    assert "research_intake" not in flattened_substages
    assert all(detail.get("label") in (None, "") for detail in task["stageDetails"])


def test_direct_draft_task_confirmed_json_start_fails_closed_without_postgres():
    res = client.post(
        "/api/tasks/draft",
        json={"goalSummary": "Study solid electrolyte interface stability and plan follow-up experiments."},
    )
    assert res.status_code == 201
    task = res.json()
    task_id = task["taskId"]
    draft_artifact_refs = task["stageDetails"][0]["artifactRefs"]
    ordered_substages = [
        row["key"]
        for stage in task["stageCatalog"]
        for row in stage["allowedSubStages"]
    ]
    assert len(ordered_substages) >= 2
    confirmed = client.post(f"/api/tasks/{task_id}/confirm-plan", json=_research_start_confirmation_payload())
    assert confirmed.status_code == 200
    confirmed_task = confirmed.json()
    assert confirmed_task["status"] == "not_started"
    assert confirmed_task["creationStatus"] == "confirmed"
    assert [stage["stageKey"] for stage in confirmed_task["stageDetails"]] == task["stages"]
    assert [
        row["subStage"]
        for stage in confirmed_task["stageDetails"]
        for row in stage["subStages"]
    ] == [
        row["key"]
        for stage in confirmed_task["stageCatalog"]
        for row in stage["allowedSubStages"]
    ]
    assert sum(len(stage["subStages"]) for stage in confirmed_task["stageDetails"]) < 28
    assert all(
        substage["status"] == "not_started"
        for stage in confirmed_task["stageDetails"]
        for substage in stage["subStages"]
    )
    assert confirmed_task["stageDetails"][0]["summary"]
    assert confirmed_task["stageDetails"][0]["artifactRefs"] == draft_artifact_refs

    started = client.post(f"/api/tasks/{task_id}/start")
    assert started.status_code == 503
    assert started.json()["detail"] == "durable_execution_requires_postgres"
    persisted = store.get_task(task_id)
    assert persisted is not None
    assert persisted["status"] == "not_started"

def test_draft_creation_plan_can_be_adjusted_before_confirmation():
    res = client.post(
        "/api/tasks/draft",
        json={"goalSummary": "Study a research direction without enough details."},
    )
    assert res.status_code == 201
    task = res.json()
    task_id = task["taskId"]
    editable = _editable_plan(task)
    first_stage = editable["stages"][0]
    original = [row["substage"] for row in first_stage["substages"]]
    adjusted = [original[1], original[0], *original[3:]]

    rows = {row["substage"]: row for row in first_stage["substages"]}
    first_stage["substages"] = [rows[key] for key in adjusted]

    updated = client.post(f"/api/tasks/{task_id}/update-plan", json=editable)
    assert updated.status_code == 200
    body = updated.json()

    assert body["status"] == "draft"
    assert body["creationStatus"] == "awaiting_confirmation"
    assert [row["substage"] for row in body["creationPlan"]["stages"][0]["substages"]] == adjusted
    assert [row["subStage"] for row in body["stageDetails"][0]["subStages"]] == adjusted

    confirmed = client.post(f"/api/tasks/{task_id}/confirm-plan", json=_research_start_confirmation_payload())
    assert confirmed.status_code == 200
    confirmed_body = confirmed.json()
    assert [row["subStage"] for row in confirmed_body["stageDetails"][0]["subStages"]] == adjusted


def test_draft_creation_plan_allows_empty_unselected_fixed_stages():
    res = client.post(
        "/api/tasks/draft",
        json={"goalSummary": "Study reliable code feedback for multilingual learners."},
    )
    assert res.status_code == 201
    task = res.json()
    editable = _editable_plan(task)
    selected = editable["stages"][0]["substages"][:1]
    assert selected
    editable["stages"][0]["substages"] = selected
    for stage in editable["stages"][1:]:
        stage["substages"] = []

    updated = client.post(f"/api/tasks/{task['taskId']}/update-plan", json=editable)

    assert updated.status_code == 200
    body = updated.json()
    assert [stage["stageKey"] for stage in body["creationPlan"]["stages"]] == SDR_STAGE_KEYS
    assert len(body["creationPlan"]["stages"][0]["substages"]) == 1
    assert all(stage["substages"] == [] for stage in body["creationPlan"]["stages"][1:])
    assert len(body["stageDetails"][0]["subStages"]) == 1
    assert all(stage["subStages"] == [] for stage in body["stageDetails"][1:])
    assert len(body["stageCatalog"][0]["allowedSubStages"]) == 1
    assert all(stage["allowedSubStages"] == [] for stage in body["stageCatalog"][1:])
    persisted = store.get_task(task["taskId"])
    assert persisted is not None
    assert [row["substage"] for row in persisted["creationPlan"]["stages"][0]["substages"]] == [
        selected[0]["substage"]
    ]
    assert body["status"] == "draft"
    assert body["creationStatus"] == "awaiting_confirmation"
    assert body["capabilityCalls"] == []

    confirmed = client.post(
        f"/api/tasks/{task['taskId']}/confirm-plan",
        json=_research_start_confirmation_payload(),
    )
    assert confirmed.status_code == 200
    confirmed_body = confirmed.json()
    assert all(stage["subStages"] == [] for stage in confirmed_body["stageDetails"][1:])
    assert all(stage["allowedSubStages"] == [] for stage in confirmed_body["stageCatalog"][1:])
    assert confirmed_body["capabilityCalls"] == []


def test_draft_creation_plan_rejects_all_empty_fixed_stages():
    res = client.post(
        "/api/tasks/draft",
        json={"goalSummary": "Study reliable code feedback for multilingual learners."},
    )
    assert res.status_code == 201
    task = res.json()
    editable = _editable_plan(task)
    for stage in editable["stages"]:
        stage["substages"] = []

    rejected = client.post(f"/api/tasks/{task['taskId']}/update-plan", json=editable)

    assert rejected.status_code == 422
    assert "at least one substage" in str(rejected.json())


def test_draft_creation_plan_update_requires_complete_substage_rows():
    res = client.post(
        "/api/tasks/draft",
        json={"goalSummary": "Study solid electrolyte interface stability and plan follow-up experiments."},
    )
    assert res.status_code == 201
    task = res.json()
    task_id = task["taskId"]
    editable = _editable_plan(task)
    editable["stages"][0]["substages"][0].pop("action")

    updated = client.post(f"/api/tasks/{task_id}/update-plan", json=editable)
    assert updated.status_code == 422


def test_draft_creation_plan_rejects_cross_stage_substage_move():
    res = client.post(
        "/api/tasks/draft",
        json={"goalSummary": "Study solid electrolyte interface stability and plan follow-up experiments."},
    )
    assert res.status_code == 201
    task = res.json()
    task_id = task["taskId"]
    editable = _editable_plan(task)
    problem_substage = editable["stages"][0]["substages"][0]
    editable["stages"][1]["substages"] = [problem_substage]

    rejected = client.post(f"/api/tasks/{task_id}/update-plan", json=editable)
    assert rejected.status_code == 400
    assert "belongs to" in rejected.json()["detail"]


def test_draft_creation_plan_rejects_unknown_substage():
    res = client.post(
        "/api/tasks/draft",
        json={"goalSummary": "Study solid electrolyte interface stability and plan follow-up experiments."},
    )
    assert res.status_code == 201
    task = res.json()
    task_id = task["taskId"]
    editable = _editable_plan(task)
    editable["stages"][0]["substages"] = [{
        "substage": "not_a_real_substage",
        "label": "Unknown step",
        "action": "Attempt an unknown step",
        "expectedArtifact": "Unknown output",
    }]

    rejected = client.post(f"/api/tasks/{task_id}/update-plan", json=editable)
    assert rejected.status_code == 400
    assert "not available" in rejected.json()["detail"]
