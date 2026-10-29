from pathlib import Path

import pytest
import yaml
from app.models.research_orchestration import ResearchUnderstanding
from app.models.workflow_definition import WorkflowDefinitionV2, freeze_workflow_definition
from app.modules import research_start_planner as planner
from app.modules.provider_registry import ModelBinding
from app.modules.research_design_service import ResearchUnderstandingProposal
from app.modules.workflow_planning import _planning_context

ROOT = Path(__file__).resolve().parents[6]
WORKFLOWS = (
    None,
    "opentrajectory_research_workflow_v4.yaml",
    "pvd_process_optimization_workflow_v3.yaml",
    "cs_paper_reproduction_workflow_v7.yaml",
)


def _context(filename):
    if filename is None:
        return None
    definition = WorkflowDefinitionV2.model_validate(
        yaml.safe_load((ROOT / "schemas/workflows" / filename).read_text(encoding="utf-8"))
    )
    frozen = freeze_workflow_definition(
        definition,
        registry_snapshot_ref={"schemaVersion": 1, "contentHash": "sha256:" + "a" * 64},
    )
    return _planning_context(frozen)


def _catalog(context):
    responsibilities = (
        [*context["coreResponsibilities"], *context["conditionalResponsibilities"]]
        if context else [{"definitionId": "general:problem", "definitionVersion": 1}]
    )
    return [{
        **row,
        "pack": "general",
        "stage": "problem_modeling",
        "label": row["definitionId"],
        "description": "明确当前研究目标及证据边界。",
        "planningApplicability": {
            "scopeKind": "general", "scopeLabel": "科研目标建模",
        },
        "instancePolicy": {"mode": "repeatable"},
        "contractComplete": False,
    } for row in responsibilities]


@pytest.mark.parametrize("filename", WORKFLOWS)
def test_home_input_reaches_strict_planner_receiver(monkeypatch, filename):
    provider = "huiyan_chat_completions"
    monkeypatch.setattr(planner.settings, "research_orchestration_v1", True)
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_PROVIDER", provider)
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.invalid")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_BASE_URL", "https://provider.invalid")
    monkeypatch.setattr(
        planner, "resolve_model_binding",
        lambda *args, **kwargs: ModelBinding(
            "planner", provider, "https://provider.invalid", "test-model",
        ),
    )
    context = _context(filename)
    query = "核查一种科研方法的证据、适用条件与尚待验证的问题。"
    prepared = planner.prepare_planner_attempt(
        {"input": query, "sourcePage": "home", "selectedHint": None},
        None,
        1,
        definition_catalog=_catalog(context),
        workflow_planning_context=context,
    )
    assert prepared.provider == provider
    assert prepared.ordinal == 1
    semantic_attempt = getattr(prepared, "semantic_attempt", prepared)
    assert query in semantic_attempt.user_prompt
    assert prepared.provider_schema


def test_compiled_understanding_round_trips_without_renaming_planning_rationale():
    payload = {
        "researchType": None,
        "userIntent": "Evaluate a research method",
        "materialSummary": None,
        "primaryResearchQuestions": ["What does current evidence support?"],
        "auxiliaryResearchDimensions": [],
        "targetOutputs": ["Evidence-grounded conclusion"],
        "evidenceBoundaries": [],
        "missingInputs": [],
        "riskNotes": [],
        "planningRationale": "Compare evidence before drawing conclusions.",
    }
    proposal = ResearchUnderstandingProposal.model_validate(payload)
    formal = ResearchUnderstanding.model_validate(proposal.model_dump(by_alias=True))
    assert formal.model_dump(by_alias=True) == payload
