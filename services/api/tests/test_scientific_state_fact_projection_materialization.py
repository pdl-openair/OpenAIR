from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.modules.scientific_state_fact_projection import (
    materialize_scientific_state_projection,
    project_validated_structured_result,
    validate_scientific_state_projection_declaration,
)


OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["experimental_system_context"],
    "properties": {
        "experimental_system_context": {"type": "object"},
    },
}


def test_v1_projection_remains_zero_reshape_and_does_not_materialize_state() -> None:
    declaration = validate_scientific_state_projection_declaration(
        {
            "contract_version": 1,
            "facts": [
                {
                    "fact_key": "experimental_system_context",
                    "source_field": "experimental_system_context",
                }
            ],
        },
        output_schema=OUTPUT_SCHEMA,
    )
    content = json.dumps({"experimental_system_context": {"contract": "ctx"}})
    projected = project_validated_structured_result(content, declaration)
    payload = {"taskId": "task-1", "scientificStates": []}

    result = materialize_scientific_state_projection(
        task_payload=payload,
        declaration=declaration,
        projected_facts=projected,
        source_artifact_id="artifact-1",
        research_design_version=1,
        occurred_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )

    assert result == payload


def test_v2_projection_requires_known_explicit_materializer() -> None:
    with pytest.raises(
        ValueError,
        match="scientific_state_projection_materializer_invalid",
    ):
        validate_scientific_state_projection_declaration(
            {
                "contract_version": 2,
                "materializer_ref": "unknown:materializer:v1",
                "facts": [
                    {
                        "fact_key": "experimental_system_context",
                        "source_field": "experimental_system_context",
                    }
                ],
            },
            output_schema=OUTPUT_SCHEMA,
        )


def test_v2_projection_accepts_pvd_recommendation_materializer() -> None:
    declaration = validate_scientific_state_projection_declaration(
        {
            "contract_version": 2,
            "materializer_ref": "pvd:round-experiment-recommendation:v1",
            "facts": [
                {
                    "fact_key": "pvd_recommendation_decision_status",
                    "source_field": "decision_status",
                },
                {
                    "fact_key": "pvd_recommendation_first_run_ranking_status",
                    "source_field": "first_run_ranking_status",
                },
                {
                    "fact_key": "pvd_recommendation_experiments",
                    "source_field": "experiments",
                },
                {
                    "fact_key": "pvd_recommendation_blocked_experiments",
                    "source_field": "blocked_experiments",
                },
                {
                    "fact_key": "pvd_recommendation_next_step",
                    "source_field": "next_step",
                },
                {
                    "fact_key": "pvd_recommendation_important_unknowns",
                    "source_field": "important_unknowns",
                },
            ],
        },
        output_schema={
            "type": "object",
            "required": [
                "decision_status",
                "first_run_ranking_status",
                "experiments",
                "blocked_experiments",
                "next_step",
                "important_unknowns",
            ],
            "properties": {
                "decision_status": {"type": "string"},
                "first_run_ranking_status": {"type": "string"},
                "experiments": {"type": "array"},
                "blocked_experiments": {"type": "array"},
                "next_step": {"type": "string"},
                "important_unknowns": {"type": "array"},
            },
        },
    )
    assert declaration["materializer_ref"] == "pvd:round-experiment-recommendation:v1"


def test_v2_projection_materializes_pvd_state() -> None:
    declaration = validate_scientific_state_projection_declaration(
        {
            "contract_version": 2,
            "materializer_ref": "pvd:experimental-system-context:v1",
            "facts": [
                {
                    "fact_key": "experimental_system_context",
                    "source_field": "experimental_system_context",
                }
            ],
        },
        output_schema=OUTPUT_SCHEMA,
    )
    content = json.dumps({"experimental_system_context": {"contract": "ctx"}})
    projected = project_validated_structured_result(content, declaration)

    result = materialize_scientific_state_projection(
        task_payload={
            "taskId": "task-1",
            "status": "running",
            "finishedAt": None,
            "scientificStates": [],
            "finalResultAuthority": None,
        },
        declaration=declaration,
        projected_facts=projected,
        source_artifact_id="artifact-1",
        research_design_version=1,
        occurred_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )

    assert len(result["scientificStates"]) == 1
    facts = result["scientificStates"][0]["scientificFacts"]
    assert facts["current_experimental_system_context_ref"].startswith(
        "pvd-experimental-system-context:"
    )
    assert facts["experiment_history"] == []
    assert facts["experimental_system_contexts"][0]["experimental_system_context"] == {
        "contract": "ctx"
    }


def test_projection_preserves_available_facts_without_inventing_missing_fields(caplog) -> None:
    declaration = {"contract_version": 1, "facts": [
        {"fact_key": "available", "source_field": "present"},
        {"fact_key": "missing", "source_field": "absent"},
    ]}
    content = json.dumps({"present": {"limits": ["unknown"]}, "extra": "retained"})
    assert project_validated_structured_result(content, declaration) == {
        "available": {"limits": ["unknown"]},
    }
    assert "scientific_state_projection_source_missing" in caplog.text
    assert json.loads(content)["extra"] == "retained"
