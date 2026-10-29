from __future__ import annotations

from app.execution.workbench_collaboration_agent import (
    _research_design_brief,
    build_collaboration_system_prompt,
    parse_collaboration_action,
    reply_for_plan_regeneration,
    should_propose_plan_regeneration,
    user_requests_plan_regeneration,
)


def test_research_design_brief_marks_empty_shell_incomplete() -> None:
    brief = _research_design_brief(
        {
            "status": "awaiting_confirmation",
            "creationStatus": "awaiting_confirmation",
            "activeResearchDesignRef": None,
            "pendingResearchDesignRef": None,
            "creationPlan": None,
            "researchDesignVersions": [],
            "progressProjection": {
                "taskProgress": {
                    "status": "external_result_unknown",
                    "awaitingConfirmationRefs": [],
                }
            },
        }
    )
    assert brief["hasResearchDesign"] is False
    assert brief["awaitingConfirmationCount"] == 0
    assert brief["userFacingSituation"] == "research_design_incomplete"


def test_collaboration_prompt_forbids_false_confirmation_gate_without_design() -> None:
    prompt = build_collaboration_system_prompt(
        task={
            "title": "复现 SimCSE",
            "goalSummary": "复现 STS 结果",
            "status": "awaiting_confirmation",
            "creationStatus": "awaiting_confirmation",
            "activeResearchDesignRef": None,
            "creationPlan": None,
            "researchDesignVersions": [],
            "progressProjection": {
                "taskProgress": {
                    "status": "external_result_unknown",
                    "awaitingConfirmationRefs": [],
                }
            },
        },
        actions_snapshot={"allowedActions": [], "items": [], "executionStopped": False},
    )
    assert "research_design_incomplete" in prompt
    assert "不得告诉用户去确认方案或核查结果" in prompt
    assert "方案还未形成" in prompt
    assert "regenerate_research_plan" in prompt
    assert "不要说目前不能重试" in prompt


_INCOMPLETE_TASK = {
    "status": "failed",
    "creationStatus": "planning",
    "activeResearchDesignRef": None,
    "pendingResearchDesignRef": None,
    "creationPlan": None,
    "researchDesignVersions": [],
    "progressProjection": {"taskProgress": {"status": "failed", "awaitingConfirmationRefs": []}},
}


def test_retry_without_a_plan_proposes_regeneration_not_execution_retry() -> None:
    assert user_requests_plan_regeneration("你能重试吗")
    assert not user_requests_plan_regeneration("先不要重新生成")
    assert should_propose_plan_regeneration("你能重试吗", _INCOMPLETE_TASK, {"allowedActions": []})
    assert not should_propose_plan_regeneration(
        "你能重试吗",
        _INCOMPLETE_TASK,
        {"allowedActions": ["explicit_retry"], "items": []},
    )
    visible, action = parse_collaboration_action(
        '好的。\n```openair-action\n{"type":"regenerate_research_plan"}\n```'
    )
    assert visible == "好的。"
    assert action == {"type": "regenerate_research_plan"}
    assert "选定的方式" in reply_for_plan_regeneration({"applied": True, "reason": "queued"})
    assert "不能确认" in reply_for_plan_regeneration(
        {"applied": False, "reason": "external_result_unknown"}
    )
