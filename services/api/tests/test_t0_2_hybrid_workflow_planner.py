from __future__ import annotations

import json

import httpx
import pytest

from app.execution.research_start_provider_adapter import ProviderContractError, ProviderTransportUncertainError
from app.modules.research_design_workflow_semantics import (
    WORKFLOW_SEMANTIC_MAX_TOTAL_CHARS,
    WorkflowProjectSemanticBriefV2,
)


class _Response:
    def __init__(self, body: dict):
        self._body = body

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        return None


class _Transport:
    def __init__(self, response: _Response | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def post(self, endpoint, *, headers, json, timeout):
        self.calls.append({"endpoint": endpoint, "json": json, "timeout": timeout})
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _catalog() -> list[dict]:
    return [
        {
            "definitionId": "wf:intent",
            "definitionVersion": 1,
            "pack": "workflow_test",
            "planningApplicability": {
                "scopeKind": "specialized",
                "scopeLabel": "Workflow test",
                "scopeDescription": "Test workflow research work",
                "whenToUse": "Use only inside the test workflow.",
            },
            "stage": "problem_modeling",
            "label": "Intent",
            "description": "Clarify the research goal.",
            "instancePolicy": {"mode": "repeatable"},
            "requiredInputRefs": ["user:request"],
            "artifactTypes": ["intent"],
            "criterionRefs": ["crit:intent"],
            "contractComplete": True,
        },
        {
            "definitionId": "wf:findings_synthesis",
            "definitionVersion": 1,
            "pack": "workflow_test",
            "planningApplicability": {
                "scopeKind": "specialized",
                "scopeLabel": "Workflow test",
                "scopeDescription": "Test workflow research work",
                "whenToUse": "Use only inside the test workflow.",
            },
            "stage": "result_synthesis",
            "label": "Findings",
            "description": "Synthesize the research findings.",
            "instancePolicy": {"mode": "repeatable"},
            "requiredInputRefs": ["intent"],
            "artifactTypes": ["findings"],
            "criterionRefs": ["crit:findings"],
            "contractComplete": True,
        },
        {
            "definitionId": "sdr:report_generation",
            "definitionVersion": 1,
            "pack": "sdr",
            "planningApplicability": {
                "scopeKind": "general",
                "scopeLabel": "SDR",
                "scopeDescription": "General research work",
                "whenToUse": "Use across domains for final report generation.",
            },
            "stage": "result_delivery",
            "label": "Report",
            "description": "Produce the final user-facing report.",
            "instancePolicy": {"mode": "repeatable"},
            "requiredInputRefs": ["findings"],
            "artifactTypes": ["research_report"],
            "criterionRefs": ["crit:report"],
            "contractComplete": True,
        },
    ]


def _workflow_context() -> dict:
    return {
        "schemaVersion": "workflow-planning-context.v1",
        "workflowId": "wf",
        "workflowVersion": 1,
        "workflowContentHash": "sha256:" + "a" * 64,
        "description": "Evidence-grounded research workflow.",
        "requiredNodes": [
            {"nodeKey": "intent", "definitionId": "wf:intent", "definitionVersion": 1, "displayOrder": 10},
            {"nodeKey": "findings", "definitionId": "wf:findings_synthesis", "definitionVersion": 1, "displayOrder": 30},
        ],
        "optionalNodes": [],
        "dependencies": [
            {
                "upstreamNodeKey": "intent",
                "downstreamNodeKey": "findings",
                "upstreamDefinitionId": "wf:intent",
                "downstreamDefinitionId": "wf:findings_synthesis",
                "dependencyType": "scientific_dependency",
            }
        ],
        "boundaryPolicy": {
            "allowedExternalActions": ["external_read"],
            "confirmationRequiredActions": ["external_read"],
            "prohibitedActions": ["external_write", "physical_action"],
        },
    }


def _prepare(monkeypatch: pytest.MonkeyPatch, query: str):
    from app.modules import research_start_planner as planner

    monkeypatch.setattr(planner.settings, "research_orchestration_v1", True)
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_PROVIDER", "huiyan_chat_completions")
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_LLM_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_LLM_TIMEOUT_SECONDS", "77")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    return planner, planner.prepare_planner_attempt(
        {"input": query, "sourcePage": "home"},
        {"reason": "workflow"},
        1,
        definition_catalog=_catalog(),
        workflow_planning_context=_workflow_context(),
    )


