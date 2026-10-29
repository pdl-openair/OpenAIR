from __future__ import annotations

import json
from pathlib import Path

import yaml

from app.models.research_orchestration import PlanningSourceUsage
from app.models.workflow_definition import WorkflowDefinitionV2, freeze_workflow_definition
from app.modules.research_design_planner_assembly import build_adaptive_workflow_semantics
from app.modules.research_design_workflow_semantic_provider import (
    WorkflowSemanticProviderConfig,
    prepare_hybrid_workflow_planner_attempt,
)
from app.modules.research_design_workflow_semantics import WorkflowProjectSemanticBriefV4
from app.modules.research_design_workflow_semantics import parse_workflow_semantic_delta
from app.modules.workflow_planning import _planning_context


REPO_ROOT = Path(__file__).resolve().parents[6]
WORKFLOW_PATH = REPO_ROOT / "schemas" / "workflows" / "cs_paper_reproduction_workflow_v2.yaml"


class _Response:
    def __init__(self, body: dict):
        self._body = body

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        return None


class _Transport:
    def __init__(self, response: _Response):
        self.response = response

    def post(self, endpoint, *, headers, json, timeout):
        return self.response


def _context() -> dict:
    definition = WorkflowDefinitionV2.model_validate(
        yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    )
    frozen = freeze_workflow_definition(
        definition,
        registry_snapshot_ref={
            "schemaVersion": 1,
            "contentHash": "sha256:" + "a" * 64,
        },
    )
    return _planning_context(frozen)


def _catalog(context: dict) -> list[dict]:
    rows = [
        *context["coreResponsibilities"],
        *context["conditionalResponsibilities"],
    ]
    return [
        {
            "definitionId": row["definitionId"],
            "definitionVersion": row["definitionVersion"],
            "stage": "execution_validation",
            "label": row["nodeKey"],
            "description": f"完成 {row['nodeKey']} 对应的正式研究职责。",
            "instancePolicy": {"mode": "repeatable"},
            "requiredInputRefs": [],
            "artifactTypes": [],
            "criterionRefs": [],
            "contractComplete": False,
        }
        for row in rows
    ]


def _brief(*, diagnosis: bool = False, reuse_baseline_ref: str | None = None) -> WorkflowProjectSemanticBriefV4:
    responsibilities = [
        "明确论文目标结果、参考条件和有依据的达标判据。",
        "形成与复现目标对应的真实实验方案。",
        "核验代码、资源、环境和执行入口是否满足真实执行条件。",
        "按方案真实运行并取得可追溯实际结果。",
        "核验实际结果与论文参考结果的可比性并按预定判据判断。",
        "基于当前证据直接给出复现结论、限制和下一步。",
    ]
    deltas: list[dict] = []
    for index, text in enumerate(responsibilities, start=1):
        if index == 1 and reuse_baseline_ref is not None:
            deltas.append({
                "responsibilityChoice": "responsibility_1",
                "reuseCandidateKey": "planning_source_1",
            })
        else:
            deltas.append({
                "responsibilityChoice": f"responsibility_{index}",
                "workItemScientificContexts": [text],
            })
    if diagnosis:
        deltas.append({
            "responsibilityChoice": "responsibility_7",
            "workItemScientificContexts": ["Diagnose the material deviation and design necessary validation."],
        })
    planning_source_uses = []
    if reuse_baseline_ref is not None:
        planning_source_uses.append({
            "sourceChoice": "planning_source_1",
            "adoptedUnitNumbers": [1],
            "notAdoptedUnitNumbers": [],
            "revalidationUnitNumbers": [],
        })
    return WorkflowProjectSemanticBriefV4.model_validate(
        {
            "researchGoal": "复现指定论文的目标实验，并基于可比的真实结果判断是否复现成功。",
            "primaryResearchQuestions": ["本次真实实验是否达到预先确定的复现标准？"],
            "auxiliaryResearchDimensions": ["实验条件可比性", "执行证据完整性"],
            "targetOutputs": ["当前复现范围的达标结论、依据、限制与下一步"],
            "constraints": [],
            "scientificUnknowns": ["真实执行结果及其与参考值的差异尚待取得和判断。"],
            "risksAndTradeoffs": ["论文公开条件可能不足以支持完全等价复现。"],
            "responsibilityDeltas": deltas,
            "planningSourceUses": planning_source_uses,
        }
    )


