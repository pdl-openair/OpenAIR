from __future__ import annotations

from copy import deepcopy

import pytest

from app.modules.research_design_compiler import (
    ResearchDesignCompilerError,
    compile_research_design_proposal,
)
from app.modules.research_start_planner import (
    _PlannerSemanticProposalError,
    _apply_focused_semantic_repair,
    _focused_semantic_repair_schema,
    _validate_semantic_payload,
)


def _catalog(*, singleton: bool = False) -> list[dict]:
    return [
        {
            "definitionId": "sdr:frame",
            "definitionVersion": 1,
            "stage": "problem_modeling",
            "label": "Frame",
            "description": "Frame the research question.",
            "instancePolicy": {"mode": "singleton_collection" if singleton else "repeatable"},
            "requiredInputRefs": ["user:request"],
            "artifactTypes": ["problem_statement"],
            "criterionRefs": ["problem_statement_validator"],
        },
        {
            "definitionId": "sdr:synthesize",
            "definitionVersion": 1,
            "stage": "solution_design",
            "label": "Synthesize",
            "description": "Synthesize evidence.",
            "instancePolicy": {"mode": "repeatable"},
            "requiredInputRefs": ["user:request"],
            "artifactTypes": ["evidence_summary"],
            "criterionRefs": ["evidence_summary_validator"],
        },
    ]


def _payload() -> dict:
    return {
        "schemaVersion": "planner-semantic.v2",
        "goal": "Produce a bounded research answer.",
        "understanding": {
            "userIntent": "Produce a bounded research answer.",
            "materialSummary": None,
            "primaryResearchQuestions": ["What bounded research question is supported by the evidence?"],
            "auxiliaryResearchDimensions": [],
            "targetOutputs": ["Question", "Evidence summary"],
            "evidenceBoundaries": ["Do not invent sources."],
            "missingInputs": [],
            "riskNotes": [],
        },
        "constraints": [],
        "requestedExternalActions": [],
        "workItems": [
            {
                "localKey": "frame",
                "objective": "Frame the question.",
                "definitionId": "sdr:frame",
                "inputRefs": ["user:request"],
                "expectedArtifactTypes": ["problem_statement"],
                "targetOutputRefs": ["Question"],
                "dependsOnLocalKeys": [],
            },
            {
                "localKey": "synthesize",
                "objective": "Synthesize the evidence.",
                "definitionId": "sdr:synthesize",
                "inputRefs": ["user:request"],
                "expectedArtifactTypes": ["evidence_summary"],
                "targetOutputRefs": ["Evidence summary"],
                "dependsOnLocalKeys": ["frame"],
            },
        ],
    }


def _failure(payload: dict, *, catalog: list[dict] | None = None) -> _PlannerSemanticProposalError:
    with pytest.raises(_PlannerSemanticProposalError) as captured:
        _validate_semantic_payload(
            payload,
            raw_input="Organize a research-start draft for battery evidence.",
            definition_catalog=catalog or _catalog(),
        )
    return captured.value


def test_duplicate_local_key_enters_one_indexed_repair_and_succeeds() -> None:
    payload = _payload()
    payload["workItems"][1]["localKey"] = "frame"
    payload["workItems"][1]["dependsOnLocalKeys"] = []

    failure = _failure(payload)
    context = failure.safe_repair_context
    assert context["graphIssues"][0]["issueKind"] == "duplicate_local_key"
    assert context["graphIssues"][0]["workItems"] == [
        {"index": 0, "localKey": "frame"},
        {"index": 1, "localKey": "frame"},
    ]
    schema = _focused_semantic_repair_schema(context)
    assert schema["properties"]["workItemRepairs"]["minItems"] == 2
    assert schema["properties"]["workItemRepairs"]["maxItems"] == 2
    assert "workItemIndex" in schema["properties"]["workItemRepairs"]["items"]["properties"]

    repaired = _apply_focused_semantic_repair(
        context,
        {
            "workItemRepairs": [
                {"workItemIndex": 0, "localKey": "frame"},
                {"workItemIndex": 1, "localKey": "synthesize"},
            ]
        },
    )
    assert _validate_semantic_payload(
        repaired,
        raw_input="Organize a research-start draft for battery evidence.",
        definition_catalog=_catalog(),
    ).work_items[1].local_key == "synthesize"