def _brief(
    *,
    goal: str,
    primary_questions: list[str],
    auxiliary_dimensions: list[str],
    outputs: list[str],
    work_contexts: list[str],
    constraints: list[str] | None = None,
) -> dict:
    return {
        "researchGoal": goal,
        "primaryResearchQuestions": primary_questions,
        "auxiliaryResearchDimensions": auxiliary_dimensions,
        "targetOutputs": outputs,
        "constraints": constraints or [],
        "scientificUnknowns": ["现有证据能否区分相关性与因果机制。"],
        "risksAndTradeoffs": ["不同研究条件可能限制结论的直接可比性。"],
        "workItemScientificContexts": work_contexts,
    }


def _execute(planner, prepared, brief: dict, *, call_id: str = "provider-semantic-1"):
    transport = _Transport(_Response({
        "id": call_id,
        "model": "gpt-5.6-terra",
        "choices": [{"message": {"content": json.dumps(brief, ensure_ascii=False)}}],
    }))
    return planner.execute_planner_attempt(prepared, transport=transport), transport


def _topology(proposal: dict) -> list[tuple[str, tuple[str, ...]]]:
    return [
        (
            row["definitionId"],
            tuple(
                dep["upstreamInstanceRef"]["substageInstanceId"]
                for dep in row.get("dependencies", [])
                if dep.get("upstreamInstanceRef")
            ),
        )
        for row in proposal["instances"]
    ]


