from __future__ import annotations

import json

import pytest

from app.execution.research_start_provider_adapter import ProviderContractError
from app.modules import research_start_planner as planner_module
from app.modules.research_start_planner import execute_planner_attempt, prepare_planner_attempt


CATALOG = [
    {
        "definitionId": "sdr:variable_definition",
        "definitionVersion": 1,
        "pack": "sdr",
        "planningApplicability": {
            "scopeKind": "general",
            "scopeLabel": "SDR",
            "scopeDescription": "General research work",
            "whenToUse": "Use across domains when variable definition is needed.",
        },
        "stage": "solution_design",
        "label": "Variable definition",
        "description": "Define variables and comparison dimensions.",
        "instancePolicy": {"mode": "repeatable"},
        "requiredInputRefs": ["user:request"],
        "artifactTypes": ["variable_spec"],
        "criterionRefs": ["variable_validator"],
    },
    {
        "definitionId": "sdr:ablation_design",
        "definitionVersion": 1,
        "pack": "sdr",
        "planningApplicability": {
            "scopeKind": "general",
            "scopeLabel": "SDR",
            "scopeDescription": "General research work",
            "whenToUse": "Use across domains when ablation design is needed.",
        },
        "stage": "solution_design",
        "label": "Ablation design",
        "description": "Design an ablation experiment.",
        "instancePolicy": {"mode": "repeatable"},
        "requiredInputRefs": ["user:request"],
        "artifactTypes": ["ablation_plan"],
        "criterionRefs": ["ablation_validator"],
    },
    {
        "definitionId": "sdr:execution_check",
        "definitionVersion": 1,
        "pack": "sdr",
        "planningApplicability": {
            "scopeKind": "general",
            "scopeLabel": "SDR",
            "scopeDescription": "General research work",
            "whenToUse": "Use across domains when execution validation is needed.",
        },
        "stage": "execution_validation",
        "label": "Execution validation",
        "description": "Validate an experiment or evaluation.",
        "instancePolicy": {"mode": "repeatable"},
        "requiredInputRefs": ["user:request"],
        "artifactTypes": ["validation_report"],
        "criterionRefs": ["validation_validator"],
    },
]


class _Response:
    def __init__(self, body: dict) -> None:
        self.body = body

    def json(self) -> dict:
        return self.body

    def raise_for_status(self) -> None:
        return None


class _Transport:
    def __init__(self, body: dict) -> None:
        self.body = body
        self.calls = 0

    def post(self, endpoint: str, *, headers: dict, json: dict, timeout: float) -> _Response:
        self.calls += 1
        return _Response(self.body)


def _revision(stage: str = "solution_design") -> dict:
    return {
        "goal": "Compare GraphRAG retrieval strategies",
        "steps": [
            {
                "localKey": "user-step-ablation",
                "stageKey": stage,
                "objective": "Design an ablation experiment for retrieval components",
            }
        ],
        "constraints": ["Use evidence from 2024-2026"],
        "evidenceBoundaries": ["Do not claim completed experiments"],
        "requestedExternalActions": [],
        "materialSummary": "No supplied materials",
    }


def _semantic(*, definition_id: str = "sdr:ablation_design", constraints: list[str] | None = None) -> dict:
    return {
        "schemaVersion": "planner-semantic.v2",
        "goal": "Compare GraphRAG retrieval strategies",
        "understanding": {
            "userIntent": "Compare GraphRAG retrieval strategies",
            "materialSummary": "No supplied materials",
            "primaryResearchQuestions": ["Which GraphRAG retrieval strategy performs better under the requested evidence boundary?"],
            "auxiliaryResearchDimensions": ["Ablation variables and evaluation criteria"],
            "targetOutputs": ["ablation_plan"],
            "evidenceBoundaries": ["Do not claim completed experiments"],
            "missingInputs": [],
            "riskNotes": [],
        },
        "constraints": constraints if constraints is not None else ["Use evidence from 2024-2026"],
        "requestedExternalActions": [],
        "workItems": [
            {
                "localKey": "user-step-ablation",
                "objective": "Design an ablation experiment for retrieval components",
                "definitionId": definition_id,
                "inputRefs": ["user:request"],
                "expectedArtifactTypes": [
                    "ablation_plan" if definition_id == "sdr:ablation_design" else "validation_report"
                ],
                "targetOutputRefs": ["ablation_plan"],
                "dependsOnLocalKeys": [],
            }
        ],
    }


@pytest.fixture(autouse=True)
def _provider_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(planner_module.settings, "research_orchestration_v1", True)
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_LLM_PROVIDER", "huiyan_chat_completions")
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_LLM_MODEL", "planner-test-model")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")


def test_user_revision_uses_full_frozen_catalog_and_existing_semantic_compiler() -> None:
    prepared = prepare_planner_attempt(
        {"input": "Compare GraphRAG retrieval strategies", "sourcePage": "workbench"},
        None,
        1,
        definition_catalog=CATALOG,
        semantic_revision=_revision(),
    )
    assert "authoritative user edits" in prepared.user_prompt
    assert "sdr:variable_definition" in prepared.user_prompt
    assert "sdr:ablation_design" in prepared.user_prompt
    assert "sdr:execution_check" in prepared.user_prompt
    assert "preserve those arrays exactly" in prepared.user_prompt

    transport = _Transport(
        {
            "id": "planner-revision-call",
            "choices": [{"message": {"content": json.dumps(_semantic())}}],
        }
    )
    result = execute_planner_attempt(prepared, transport=transport)
    assert transport.calls == 1
    assert result.provider_call_ref == "planner-revision-call"
    assert result.proposal["instances"][0]["definitionId"] == "sdr:ablation_design"
    assert result.proposal["instances"][0]["expectedArtifactTypes"] == ["ablation_plan"]
    assert result.proposal["scopeBoundary"]["maxSubstageInstances"] == 1


def test_user_revision_fails_closed_on_wrong_stage_or_dropped_read_only_boundaries() -> None:
    wrong_stage = prepare_planner_attempt(
        {"input": "Compare GraphRAG retrieval strategies", "sourcePage": "workbench"},
        None,
        1,
        definition_catalog=CATALOG,
        semantic_revision=_revision(stage="execution_validation"),
    )
    with pytest.raises(ProviderContractError) as captured_stage:
        execute_planner_attempt(
            wrong_stage,
            transport=_Transport(
                {
                    "id": "wrong-stage",
                    "choices": [{"message": {"content": json.dumps(_semantic(definition_id="sdr:ablation_design"))}}],
                }
            ),
        )
    assert captured_stage.value.safe_error_code == "research_design_revision_semantic_mismatch"

    dropped_boundary = prepare_planner_attempt(
        {"input": "Compare GraphRAG retrieval strategies", "sourcePage": "workbench"},
        None,
        1,
        definition_catalog=CATALOG,
        semantic_revision=_revision(),
    )
    with pytest.raises(ProviderContractError) as captured_boundary:
        execute_planner_attempt(
            dropped_boundary,
            transport=_Transport(
                {
                    "id": "dropped-boundary",
                    "choices": [{"message": {"content": json.dumps(_semantic(constraints=[]))}}],
                }
            ),
        )
    assert captured_boundary.value.safe_error_code == "research_design_revision_semantic_mismatch"
