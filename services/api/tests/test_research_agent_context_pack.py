from app.modules.research_agent_context_pack import (
    ResearchAgentContextPack,
    build_research_agent_context_pack,
    research_agent_context_pack_json_schema,
)


def test_research_agent_context_pack_schema_exports_issue_required_fields() -> None:
    schema = research_agent_context_pack_json_schema()

    required = set(schema["required"])
    assert {
        "subgoal",
        "targetAgent",
        "sdrStage",
        "substage",
        "allowedMaterialIds",
        "selectedExcerpts",
        "knownGaps",
        "forbiddenOutputs",
        "outputContract",
        "budget",
        "requiredValidationChecks",
        "materialReadinessReport",
    } <= required






def test_context_pack_preserves_material_order_without_keyword_ranking() -> None:
    result = build_research_agent_context_pack(
        target_agent="research_gap_identification",
        user_input="identify the evidence gap",
        materials=[
            {
                "materialId": "M1",
                "locator": "source-1",
                "excerptType": "source_table_row",
                "text": "first material in the caller's bounded input",
            },
            {
                "materialId": "M2",
                "locator": "source-2",
                "excerptType": "source_table_row",
                "text": "gap review evidence negative benchmark validation unclear",
            },
        ],
        task_id="order-contract",
        top_k=1,
    )

    assert result.status == "ready"
    assert result.contextPack is not None
    assert result.contextPack.allowedMaterialIds == ["M1"]
    assert [row.materialId for row in result.contextPack.selectedExcerpts] == ["M1"]


def test_context_pack_carries_upstream_relevance_without_inventing_it() -> None:
    result = build_research_agent_context_pack(
        target_agent="research_gap_identification",
        user_input="review these materials",
        materials=[
            {
                "materialId": "M1",
                "locator": "source-1",
                "excerptType": "source_table_row",
                "text": "first excerpt",
            },
            {
                "materialId": "M2",
                "locator": "source-2",
                "excerptType": "source_table_row",
                "text": "second excerpt",
                "relevanceScore": 0.82,
            },
        ],
        task_id="relevance-carry-contract",
        top_k=2,
    )

    assert result.contextPack is not None
    scores = [row.relevanceScore for row in result.contextPack.selectedExcerpts]
    assert scores == [None, 0.82]


def test_validation_design_readiness_does_not_parse_user_language() -> None:
    result = build_research_agent_context_pack(
        target_agent="validation_design",
        user_input="run the experiment and tell me whether it works",
        materials=[],
        task_id="validation-design-contract",
    )

    assert result.status == "ready"
    assert result.contextPack is not None
    assert result.materialReadinessReport.status == "design_only"
    assert result.materialReadinessReport.failClosed is False
    assert result.materialReadinessReport.missingMaterials == []


def test_result_audit_uses_explicit_material_types_for_hard_input_gate() -> None:
    insufficient = build_research_agent_context_pack(
        target_agent="result_reliability_audit",
        user_input="audit this result",
        materials=[
            {
                "materialId": "M1",
                "locator": "claim-1",
                "excerptType": "claim",
                "text": "reported result claim",
            }
        ],
        task_id="audit-structural-contract",
    )
    assert insufficient.status == "fail_closed"

    ready = build_research_agent_context_pack(
        target_agent="result_reliability_audit",
        user_input="audit this result",
        materials=[
            {
                "materialId": "M1",
                "locator": "claim-1",
                "excerptType": "claim",
                "text": "reported result claim",
            },
            {
                "materialId": "M2",
                "locator": "run-1",
                "excerptType": "run_log",
                "text": "structured run record",
            },
        ],
        task_id="audit-structural-contract-ready",
    )
    assert ready.status == "ready"
    assert ready.contextPack is not None
