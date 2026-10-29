from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from jsonschema import ValidationError, validate as validate_json_schema

from app.execution.opentrajectory_structured_runtime_guard import (
    RuntimeGuardRejected,
    apply_opentrajectory_runtime_guard,
)
from app.modules.research_execution_decision_service import _dependency_preflight_block
from app.models.research_orchestration import DependencyConditionType, SubstageRuntimeStatus


ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = ROOT / "skills" / "opentrajectory" / "research-lead-hypothesis-generation-skill"


RECORD_ID = "rr:" + "a" * 32
OTHER_RECORD_ID = "rr:" + "b" * 32
EDGE_ID = "me:" + "c" * 20


def _record(record_id: str = RECORD_ID) -> dict:
    return {
        "record_id": record_id,
        "source": {
            "candidate_id": "candidate-1",
            "anchor_id": "anchor-1",
            "title": "Source title",
            "doi": "",
            "arxiv_id": "",
            "provider": "fixture",
            "source_locator": "fixture:section",
            "section_name": "Results",
            "content_hash": "d" * 64,
            "source_type": "paper",
            "provenance": [],
        },
        "source_anchor_id": "anchor-1",
        "record_type": "qualitative_result",
        "research_object": "fixture research object",
        "condition_status": "not_applicable",
        "conditions": [],
        "result": {
            "statement": "The supplied source reports a bounded result.",
            "values": [],
            "mechanism": None,
            "comparison": None,
        },
        "information_identity": "direct_source_fact",
        "assertion_mode": "source_statement",
        "source_quote": "The supplied source reports a bounded result in this fixture.",
        "qualifiers": [],
        "uncertainty": None,
        "unknown_fields": [],
        "relevant_target_ids": [],
    }


def _mechanism_packet() -> dict:
    return {
        "mechanism_edges": [
            {
                "local_edge_key": "edge-1",
                "edge_id": EDGE_ID,
                "edge_status": "support",
                "source_node": {"node_type": "condition", "label": "condition A"},
                "relation": {
                    "predicate": "associated_with",
                    "description": "is associated with the bounded result",
                },
                "target_node": {"node_type": "performance", "label": "result B"},
                "evidence_ids": [RECORD_ID],
                "rationale": "The edge is grounded in the supplied ResearchRecord.",
                "confidence": 0.7,
            }
        ],
        "mechanism_chains": [],
        "conflicts": [],
        "gaps": [],
        "summary": {
            "input_evidence_item_count": 1,
            "mechanism_edge_count": 1,
            "mechanism_chain_count": 0,
            "edge_status_counts": {"support": 1, "conflict": 0, "gap": 0},
            "dropped_duplicate_edge_count": 0,
            "dropped_duplicate_chain_count": 0,
            "degraded": False,
        },
        "audit": {
            "skill_id": "mechanism_chain_synthesis",
            "traceability_mode": "evidence_id_guard",
            "guard_version": "p07d-v1",
            "input_fingerprint": "e" * 64,
            "allowed_edge_statuses": ["conflict", "gap", "support"],
            "allowed_predicates": ["associated_with"],
        },
        "synthesis_warnings": [],
    }


def _input(*, with_mechanism: bool) -> dict:
    payload = {
        "task_id": "task-final-synthesis",
        "research_context": {
            "original_user_query": "What does the supplied evidence support?",
            "research_object": "fixture research object",
            "target_metrics": ["fixture metric"],
            "boundary_conditions": ["fixture boundary"],
            "output_intent": "summary",
        },
        "research_records": [_record()],
    }
    if with_mechanism:
        payload["mechanism_packet"] = _mechanism_packet()
    return payload


