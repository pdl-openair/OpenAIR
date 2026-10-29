from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.modules.cs_reproduction_scientific_state_materialization import (
    materialize_cs_reproduction_inputs,
)


def _payload(goal: str) -> dict:
    return {
        "taskId": "task-cs-w1",
        "status": "running",
        "finishedAt": None,
        "finalResultAuthority": None,
        "scientificStates": [],
        "artifacts": [{"artifactId": "artifact-1", "workUnitId": "work-1"}],
        "workUnits": [
            {
                "workUnitId": "work-1",
                "researchDesignVersion": 1,
                "invocationContext": {"canonical_user_query": goal},
            }
        ],
    }


def _facts(repository: str | None, revision: str | None, unresolved: list[str]) -> dict:
    return {
        "reproduction_repository": repository,
        "reproduction_revision": revision,
        "reproduction_unresolved_inputs": unresolved,
    }


def test_materializer_accepts_only_traceable_confirmed_inputs() -> None:
    result = materialize_cs_reproduction_inputs(
        task_payload=_payload(
            "复现实验；repository=https://example.com/repo.git；revision=v1.0"
        ),
        projected_facts=_facts("https://example.com/repo.git", "v1.0", []),
        source_artifact_id="artifact-1",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 15, tzinfo=UTC),
    )
    facts = result["scientificStates"][-1]["scientificFacts"]
    assert facts["reproduction_repository"] == "https://example.com/repo.git"
    assert facts["reproduction_revision"] == "v1.0"
    assert facts["reproduction_unresolved_inputs"] == []


def test_materializer_rejects_value_not_present_in_confirmed_goal() -> None:
    with pytest.raises(ValueError, match="cs_reproduction_repository_source_mismatch"):
        materialize_cs_reproduction_inputs(
            task_payload=_payload("复现指定论文实验；revision=v1.0"),
            projected_facts=_facts("https://hallucinated.example/repo.git", "v1.0", []),
            source_artifact_id="artifact-1",
            research_design_version=1,
            occurred_at=datetime(2026, 9, 15, tzinfo=UTC),
        )


def test_materializer_preserves_traceable_input_with_incomplete_missingness_metadata() -> None:
    result = materialize_cs_reproduction_inputs(
        task_payload=_payload("复现指定论文实验；repository=https://example.com/repo.git"),
        projected_facts=_facts("https://example.com/repo.git", None, []),
        source_artifact_id="artifact-1",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 15, tzinfo=UTC),
    )
    facts = result["scientificStates"][-1]["scientificFacts"]
    assert facts["reproduction_repository"] == "https://example.com/repo.git"
    assert "reproduction_revision" not in facts
    assert facts["reproduction_unresolved_inputs"] == ["revision"]


def test_materializer_keeps_missing_inputs_unknown_in_scientific_state() -> None:
    result = materialize_cs_reproduction_inputs(
        task_payload=_payload("复现指定论文实验；repository=https://example.com/repo.git"),
        projected_facts=_facts("https://example.com/repo.git", None, ["revision"]),
        source_artifact_id="artifact-1",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 15, tzinfo=UTC),
    )
    facts = result["scientificStates"][-1]["scientificFacts"]
    assert facts["reproduction_repository"] == "https://example.com/repo.git"
    assert "reproduction_revision" not in facts
    assert facts["reproduction_unresolved_inputs"] == ["revision"]


def test_materializer_keeps_valid_repository_when_revision_field_is_bad() -> None:
    result = materialize_cs_reproduction_inputs(
        task_payload=_payload("复现指定论文实验；repository=https://example.com/repo.git"),
        projected_facts={"reproduction_repository": "https://example.com/repo.git",
                         "reproduction_revision": {"not": "an identity"}},
        source_artifact_id="artifact-1",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 15, tzinfo=UTC),
    )
    facts = result["scientificStates"][-1]["scientificFacts"]
    assert facts["reproduction_repository"] == "https://example.com/repo.git"
    assert "reproduction_revision" not in facts
    assert facts["reproduction_unresolved_inputs"] == ["revision"]


def test_partial_projection_cannot_erase_an_earlier_accepted_execution_identity() -> None:
    first = materialize_cs_reproduction_inputs(
        task_payload=_payload("repository=https://example.com/repo.git；revision=v1.0"),
        projected_facts=_facts("https://example.com/repo.git", "v1.0", []),
        source_artifact_id="artifact-1", research_design_version=1,
        occurred_at=datetime(2026, 9, 15, tzinfo=UTC),
    )
    second = materialize_cs_reproduction_inputs(
        task_payload=first, projected_facts={"reproduction_revision": None},
        source_artifact_id="artifact-1", research_design_version=1,
        occurred_at=datetime(2026, 9, 16, tzinfo=UTC),
    )
    facts = second["scientificStates"][-1]["scientificFacts"]
    assert facts["reproduction_repository"] == "https://example.com/repo.git"
    assert facts["reproduction_revision"] == "v1.0"
    assert facts["reproduction_unresolved_inputs"] == []
