import pytest

from app.modules.task_execution_graph import (
    ExecutionGraphError,
    build_creation_plan_stages_from_skill_ids,
    build_creation_plan_stages_from_substages,
)


def _flatten(stages: list[dict]) -> list[str]:
    return [
        row["substage"]
        for stage in stages
        for row in stage.get("substages", [])
    ]


def test_build_creation_plan_stages_from_skill_metadata() -> None:
    stages = build_creation_plan_stages_from_skill_ids(
        ["pvd_literature_evidence_acquisition_closure"],
        pack="pvd",
        stage_descriptions={"execution_validation": "Extract structured evidence."},
    )

    assert [stage["stageKey"] for stage in stages] == ["execution_validation"]
    assert _flatten(stages) == ["literature_experiment_record_verification"]
    assert stages[0]["subStages"] == ["literature_experiment_record_verification"]
    assert stages[0]["description"] == "Extract structured evidence."
    assert stages[0]["substages"][0]["skills"] == [
        "pvd_literature_evidence_acquisition_closure"
    ]


def test_build_creation_plan_stages_fails_closed_when_skill_has_no_substage_metadata() -> None:
    with pytest.raises(ExecutionGraphError):
        build_creation_plan_stages_from_skill_ids(["new_task_planning"], pack="sdr")


def test_build_creation_plan_stages_preserves_planner_metadata() -> None:
    stages = build_creation_plan_stages_from_substages(
        [
            {
                "substage": "literature_experiment_record_verification",
                "skills": ["pvd_literature_evidence_acquisition_closure"],
                "rationale": "Evidence table requires structured extraction.",
                "expectedArtifactTypes": ["evidence_table"],
                "confirmationRequired": True,
            }
        ],
        pack="pvd",
    )

    row = stages[0]["substages"][0]
    assert row["substage"] == "literature_experiment_record_verification"
    assert row["rationale"] == "Evidence table requires structured extraction."
    assert row["expectedArtifactTypes"] == ["evidence_table"]
    assert row["confirmationRequired"] is True