def _draft(*, with_mechanism: bool) -> dict:
    mechanism_refs = [EDGE_ID] if with_mechanism else []
    return {
        "direct_answer": {
            "answer": "The supplied evidence supports only the bounded reported result.",
            "supporting_record_ids": [RECORD_ID],
            "caveats": ["Transferability is not established."],
            "confidence": 0.7,
        },
        "evidence_supported_conclusions": [
            {
                "local_conclusion_key": "conclusion-1",
                "conclusion_id": "",
                "claim_type": "evidence_supported_conclusion",
                "statement": "The bounded reported result is supported within the supplied source context.",
                "epistemic_status": "boundary_limited",
                "supporting_record_ids": [RECORD_ID],
                "supporting_mechanism_edge_ids": mechanism_refs,
                "evidence_basis": "The conclusion is directly grounded in the supplied ResearchRecord.",
                "scope_boundary": "Only the supplied source context is covered.",
                "uncertainties": ["Transferability is not established."],
                "confidence": 0.7,
            }
        ],
        "conflicts": [],
        "uncertainties": ["Transferability is not established."],
        "evidence_gaps": [
            {
                "gap": "No independent transferability evidence is supplied.",
                "related_record_ids": [RECORD_ID],
                "mechanism_context_edge_ids": [],
                "impact": "The result cannot be generalized beyond the supplied context.",
            }
        ],
        "research_leads": [
            {
                "local_lead_key": "lead-1",
                "research_lead_id": "",
                "claim_type": "research_lead",
                "lead_kind": "transferability_question",
                "title": "Assess transferability",
                "research_question": "Does the bounded relationship transfer under matched conditions?",
                "investigation_target": "Transferability beyond the supplied context.",
                "rationale": "The ResearchRecord supports the bounded result but not transferability.",
                "supporting_record_ids": [RECORD_ID],
                "mechanism_context_edge_ids": mechanism_refs,
                "boundary_conditions": ["matched conditions"],
                "scientific_significance": "Transferability determines whether the relationship is reusable.",
                "uncertainties": ["No independent transferability evidence is supplied."],
                "confidence": 0.5,
                "novelty_boundary": "research_direction_not_hypothesis_or_novelty_claim",
            }
        ],
        "hypotheses": [
            {
                "local_hypothesis_key": "hypothesis-1",
                "hypothesis_id": "",
                "claim_type": "unvalidated_hypothesis",
                "source_local_lead_key": "lead-1",
                "research_lead_id": "",
                "title": "Bounded transferability hypothesis",
                "statement": "Under matched conditions, changing the independent variable may shift the dependent outcome.",
                "independent_variable": "controlled variable A",
                "dependent_variable": "bounded outcome B",
                "expected_direction": "measurable change relative to the matched baseline",
                "boundary_conditions": ["matched conditions"],
                "supporting_record_ids": [RECORD_ID],
                "mechanism_context_edge_ids": mechanism_refs,
                "evidence_basis": "The supplied ResearchRecord motivates the hypothesis but does not validate it.",
                "mechanism_reasoning": (
                    "The optional mechanism edge supplies context for the proposed relationship."
                    if with_mechanism
                    else None
                ),
                "falsifiable_prediction": "A matched comparison will show a reproducible directional difference.",
                "validation_conditions": ["Use matched conditions and a predefined outcome measure."],
                "falsification_conditions": ["No reproducible directional difference is observed under matched conditions."],
                "scientific_significance": "The test distinguishes transferability from source-specific behavior.",
                "uncertainties": ["The expected direction is unvalidated."],
                "confidence": 0.4,
                "novelty_boundary": "derived_from_evidence_not_established_by_sources",
            }
        ],
        "summary": {},
        "audit": {},
        "generation_warnings": [],
    }


def _validate_final(value: dict) -> None:
    schema = yaml.safe_load((SKILL_ROOT / "output.schema.json").read_text(encoding="utf-8"))
    validate_json_schema(instance=value, schema=schema)


def test_final_synthesis_without_mechanism_is_legal_and_grounded() -> None:
    guarded, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="research_lead_hypothesis_generation",
        input_payload=_input(with_mechanism=False),
        output_payload=_draft(with_mechanism=False),
    )
    _validate_final(guarded)

    assert diagnostics["status"] == "passed"
    assert guarded["summary"]["mechanism_available"] is False
    assert guarded["evidence_supported_conclusions"][0]["supporting_record_ids"] == [RECORD_ID]
    assert guarded["hypotheses"][0]["claim_type"] == "unvalidated_hypothesis"
    assert guarded["hypotheses"][0]["validation_conditions"]
    assert guarded["hypotheses"][0]["falsification_conditions"]


def test_final_synthesis_with_mechanism_uses_it_only_as_grounded_enhancement() -> None:
    guarded, diagnostics = apply_opentrajectory_runtime_guard(
        skill_id="research_lead_hypothesis_generation",
        input_payload=_input(with_mechanism=True),
        output_payload=_draft(with_mechanism=True),
    )
    _validate_final(guarded)

    assert diagnostics["status"] == "passed"
    assert guarded["summary"]["mechanism_available"] is True
    assert guarded["summary"]["mechanism_edge_count"] == 1
    assert guarded["evidence_supported_conclusions"][0]["supporting_mechanism_edge_ids"] == [EDGE_ID]