def _proposal(*, diagnosis: bool = False, reuse_baseline_ref: str | None = None) -> dict:
    context = _context()
    raw_input = "帮我复现这篇论文，并告诉我是否复现成功。"
    if diagnosis:
        raw_input = "帮我复现这篇论文。已有真实运行结果与论文参考值出现关键偏差，请诊断后告诉我是否复现成功。"
    if reuse_baseline_ref is not None:
        raw_input = f"帮我复现这篇论文，并告诉我是否复现成功。已有正式基线结果 {reuse_baseline_ref}，若仍适用请复用。"
    planning_sources = []
    if reuse_baseline_ref is not None:
        planning_sources.append({
            "sourceChoice": "planning_source_1",
            "sourceKind": "research_package",
            "sourceId": "rp-formal-baseline",
            "sourceVersion": "1.0",
            "sourceArtifactRef": reuse_baseline_ref,
            "sourceContentHash": "sha256:" + "c" * 64,
            "name": "Formal reproduction baseline",
            "units": [{
                "unitNumber": 1,
                "sourcePath": "/reference_cases/0/results/0",
                "category": "reference_result",
                "content": "A traceable formal baseline criterion and result.",
                "supportingSources": [],
            }],
        })
    semantic = build_adaptive_workflow_semantics(
        raw_input=raw_input,
        definition_catalog=_catalog(context),
        workflow_planning_context=context,
        brief=_brief(diagnosis=diagnosis, reuse_baseline_ref=reuse_baseline_ref),
        planning_sources=planning_sources,
    )
    return semantic.model_dump(mode="json", by_alias=True)


def test_cs_core_responsibilities_remain_planned_without_forcing_diagnosis() -> None:
    proposal = _proposal()

    assert [row["definitionId"] for row in proposal["workItems"]] == [
        "cs_reproduction:reproduction_baseline_criteria",
        "cs_reproduction:reproduction_experiment_design",
        "cs_reproduction:execution_readiness_validation",
        "cs_reproduction:real_execution_result_acquisition",
        "cs_reproduction:result_comparability_judgment",
        "cs_reproduction:reproduction_conclusion_recommendation",
    ]
    coverage = {
        row["responsibilityKey"]: row for row in proposal["workflowResponsibilityCoverage"]
    }
    assert coverage["deviation_diagnosis_validation"]["coverageMode"] == "not_selected"


def test_cs_missing_and_extra_responsibility_contexts_are_harmless() -> None:
    context = _context()
    brief = parse_workflow_semantic_delta(
        {
            "researchGoal": "Reproduce the requested BERT-base result.",
            "targetOutputs": ["A current reproducibility conclusion."],
            "responsibilityDeltas": [
                {
                    "responsibilityChoice": "responsibility_1",
                    "workItemScientificContexts": ["Set the paper and official baseline."],
                },
                {
                    "responsibilityChoice": "responsibility_1",
                    "workItemScientificContexts": ["This duplicate is ignored."],
                },
                {
                    "responsibilityChoice": "not_a_registered_responsibility",
                    "workItemScientificContexts": ["This is ignored."],
                },
            ],
        }
    )

    semantic = build_adaptive_workflow_semantics(
        raw_input="Reproduce the requested BERT-base result.",
        definition_catalog=_catalog(context),
        workflow_planning_context=context,
        brief=brief,
    )

    coverage = {row.responsibility_key: row for row in semantic.workflow_responsibility_coverage}
    assert all(
        row.coverage_mode == "planned_work"
        for row in coverage.values()
        if row.requirement == "core"
    )
    baseline_keys = coverage["reproduction_baseline_criteria"].work_item_local_keys
    baselines = [row for row in semantic.work_items if row.local_key in baseline_keys]
    assert len(baselines) == 1
    assert any("Set the paper and official baseline." in row.objective for row in baselines)
    assert any("This duplicate is ignored." in row.objective for row in baselines)
    assert all("This is ignored." not in row.objective for row in semantic.work_items)
    assert any(
        "not_a_registered_responsibility" in note and "This is ignored." in note
        for note in semantic.understanding.risk_notes
    )