def test_workflow_provider_schema_contains_only_scientific_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planner, prepared = _prepare(
        monkeypatch,
        "研究锂金属电池 SEI 形成机制中材料组成、界面反应与循环稳定性的关系。",
    )
    schema = prepared.provider_schema
    assert prepared.response_format == {"type": "json_object"}
    assert set(schema["properties"]) == {
        "researchGoal",
        "primaryResearchQuestions",
        "auxiliaryResearchDimensions",
        "targetOutputs",
        "constraints",
        "scientificUnknowns",
        "risksAndTradeoffs",
        "workItemScientificContexts",
    }
    serialized_schema = json.dumps(schema, ensure_ascii=False, sort_keys=True)
    serialized_request = json.dumps(prepared.payload, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        "nodeKey",
        "localKey",
        "definitionId",
        "definitionVersion",
        "workItems",
        "dependsOnLocalKeys",
        "artifactTypes",
        "evaluatorRef",
        "workflowId",
        "workflowVersion",
        "completionMode",
    ):
        assert forbidden not in serialized_schema
        assert forbidden not in serialized_request
    assert prepared.payload["max_tokens"] == 1400
    assert prepared.payload["thinking"] == {"type": "disabled"}
    assert prepared.payload["reasoning_effort"] == "none"
    assert prepared.timeout_seconds == 77.0

    # The formal Provider Schema keeps its scientific documentation. Projection
    # and provider-specific transport belong to the shared execution adapter.
    formal_schema = WorkflowProjectSemanticBriefV2.model_json_schema(by_alias=True)
    primary_description = formal_schema["properties"]["primaryResearchQuestions"]["description"]
    auxiliary_description = formal_schema["properties"]["auxiliaryResearchDimensions"]["description"]
    assert "one core decision" in primary_description
    assert "normally have exactly one primary question" in primary_description
    assert "do not independently widen" in auxiliary_description

    assert schema["required"] == formal_schema["required"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["researchGoal"]["minLength"] == 1
    assert schema["properties"]["researchGoal"]["maxLength"] == 450
    assert schema["properties"]["primaryResearchQuestions"]["minItems"] == 1
    assert schema["properties"]["primaryResearchQuestions"]["maxItems"] == 6
    assert schema["properties"]["workItemScientificContexts"]["minItems"] == 2
    assert schema["properties"]["workItemScientificContexts"]["maxItems"] == 2

    messages = prepared.payload["messages"]
    system_prompt = next(row["content"] for row in messages if row["role"] == "system")
    user_prompt = next(row["content"] for row in messages if row["role"] == "user")
    assert "outputSchema" not in json.loads(user_prompt)
    assert "normally return exactly ONE primary question" in system_prompt
    assert "do not reward apparent comprehensiveness" in system_prompt
    assert "Multiple primary questions are appropriate only when the user actually asks" in system_prompt

    result, transport = _execute(
        planner,
        prepared,
        _brief(
            goal="判断 SEI 形成机制中材料组成、界面反应与循环稳定性的关系。",
            primary_questions=["材料组成和界面反应如何共同影响循环稳定性？"],
            auxiliary_dimensions=["界面化学组成"],
            outputs=["当前证据支持的关系判断"],
            work_contexts=["界定关系判断的证据边界。", "综合相关证据并形成判断。"],
        ),
    )
    assert result.proposal["goal"]
    sent_payload = transport.calls[0]["json"]
    sent_messages = sent_payload["messages"]
    assert [row["role"] for row in sent_messages] == ["system", "user"]
    assert "outputSchema" not in json.loads(sent_messages[1]["content"])
    assert sent_payload["response_format"]["type"] == "json_schema"
    assert sent_payload["response_format"]["json_schema"]["strict"] is True
    transported = sent_payload["response_format"]["json_schema"]["schema"]
    assert transported["properties"]["workItemScientificContexts"]["minItems"] == 2
    assert transported["properties"]["workItemScientificContexts"]["maxItems"] == 2
    assert '"title"' not in json.dumps(transported, ensure_ascii=False)


def test_workflow_primary_question_contract_allows_true_multi_goal_requests() -> None:
    schema = WorkflowProjectSemanticBriefV2.model_json_schema(by_alias=True)
    primary = schema["properties"]["primaryResearchQuestions"]

    # The contract guides scientific classification; code must not mechanically
    # collapse a genuinely multi-goal request into one question.
    assert primary["minItems"] == 1
    assert primary["maxItems"] == 6


def test_single_material_scientific_dimension_is_valid_without_padding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planner, prepared = _prepare(monkeypatch, "确定某单一热处理温度是否改变目标相的形成窗口。")
    brief = _brief(
        goal="判断热处理温度变化是否改变目标相的形成窗口。",
        primary_questions=["热处理温度变化是否改变目标相的形成窗口？"],
        auxiliary_dimensions=[],
        outputs=["温度变化与目标相形成窗口关系的证据化判断"],
        work_contexts=["界定温度变化与目标相形成窗口这一主问题。", "综合与该主问题直接相关的证据。"],
    )
    result, transport = _execute(planner, prepared, brief)
    assert len(transport.calls) == 1
    assert result.proposal["goal"] == brief["researchGoal"]
    assert brief["primaryResearchQuestions"][0] in json.dumps(result.proposal, ensure_ascii=False)


def test_semantic_brief_has_a_total_character_budget() -> None:
    payload = _brief(
        goal="目标" + "甲" * 300,
        primary_questions=["主问题" + "乙" * 280],
        auxiliary_dimensions=["维度甲" + "丙" * 280, "维度乙" + "丁" * 280],
        outputs=["产出甲" + "戊" * 280, "产出乙" + "己" * 280],
        work_contexts=[
            "任务上下文甲" + "庚" * 280,
            "任务上下文乙" + "庚" * 280,
            "任务上下文丙" + "庚" * 280,
        ],
        constraints=["约束甲" + "辛" * 280],
    )
    payload["scientificUnknowns"] = ["未知甲" + "壬" * 280]
    payload["risksAndTradeoffs"] = ["风险甲" + "癸" * 280]
    assert sum(
        len(value)
        for value in payload.values()
        for value in ([value] if isinstance(value, str) else value)
    ) > WORKFLOW_SEMANTIC_MAX_TOTAL_CHARS
    with pytest.raises(ValueError, match="total character budget"):
        WorkflowProjectSemanticBriefV2.model_validate(payload)


def test_sei_semantics_enrich_content_without_changing_workflow_topology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = "研究锂金属电池 SEI 形成机制中材料组成、界面反应与循环稳定性的关系。"
    planner, prepared = _prepare(monkeypatch, query)
    brief = _brief(
        goal="阐明锂金属电池 SEI 中材料组成与界面反应如何共同影响循环稳定性。",
        primary_questions=["SEI 的材料组成与界面反应如何共同影响循环稳定性？"],
        auxiliary_dimensions=["SEI 无机/有机组成与空间分布", "循环条件与表征条件下的证据可比性"],
        outputs=["SEI 组成—界面反应—循环稳定性的证据化机制链", "关键证据缺口与适用边界"],
        work_contexts=["把主问题界定为组成—界面反应—循环稳定性的因果链。", "比较能够支持或限制该机制链的证据，并处理条件可比性。"],
    )
    result, transport = _execute(planner, prepared, brief)

    assert len(transport.calls) == 1
    assert result.provider_call_ref == "provider-semantic-1"
    assert result.proposal["goal"] == brief["researchGoal"]
    assert result.proposal["understanding"]["targetOutputs"] == brief["targetOutputs"]
    assert result.proposal["understanding"]["missingInputs"] == []
    assert any(note.startswith("待研究未知：") for note in result.proposal["understanding"]["riskNotes"])
    assert not any("尚未执行检索" in note for note in result.proposal["understanding"]["riskNotes"])
    assert [row["definitionId"] for row in result.proposal["instances"]] == [
        "wf:intent",
        "wf:findings_synthesis",
    ]
    understanding = result.proposal["understanding"]
    assert understanding["primaryResearchQuestions"] == brief["primaryResearchQuestions"]
    assert understanding["auxiliaryResearchDimensions"] == brief["auxiliaryResearchDimensions"]
    assert not any(
        dimension in understanding["targetOutputs"]
        for dimension in understanding["auxiliaryResearchDimensions"]
    )
    objectives = [row["objective"] for row in result.proposal["instances"]]
    assert len(set(objectives)) == len(objectives)
    assert all(brief["researchGoal"] not in objective for objective in objectives)
    assert not all(
        all(dimension in objective for dimension in brief["auxiliaryResearchDimensions"])
        for objective in objectives
    )


def test_new_task_changes_scientific_content_not_fixed_workflow_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planner_a, prepared_a = _prepare(
        monkeypatch,
        "研究锂金属电池 SEI 形成机制中材料组成、界面反应与循环稳定性的关系。",
    )
    result_a, transport_a = _execute(
        planner_a,
        prepared_a,
        _brief(
            goal="解释 SEI 组成与循环稳定性的机制关系。",
            primary_questions=["SEI 组成如何通过界面反应影响循环稳定性？"],
            auxiliary_dimensions=["测试条件下的证据可比性"],
            outputs=["SEI 机制证据链"],
            work_contexts=["界定 SEI 机制主问题。", "综合机制证据。"],
        ),
        call_id="provider-sei",
    )

    planner_b, prepared_b = _prepare(
        monkeypatch,
        "评估仓库级代码检索是否能提高复杂代码修改的一次成功率，并明确适用条件。",
    )
    result_b, transport_b = _execute(
        planner_b,
        prepared_b,
        _brief(
            goal="评估仓库级代码检索对复杂代码修改一次成功率的真实增益及适用边界。",
            primary_questions=["仓库级代码检索是否提高复杂代码修改的一次成功率？"],
            auxiliary_dimensions=["检索覆盖率与定位准确性", "修改复杂度", "基线与混杂因素控制"],
            outputs=["带基线对照的成功率评估", "适用条件与失败模式"],
            work_contexts=["界定一次成功率与对照基线。", "比较检索增强与基线在不同修改复杂度下的结果。"],
        ),
        call_id="provider-code",
    )

    assert len(transport_a.calls) == len(transport_b.calls) == 1
    assert _topology(result_a.proposal) == _topology(result_b.proposal)
    assert result_a.proposal["goal"] != result_b.proposal["goal"]
    assert "SEI" not in json.dumps(result_b.proposal, ensure_ascii=False)
    assert "仓库级代码检索" in json.dumps(result_b.proposal, ensure_ascii=False)
    understanding_b = result_b.proposal["understanding"]
    assert understanding_b["primaryResearchQuestions"] == [
        "仓库级代码检索是否提高复杂代码修改的一次成功率？"
    ]
    assert understanding_b["auxiliaryResearchDimensions"] == [
        "检索覆盖率与定位准确性",
        "修改复杂度",
        "基线与混杂因素控制",
    ]
    assert not any(
        dimension in understanding_b["targetOutputs"]
        for dimension in understanding_b["auxiliaryResearchDimensions"]
    )
    objectives_b = [row["objective"] for row in result_b.proposal["instances"]]
    assert not all(
        all(dimension in objective for dimension in understanding_b["auxiliaryResearchDimensions"])
        for objective in objectives_b
    )


def test_explicit_constraints_are_preserved_as_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planner, prepared = _prepare(
        monkeypatch,
        "比较三种方案，只使用近五年英文同行评议文献，不做实验。",
    )
    constraints = ["只使用近五年英文同行评议文献。", "本轮不执行实验。"]
    result, _ = _execute(
        planner,
        prepared,
        _brief(
            goal="比较三种方案在现有同行评议证据下的差异。",
            primary_questions=["三种方案在现有同行评议证据下有哪些可支持的差异？"],
            auxiliary_dimensions=["证据质量", "适用边界"],
            outputs=["三种方案的证据化比较"],
            work_contexts=["界定三种方案的比较问题。", "综合符合约束的比较证据。"],
            constraints=constraints,
        ),
    )
    assert result.proposal["constraints"] == constraints


def test_provider_failure_is_not_converted_into_generic_confirmable_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planner, prepared = _prepare(monkeypatch, "研究一个需要科研判断的问题。")
    transport = _Transport(error=httpx.ReadTimeout("slow semantic call"))

    with pytest.raises(ProviderTransportUncertainError):
        planner.execute_planner_attempt(prepared, transport=transport)

    assert len(transport.calls) == 1


def test_invalid_semantic_payload_is_not_converted_into_generic_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planner, prepared = _prepare(monkeypatch, "研究一个需要科研判断的问题。")
    transport = _Transport(_Response({
        "id": "provider-invalid-semantic",
        "model": "gpt-5.6-terra",
        "choices": [{"message": {"content": "not-json"}}],
    }))
    with pytest.raises(ProviderContractError):
        planner.execute_planner_attempt(prepared, transport=transport)
    assert len(transport.calls) == 1


def test_missing_semantic_provider_configuration_fails_before_draft_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules import research_start_planner as planner

    monkeypatch.setattr(planner.settings, "research_orchestration_v1", True)
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_PROVIDER", "huiyan_chat_completions")
    monkeypatch.delenv("LLM_PROVIDER_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER_BASE_URL", raising=False)

    with pytest.raises(planner.ResearchStartPlannerUnavailable):
        planner.prepare_planner_attempt(
            {"input": "研究一个需要科研判断的问题。", "sourcePage": "home"},
            {"reason": "workflow"},
            1,
            definition_catalog=_catalog(),
            workflow_planning_context=_workflow_context(),
        )