def test_final_synthesis_rejects_unknown_record_reference() -> None:
    draft = _draft(with_mechanism=False)
    draft["evidence_supported_conclusions"][0]["supporting_record_ids"] = [OTHER_RECORD_ID]

    with pytest.raises(RuntimeGuardRejected):
        apply_opentrajectory_runtime_guard(
            skill_id="research_lead_hypothesis_generation",
            input_payload=_input(with_mechanism=False),
            output_payload=draft,
        )


def test_unknown_source_field_cannot_enter_final_conclusion_schema() -> None:
    draft = _draft(with_mechanism=False)
    draft["evidence_supported_conclusions"][0]["source_id"] = "invented-source"
    guarded, _ = apply_opentrajectory_runtime_guard(
        skill_id="research_lead_hypothesis_generation",
        input_payload=_input(with_mechanism=False),
        output_payload=draft,
    )
    schema = yaml.safe_load((SKILL_ROOT / "output.schema.json").read_text(encoding="utf-8"))

    with pytest.raises(ValidationError):
        validate_json_schema(instance=guarded, schema=schema)


def test_final_skill_input_contract_requires_research_records_and_not_mechanism() -> None:
    schema = yaml.safe_load((SKILL_ROOT / "input.schema.json").read_text(encoding="utf-8"))
    assert set(schema["required"]) == {"task_id", "research_context", "research_records"}
    assert schema["properties"]["research_records"]["minItems"] == 1
    assert "mechanism_packet" in schema["properties"]
    assert "mechanism_packet" not in schema["required"]

    validate_json_schema(instance=_input(with_mechanism=False), schema=schema)
    validate_json_schema(instance=_input(with_mechanism=True), schema=schema)






def test_workflow_and_registry_publish_one_self_validating_final_synthesis() -> None:
    workflow = yaml.safe_load(
        (ROOT / "schemas" / "workflows" / "opentrajectory_research_workflow.yaml").read_text(
            encoding="utf-8"
        )
    )
    node_keys = [row["nodeKey"] for row in workflow["nodes"]]
    assert node_keys[-1] == "findings_synthesis"
    assert "research_lead_generation" not in node_keys
    assert "hypothesis_update" not in node_keys
    final_upstreams = [
        row["upstreamNodeKey"]
        for row in workflow["dependencies"]
        if row["downstreamNodeKey"] == "findings_synthesis"
    ]
    assert final_upstreams == ["evidence_extraction"]

    registry = yaml.safe_load((ROOT / "skills" / "registry.yaml").read_text(encoding="utf-8"))
    by_id = {row["skill_id"]: row for row in registry["skills"]}
    final_skill = by_id["research_lead_hypothesis_generation"]
    assert final_skill["status"] == "ready"
    assert final_skill.get("enabled", True) is True
    assert final_skill["structured_output"] == {
        "contract_version": 1,
        "guard_version": "p06c-v1",
    }
    assert final_skill["review_policy"] == {
        "contract_version": 1,
        "mode": "schema_validated_only",
    }
    for legacy in (
        "ot_evidence_supported_findings_synthesis",
        "ot_research_lead_generation",
        "ot_falsifiable_hypothesis_generation",
    ):
        assert by_id[legacy]["enabled"] is False

def test_node9_is_declared_optional_while_final_remains_node8_dependent() -> None:
    workflow = yaml.safe_load(
        (ROOT / "schemas" / "workflows" / "opentrajectory_research_workflow.yaml").read_text(
            encoding="utf-8"
        )
    )
    nodes = {row["nodeKey"]: row for row in workflow["nodes"]}
    assert nodes["mechanism_explanation"]["definitionId"] == "opentrajectory:mechanism_explanation"
    assert nodes["mechanism_explanation"]["requirement"] == "optional"
    assert nodes["evidence_extraction"]["requirement"] == "required"
    assert nodes["findings_synthesis"]["requirement"] == "required"
    pairs = {
        (row["upstreamNodeKey"], row["downstreamNodeKey"])
        for row in workflow["dependencies"]
    }
    assert ("evidence_extraction", "mechanism_explanation") in pairs
    assert ("evidence_extraction", "findings_synthesis") in pairs
    assert ("mechanism_explanation", "findings_synthesis") not in pairs