def test_cs_conditional_diagnosis_enters_only_when_condition_is_established() -> None:
    proposal = _proposal(diagnosis=True)

    coverage = {
        row["responsibilityKey"]: row for row in proposal["workflowResponsibilityCoverage"]
    }
    assert coverage["deviation_diagnosis_validation"]["coverageMode"] == "planned_work"
    conclusion = next(
        row
        for row in proposal["workItems"]
        if row["definitionId"] == "cs_reproduction:reproduction_conclusion_recommendation"
    )
    diagnosis = next(
        row
        for row in proposal["workItems"]
        if row["definitionId"] == "cs_reproduction:deviation_diagnosis_validation"
    )
    assert diagnosis["localKey"] in conclusion["dependsOnLocalKeys"]


def test_cs_explicit_formal_baseline_can_be_reused_without_claiming_reexecution() -> None:
    reuse_ref = "artifact:formal-baseline-20260916"
    proposal = _proposal(reuse_baseline_ref=reuse_ref)

    coverage = {
        row["responsibilityKey"]: row for row in proposal["workflowResponsibilityCoverage"]
    }
    assert coverage["reproduction_baseline_criteria"]["coverageMode"] == "reused_result"
    assert coverage["reproduction_baseline_criteria"]["workItemLocalKeys"] == []
    assert coverage["reproduction_conclusion_recommendation"]["coverageMode"] == "planned_work"
    assert not any(
        row["definitionId"] == "cs_reproduction:reproduction_baseline_criteria"
        for row in proposal["workItems"]
    )
    experiment = next(
        row
        for row in proposal["workItems"]
        if row["definitionId"] == "cs_reproduction:reproduction_experiment_design"
    )
    assert reuse_ref in experiment["inputRefs"]


def test_cs_planning_source_usage_preserves_provenance_and_workflow_authority() -> None:
    context = _context()
    planning_sources = [{
        "sourceChoice": "planning_source_1",
        "sourceKind": "research_package",
        "sourceId": "rp-reproduction-method",
        "sourceVersion": "2.0",
        "sourceArtifactRef": "rp_reproduction_exact_v2",
        "sourceContentHash": "sha256:" + "b" * 64,
        "name": "Reusable reproduction method",
        "units": [
            {
                "unitNumber": 1,
                "sourcePath": "/method/logic/0",
                "category": "method_logic",
                "content": "Preserve baseline-before-execution comparison logic.",
                "supportingSources": [],
            },
            {
                "unitNumber": 2,
                "sourcePath": "/method/obsolete/0",
                "category": "method_logic",
                "content": "An obsolete alternative that is not used by this plan.",
                "supportingSources": [],
            },
            {
                "unitNumber": 3,
                "sourcePath": "/resources/0",
                "category": "resource_reference",
                "content": '{"id":"repo","ref":"https://example.test/repo","role":"code"}',
                "supportingSources": [],
            },
        ],
    }]
    brief_payload = _brief().model_dump(mode="json", by_alias=True)
    brief_payload["planningSourceUses"] = [{
        "sourceChoice": "planning_source_1",
        "adoptedUnitNumbers": [1],
        "notAdoptedUnitNumbers": [2],
        "revalidationUnitNumbers": [3],
    }]
    prepared = prepare_hybrid_workflow_planner_attempt(
        raw_input="Reproduce the paper under the current formal Workflow.",
        ordinal=1,
        definition_catalog=_catalog(context),
        workflow_planning_context=context,
        provider_config=WorkflowSemanticProviderConfig(
            provider="huiyan_chat_completions",
            model="test-model",
            endpoint="https://provider.test",
            timeout_seconds=77,
        ),
        planning_sources=planning_sources,
    )
    transport = _Transport(_Response({
        "id": "provider-cs-source-aware",
        "model": "test-model",
        "choices": [{"message": {"content": json.dumps(brief_payload)}}],
    }))

    result = prepared.execute(transport=transport)

    assert len(result.planning_source_usages) == 1
    usage = result.planning_source_usages[0]
    assert isinstance(usage, PlanningSourceUsage)
    assert usage.model_dump(mode="json", by_alias=True) == {
        "sourceKind": "research_package",
        "sourceId": "rp-reproduction-method",
        "sourceVersion": "2.0",
        "sourceArtifactRef": "rp_reproduction_exact_v2",
        "sourceContentHash": "sha256:" + "b" * 64,
        "adoptedSourcePaths": ["/method/logic/0"],
        "notAdoptedSourcePaths": ["/method/obsolete/0"],
        "revalidationSourcePaths": ["/resources/0"],
    }
    assert [row["definitionId"] for row in result.proposal["instances"]] == [
        row["definitionId"] for row in context["coreResponsibilities"]
    ]
