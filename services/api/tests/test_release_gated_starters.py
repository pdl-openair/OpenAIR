from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_public_starters_are_release_gated():
    res = client.get("/api/starters")
    assert res.status_code == 200
    starters = res.json()["starters"]
    ids = {s["starterId"] for s in starters}
    category_counts = {}
    for starter in starters:
        category_counts[starter["category"]] = category_counts.get(starter["category"], 0) + 1

    assert "exp_research_task_workflow_planning_v0_2" in ids
    assert "exp_literature_review_v0_2" not in ids
    assert category_counts == {
        "Project": 2,
        "Workflow": 4,
        "Agent": 13,
        "SkillCategory": 6,
    }
    assert all(s["visibility"] == "public_invited_trial" for s in starters)
    assert all(s["releaseDecision"] not in {"hidden", "internal_only"} for s in starters)
    for starter in starters:
        assert starter["description"]
        assert starter["usageGuide"]
        assert starter["usageGuide"]["suitableFor"]
        assert starter["usageGuide"]["input"]
        assert starter["usageGuide"]["output"]
        assert starter["usageGuide"]["boundary"]
        assert starter["readyLevel"]
        assert starter["outputList"]
        assert starter["boundaryCopy"]
        assert starter["requiredInputs"]
        assert starter["artifactOutputs"]
        assert starter["failureStates"]
        assert starter["sourceMode"]
        assert starter["trustBoundary"]

    verified = {s["starterId"] for s in starters if s["sampleStatus"] == "verified"}
    assert verified == {"project_research_direction_review_v0_3", "agent_work_plan_v0_3"}
    for starter in starters:
        if starter["starterId"] in verified:
            assert starter["verifiedSample"]
            assert starter["secondaryAction"] == "查看样例"
        else:
            assert starter["verifiedSample"] is None
            assert starter["sampleStatus"] == "pending"
            assert starter["sampleDisabledReason"]


def test_curated_research_agents_have_high_value_contracts():
    res = client.get("/api/starters")
    assert res.status_code == 200
    starters = {s["starterId"]: s for s in res.json()["starters"]}

    curated = [
        "agent_literature_research_v0_3",
        "agent_evidence_extraction_v0_3",
        "exp_evidence_mechanism_chain_v0_2",
        "agent_work_plan_v0_3",
        "agent_question_refinement_v0_3",
        "agent_gap_identification_v0_3",
        "agent_validation_design_v0_3",
        "agent_result_audit_v0_3",
    ]
    assert all(starter_id in starters for starter_id in curated)

    literature = starters["agent_literature_research_v0_3"]
    assert literature["title"] == "关键文献定位 智能体"
    assert {"检索词簇", "候选论文分层", "主要争议点"}.issubset(set(literature["outputList"]))
    assert "不声称已完成 live 检索" in literature["boundaryCopy"]
    assert "检索入口" in literature["usageGuide"]["suitableFor"]

    evidence = starters["agent_evidence_extraction_v0_3"]
    assert evidence["title"] == "论文证据抽取 智能体"
    assert {"claim-evidence 表", "直接/间接/推断标签", "缺失材料"}.issubset(set(evidence["outputList"]))
    assert "只有题录" in evidence["boundaryCopy"]
    assert evidence["creationMode"] == "requires_existing_inputs"

    mechanism = starters["exp_evidence_mechanism_chain_v0_2"]
    assert mechanism["title"] == "机制争议图谱 智能体"
    assert {"证据边", "推断边", "冲突边", "下一轮验证建议"}.issubset(set(mechanism["outputList"]))
    assert "inference/gap" in mechanism["boundaryCopy"]
    assert any("确定因果" in guardrail for guardrail in mechanism["guardrail"])

    plan = starters["agent_work_plan_v0_3"]
    assert plan["title"] == "研究推进计划 智能体"
    assert {"阶段计划", "产物定义", "停止条件"}.issubset(set(plan["outputList"]))
    assert plan["creationMode"] == "create_workflow_draft_then_confirm"
    assert "不自动启动检索" in plan["usageGuide"]["boundary"]
    question = starters["agent_question_refinement_v0_3"]
    assert question["title"] == "研究问题凝练 智能体"
    assert {"研究问题候选", "对象/变量边界", "可验证性检查"}.issubset(set(question["outputList"]))
    assert question["creationMode"] == "create_workflow_draft_then_confirm"

    gap = starters["agent_gap_identification_v0_3"]
    assert gap["title"] == "研究空缺识别 智能体"
    assert {"G-ID 空缺清单", "证据依据", "空缺类型", "置信度"}.issubset(set(gap["outputList"]))
    assert gap["creationMode"] == "requires_existing_inputs"
    assert "topic-only" in gap["boundaryCopy"]

    validation = starters["agent_validation_design_v0_3"]
    assert validation["title"] == "验证方案设计 智能体"
    assert {"验证方案草案", "变量表", "对照/基线", "消融矩阵", "分析计划"}.issubset(set(validation["outputList"]))
    assert validation["creationMode"] == "create_workflow_draft_then_confirm"

    audit = starters["agent_result_audit_v0_3"]
    assert audit["title"] == "结果可靠性审计 智能体"
    assert {"质量审计报告", "追溯完整性", "可复现性", "修复建议"}.issubset(set(audit["outputList"]))
    assert audit["creationMode"] == "requires_existing_inputs"


def test_hidden_starter_is_not_available():
    res = client.post("/api/starters/exp_literature_review_v0_2/use", json={})
    assert res.status_code == 404
