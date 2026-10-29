from app.execution import agent_service
from app.modules.task_store import store


def test_same_output_path_registers_per_substage_runtime_context(monkeypatch, tmp_path) -> None:
    task = store.create_task(
        {
            "ownerId": "artifact-agent-owner",
            "workspaceId": "artifact-agent-workspace",
            "createdBy": "pytest",
            "title": "Agent artifact path dedupe",
            "currentStage": "problem_modeling",
            "status": "running",
            "stages": ["problem_modeling"],
            "creationPlan": {
                "goalSummary": "Verify path dedupe",
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
                    }
                ],
            },
        }
    )
    task_id = task["taskId"]
    try:
        workspace = tmp_path / task_id
        workspace.mkdir()
        (workspace / "shared.md").write_text("# shared output", encoding="utf-8")
        monkeypatch.setattr(agent_service, "get_task_workspace_dir", lambda _task_id: workspace)
        monkeypatch.setattr(agent_service, "_load_l2_skill_meta", lambda _pack, _substage_key: {"output_file": "shared.md"})
        monkeypatch.setattr(agent_service.pack_config, "get_substage_stage", lambda _key, pack="sdr": "problem_modeling")

        payload = store.get_task(task_id)
        substages = payload["stageDetails"][0]["subStages"]
        first_ids = agent_service._try_register_substage_artifact(
            task_id,
            "scope_definition",
            substages[0],
            "call_first",
            pack="sdr",
        )
        second_ids = agent_service._try_register_substage_artifact(
            task_id,
            "evidence_scan",
            substages[1],
            "call_second",
            pack="sdr",
        )

        assert len(first_ids) == 1
        assert len(second_ids) == 1
        assert first_ids != second_ids
        payload = store.get_task(task_id)
        assert [artifact["sourceOutputPath"] for artifact in payload["artifacts"]] == [
            f"workspace/{task_id}/shared.md",
            f"workspace/{task_id}/shared.md",
        ]
        assert [artifact["substage"] for artifact in payload["artifacts"]] == [
            "scope_definition",
            "evidence_scan",
        ]
        assert [artifact["artifactState"] for artifact in payload["artifacts"]] == [
            "needs_review",
            "needs_review",
        ]
        assert all(artifact["humanGate"]["canConfirmPass"] is True for artifact in payload["artifacts"])
    finally:
        store.delete_task(task_id)


def test_review_required_substage_output_enters_needs_review(monkeypatch, tmp_path) -> None:
    task = store.create_task(
        {
            "ownerId": "artifact-agent-owner",
            "workspaceId": "artifact-agent-workspace",
            "createdBy": "pytest",
            "title": "Agent artifact review state",
            "currentStage": "problem_modeling",
            "status": "running",
            "stages": ["problem_modeling"],
            "creationPlan": {
                "goalSummary": "Verify artifact review state",
                "domain": "research_task",
                "skillPack": "sdr",
                "readyToConfirm": True,
                "clarifications": [],
                "stages": [
                    {
                        "stageKey": "problem_modeling",
                        "substages": [{"substage": "scope_definition"}],
                    }
                ],
            },
        }
    )
    task_id = task["taskId"]
    try:
        workspace = tmp_path / task_id
        workspace.mkdir()
        (workspace / "review.md").write_text("# review output", encoding="utf-8")
        monkeypatch.setattr(agent_service, "get_task_workspace_dir", lambda _task_id: workspace)
        monkeypatch.setattr(
            agent_service,
            "_load_l2_skill_meta",
            lambda _pack, _substage_key: {
                "output_file": "review.md",
                "review_required": True,
                "risk_level": "medium",
            },
        )
        monkeypatch.setattr(agent_service.pack_config, "get_substage_stage", lambda _key, pack="sdr": "problem_modeling")

        payload = store.get_task(task_id)
        substage = payload["stageDetails"][0]["subStages"][0]
        created_ids = agent_service._try_register_substage_artifact(
            task_id,
            "scope_definition",
            substage,
            "call_review",
            pack="sdr",
        )

        assert len(created_ids) == 1
        payload = store.get_task(task_id)
        artifact = payload["artifacts"][0]
        assert artifact["artifactState"] == "needs_review"
        assert artifact["humanGate"]["canConfirmPass"] is True
    finally:
        store.delete_task(task_id)


def test_low_risk_review_required_output_is_auto_reviewed(monkeypatch, tmp_path) -> None:
    task = store.create_task(
        {
            "ownerId": "artifact-agent-owner",
            "workspaceId": "artifact-agent-workspace",
            "createdBy": "pytest",
            "title": "Agent artifact low risk review",
            "currentStage": "problem_modeling",
            "status": "running",
            "stages": ["problem_modeling"],
            "creationPlan": {
                "goalSummary": "Verify low risk review agent",
                "domain": "research_task",
                "skillPack": "sdr",
                "readyToConfirm": True,
                "clarifications": [],
                "stages": [
                    {
                        "stageKey": "problem_modeling",
                        "substages": [{"substage": "scope_definition"}],
                    }
                ],
            },
        }
    )
    task_id = task["taskId"]
    try:
        workspace = tmp_path / task_id
        workspace.mkdir()
        (workspace / "low_review.md").write_text("# low risk review output", encoding="utf-8")
        monkeypatch.setattr(agent_service, "get_task_workspace_dir", lambda _task_id: workspace)
        monkeypatch.setattr(
            agent_service,
            "_load_l2_skill_meta",
            lambda _pack, _substage_key: {
                "output_file": "low_review.md",
                "review_required": True,
                "risk_level": "low",
            },
        )
        monkeypatch.setattr(agent_service.pack_config, "get_substage_stage", lambda _key, pack="sdr": "problem_modeling")

        payload = store.get_task(task_id)
        substage = payload["stageDetails"][0]["subStages"][0]
        created_ids = agent_service._try_register_substage_artifact(
            task_id,
            "scope_definition",
            substage,
            "call_low_review",
            pack="sdr",
        )

        assert len(created_ids) == 1
        payload = store.get_task(task_id)
        artifact = payload["artifacts"][0]
        assert artifact["artifactState"] == "usable"
        assert artifact["humanGate"]["canUseDownstream"] is True
        assert artifact["reviewSource"] == "review_agent"
        assert artifact["trustRecords"][-1]["sourceLabel"] == "Review agent"
    finally:
        store.delete_task(task_id)