@pytest.mark.parametrize(
    ("mutate", "issue_kind", "repair"),
    [
        (
            lambda payload: payload["workItems"][1].update(dependsOnLocalKeys=["missing"]),
            "unknown_dependency",
            {"workItemRepairs": [{"workItemIndex": 1, "dependsOnLocalKeys": ["frame"]}]},
        ),
        (
            lambda payload: payload["workItems"][1].update(dependsOnLocalKeys=["synthesize"]),
            "self_dependency",
            {"workItemRepairs": [{"workItemIndex": 1, "dependsOnLocalKeys": ["frame"]}]},
        ),
        (
            lambda payload: payload["workItems"][0].update(dependsOnLocalKeys=["synthesize"]),
            "dependency_cycle",
            {
                "workItemRepairs": [
                    {"workItemIndex": 0, "dependsOnLocalKeys": []},
                    {"workItemIndex": 1, "dependsOnLocalKeys": ["frame"]},
                ]
            },
        ),
        (
            lambda payload: payload["workItems"][1].update(targetOutputRefs=["Invented output"]),
            "invalid_target_output_ref",
            {
                "workItemRepairs": [
                    {"workItemIndex": 0, "targetOutputRefs": ["Question"]},
                    {"workItemIndex": 1, "targetOutputRefs": ["Evidence summary"]},
                ]
            },
        ),
        (
            lambda payload: payload["workItems"][1].update(targetOutputRefs=[]),
            "unowned_target_output",
            {
                "workItemRepairs": [
                    {"workItemIndex": 0, "targetOutputRefs": ["Question"]},
                    {"workItemIndex": 1, "targetOutputRefs": ["Evidence summary"]},
                ]
            },
        ),
    ],
)
def test_common_graph_issue_is_structured_and_full_repair_revalidates(
    mutate,
    issue_kind: str,
    repair: dict,
) -> None:
    payload = _payload()
    mutate(payload)
    failure = _failure(payload)
    context = failure.safe_repair_context

    assert any(issue["issueKind"] == issue_kind for issue in context["graphIssues"])
    repaired = _apply_focused_semantic_repair(context, repair)
    validated = _validate_semantic_payload(
        repaired,
        raw_input="Organize a research-start draft for battery evidence.",
        definition_catalog=_catalog(),
    )
    assert validated.work_items


def test_repair_must_cover_every_affected_work_item() -> None:
    payload = _payload()
    payload["workItems"][0]["dependsOnLocalKeys"] = ["synthesize"]
    context = _failure(payload).safe_repair_context

    with pytest.raises(_PlannerSemanticProposalError) as captured:
        _apply_focused_semantic_repair(
            context,
            {"workItemRepairs": [{"workItemIndex": 0, "dependsOnLocalKeys": []}]},
        )

    assert captured.value.safe_error_code == "planner_semantic_repair_invalid"


def test_repair_that_introduces_a_new_cycle_fails_full_validation() -> None:
    payload = _payload()
    payload["workItems"][1]["dependsOnLocalKeys"] = ["missing"]
    context = _failure(payload).safe_repair_context
    repaired = _apply_focused_semantic_repair(
        context,
        {"workItemRepairs": [{"workItemIndex": 1, "dependsOnLocalKeys": ["synthesize"]}]},
    )

    second = _failure(repaired)
    assert any(issue["issueKind"] == "self_dependency" for issue in second.safe_repair_context["graphIssues"])


def test_singleton_definition_conflict_is_focused_and_covers_all_items() -> None:
    payload = _payload()
    payload["workItems"][1]["definitionId"] = "sdr:frame"
    semantic = _validate_semantic_payload(
        payload,
        raw_input="Organize a research-start draft for battery evidence.",
        definition_catalog=_catalog(singleton=True),
    )

    with pytest.raises(ResearchDesignCompilerError) as captured:
        compile_research_design_proposal(
            semantic,
            definition_catalog=_catalog(singleton=True),
            raw_input="Produce a bounded research answer.",
        )

    context = captured.value.safe_repair_context
    assert context["graphIssues"][0]["issueKind"] == "singleton_definition_conflict"
    assert [row["index"] for row in context["graphIssues"][0]["workItems"]] == [0, 1]
    schema = _focused_semantic_repair_schema(context)
    assert schema["properties"]["workItemRepairs"]["minItems"] == 2


def test_graph_repair_schema_covers_more_than_bounded_diagnostic_path_limit() -> None:
    payload = _payload()
    template = deepcopy(payload["workItems"][0])
    payload["workItems"] = []
    for index in range(20):
        row = deepcopy(template)
        row["localKey"] = f"item-{index}"
        row["objective"] = f"Handle bounded item {index}."
        row["targetOutputRefs"] = []
        row["dependsOnLocalKeys"] = []
        payload["workItems"].append(row)
    payload["understanding"]["targetOutputs"] = ["Question"]

    context = _failure(payload).safe_repair_context
    assert len(context["fieldPaths"]) == 16
    schema = _focused_semantic_repair_schema(context)
    repair_array = schema["properties"]["workItemRepairs"]

    assert repair_array["minItems"] == 20
    assert repair_array["maxItems"] == 20
    assert repair_array["items"]["properties"]["workItemIndex"]["enum"] == list(range(20))
