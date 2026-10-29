from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError


REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_REL = "schemas/agents/artifacts/research_question_refinement_artifact_v1.schema.json"
SCHEMA_PATH = REPO_ROOT / SCHEMA_REL
REFERENCE_CONSTRAINTS_SCHEMA_REL = "schemas/agents/artifact-reference-constraints-v1.schema.json"
REFERENCE_CONSTRAINTS_SCHEMA_PATH = REPO_ROOT / REFERENCE_CONSTRAINTS_SCHEMA_REL
REGISTRY_PATH = REPO_ROOT / "schemas" / "agents" / "agent-registry.yaml"

SIX_BODY_FIELDS = (
    "question_candidates",
    "scope_boundary",
    "assumptions",
    "verification_readiness",
    "next_evidence_needs",
    "handoff",
)


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _load_reference_constraints_schema() -> dict[str, Any]:
    return json.loads(REFERENCE_CONSTRAINTS_SCHEMA_PATH.read_text(encoding="utf-8"))


def _resolve_pointer_tokens(schema: dict[str, Any], pointer: str) -> list[dict[str, Any]]:
    """Resolve the declared RFC 6901 pointer plus the v1 array wildcard syntax."""
    def dereference(node: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in node:
            reference = node["$ref"]
            assert isinstance(reference, str) and reference.startswith("#/")
            resolved: Any = schema
            for part in reference[2:].split("/"):
                resolved = resolved[part.replace("~1", "/").replace("~0", "~")]
            assert isinstance(resolved, dict)
            node = resolved
        return node

    assert pointer.startswith("/")
    nodes = [schema]
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        next_nodes: list[dict[str, Any]] = []
        for node in nodes:
            node = dereference(node)
            if token == "*":
                assert node.get("type") == "array"
                item = node.get("items")
                assert isinstance(item, dict)
                next_nodes.append(item)
                continue
            properties = node.get("properties")
            assert isinstance(properties, dict)
            child = properties.get(token)
            assert isinstance(child, dict)
            next_nodes.append(child)
        nodes = next_nodes
    return [dereference(node) for node in nodes]


def _validator() -> Draft202012Validator:
    schema = _load_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _valid_body() -> dict[str, Any]:
    return {
        "question_candidates": [
            {
                "candidate_id": "cand_alpha",
                "candidate_status": "candidate_unverified",
                "research_question": "Does treatment A change outcome B relative to control C in setting S?",
                "candidate_rationale": "Narrows the supplied goal keywords into a bounded comparison without asserting an answer.",
                "research_object": "treatment A",
                "outcome_or_decision": "change in outcome B",
                "boundary_ids": ["bound_in_object"],
                "assumption_ids": ["assume_goal_scope"],
                "evidence_need_ids": ["need_lit_seeds"],
            },
            {
                "candidate_id": "cand_beta",
                "candidate_status": "candidate_unverified",
                "research_question": "Which mechanism links treatment A to outcome B under constraint K?",
                "candidate_rationale": "Provides an alternative mechanism-focused framing of the same early research direction.",
                "research_object": "treatment A mechanism path",
                "outcome_or_decision": "mechanism candidacy for outcome B",
                "boundary_ids": ["bound_in_object"],
                "assumption_ids": ["assume_goal_scope"],
                "evidence_need_ids": ["need_lit_seeds"],
            },
        ],
        "scope_boundary": {
            "included_boundaries": [
                {
                    "boundary_id": "bound_in_object",
                    "dimension": "research_object",
                    "statement": "Limited to treatment A as named in the supplied goal.",
                }
            ],
            "excluded_boundaries": [
                {
                    "boundary_id": "bound_ex_method",
                    "dimension": "method",
                    "statement": "Do not infer completed experiments or retrieval from this artifact.",
                }
            ],
        },
        "assumptions": [
            {
                "assumption_id": "assume_goal_scope",
                "statement": "The user intends treatment A as the primary research object.",
                "source_basis": "user_input",
                "confirmation_status": "unconfirmed",
                "candidate_ids": ["cand_alpha", "cand_beta"],
            }
        ],
        "verification_readiness": {
            "readiness_basis": "planning_only",
            "overall_readiness": "ready_for_literature_positioning",
            "candidate_assessments": [
                {
                    "candidate_id": "cand_alpha",
                    "readiness": "ready_for_literature_positioning",
                    "reason": "Candidates are bounded enough to hand off for literature positioning.",
                    "blocking_evidence_need_ids": [],
                    "unresolved_assumption_ids": ["assume_goal_scope"],
                },
                {
                    "candidate_id": "cand_beta",
                    "readiness": "ready_for_literature_positioning",
                    "reason": "Same planning readiness applies to the mechanism-framed candidate.",
                    "blocking_evidence_need_ids": [],
                    "unresolved_assumption_ids": ["assume_goal_scope"],
                },
            ],
        },
        "next_evidence_needs": [
            {
                "evidence_need_id": "need_lit_seeds",
                "candidate_ids": ["cand_alpha", "cand_beta"],
                "need_kind": "literature_positioning",
                "question": "What prior work bounds the treatment-A and outcome-B relationship?",
                "minimum_sufficient_input": "Three seed papers or a curated keyword family covering A and B.",
                "resolution_effect": "narrow_candidate",
                "blocks_readiness": False,
            }
        ],
        "handoff": {
            "handoff_status": "ready_for_next_consumer",
            "next_consumer": "key_literature_positioning",
            "candidate_ids": ["cand_alpha", "cand_beta"],
            "evidence_need_ids": ["need_lit_seeds"],
            "instruction": "Position literature for both unverified candidates while preserving listed evidence needs; do not execute retrieval until confirmation.",
            "requires_user_confirmation_before_external_execution": True,
        },
    }


def _included_boundary_ids(body: dict[str, Any]) -> set[str]:
    rows = body["scope_boundary"]["included_boundaries"]
    return {row["boundary_id"] for row in rows}


def _assumption_ids(body: dict[str, Any]) -> set[str]:
    return {row["assumption_id"] for row in body["assumptions"]}


def _evidence_need_ids(body: dict[str, Any]) -> set[str]:
    return {row["evidence_need_id"] for row in body["next_evidence_needs"]}


def _candidate_ids(body: dict[str, Any]) -> set[str]:
    return {row["candidate_id"] for row in body["question_candidates"]}


def _static_cross_references_ok(body: dict[str, Any]) -> list[str]:
    """Contract cross-reference checks not expressible as pure JSON Schema foreign keys."""
    errors: list[str] = []
    included = _included_boundary_ids(body)
    assumptions = _assumption_ids(body)
    needs = _evidence_need_ids(body)
    candidates = _candidate_ids(body)

    for cand in body["question_candidates"]:
        for bid in cand["boundary_ids"]:
            if bid not in included:
                errors.append(f"candidate {cand['candidate_id']} boundary_ids->{bid}")
        for aid in cand["assumption_ids"]:
            if aid not in assumptions:
                errors.append(f"candidate {cand['candidate_id']} assumption_ids->{aid}")
        for eid in cand["evidence_need_ids"]:
            if eid not in needs:
                errors.append(f"candidate {cand['candidate_id']} evidence_need_ids->{eid}")

    for assumption in body["assumptions"]:
        for cid in assumption["candidate_ids"]:
            if cid not in candidates:
                errors.append(f"assumption {assumption['assumption_id']} candidate_ids->{cid}")

    for need in body["next_evidence_needs"]:
        for cid in need["candidate_ids"]:
            if cid not in candidates:
                errors.append(f"need {need['evidence_need_id']} candidate_ids->{cid}")

    for assessment in body["verification_readiness"]["candidate_assessments"]:
        if assessment["candidate_id"] not in candidates:
            errors.append(f"assessment candidate_id->{assessment['candidate_id']}")
        for eid in assessment["blocking_evidence_need_ids"]:
            if eid not in needs:
                errors.append(f"assessment blocking_evidence_need_ids->{eid}")
        for aid in assessment["unresolved_assumption_ids"]:
            if aid not in assumptions:
                errors.append(f"assessment unresolved_assumption_ids->{aid}")

    for cid in body["handoff"]["candidate_ids"]:
        if cid not in candidates:
            errors.append(f"handoff candidate_ids->{cid}")
    for eid in body["handoff"]["evidence_need_ids"]:
        if eid not in needs:
            errors.append(f"handoff evidence_need_ids->{eid}")

    return errors


def test_registry_references_resolvable_artifact_schema_not_required_fields() -> None:
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    agent = next(row for row in registry["agents"] if row["agentId"] == "research_question_refinement")
    output = agent["outputContract"]

    assert output["artifactContract"] == "research_question_refinement_artifact_v1"
    assert output["artifactSchema"] == SCHEMA_REL
    assert "requiredFields" not in output
    assert (REPO_ROOT / output["artifactSchema"]).is_file()


def test_schema_is_valid_json_with_six_required_closed_fields() -> None:
    schema = _load_schema()
    assert schema["$id"].endswith("/research_question_refinement_artifact_v1.schema.json")
    assert schema["contractId"] == "research_question_refinement_artifact_v1"
    assert schema["semanticVersion"] == "1.0.0"
    assert schema["additionalProperties"] is False
    assert list(schema["required"]) == list(SIX_BODY_FIELDS)
    assert set(schema["properties"]) == set(SIX_BODY_FIELDS)

    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    assert validator.is_valid(_valid_body())


def test_valid_fixture_passes_schema_and_static_cross_refs() -> None:
    body = _valid_body()
    _validator().validate(body)
    assert _static_cross_references_ok(body) == []


def test_extra_field_is_rejected() -> None:
    body = _valid_body()
    body["unexpected_top_level"] = "not allowed"
    with pytest.raises(ValidationError):
        _validator().validate(body)

    nested = _valid_body()
    nested["handoff"]["extra_note"] = "not allowed"
    with pytest.raises(ValidationError):
        _validator().validate(nested)


def test_wrong_nested_type_is_rejected() -> None:
    body = _valid_body()
    body["verification_readiness"]["candidate_assessments"] = "not-an-array"
    with pytest.raises(ValidationError):
        _validator().validate(body)


def test_invalid_cross_reference_static_assertion() -> None:
    body = _valid_body()
    body["question_candidates"][0]["boundary_ids"] = ["bound_missing"]
    # Structural schema still accepts unresolved reference IDs (shape-only encoding).
    _validator().validate(body)
    errors = _static_cross_references_ok(body)
    assert any("boundary_ids->bound_missing" in err for err in errors)


def test_schema_document_is_parseable_without_schema_error() -> None:
    try:
        Draft202012Validator.check_schema(_load_schema())
    except SchemaError as exc:  # pragma: no cover - failure path
        pytest.fail(f"schema is not a valid Draft 2020-12 document: {exc}")


def test_reference_constraints_meta_schema_is_a_valid_draft_2020_12_schema() -> None:
    try:
        Draft202012Validator.check_schema(_load_reference_constraints_schema())
    except SchemaError as exc:  # pragma: no cover - failure path
        pytest.fail(f"reference constraints meta schema is invalid: {exc}")


def test_reference_constraints_are_parseable_and_reference_existing_schema_fields() -> None:
    artifact_schema = _load_schema()
    declaration = artifact_schema["x-openair-referenceConstraints"]
    meta_schema = _load_reference_constraints_schema()
    Draft202012Validator(meta_schema).validate(declaration)

    collection_ids = [collection["id"] for collection in declaration["identifierCollections"]]
    rule_ids = [rule["id"] for rule in declaration["rules"]]
    assert len(collection_ids) == len(set(collection_ids))
    assert len(rule_ids) == len(set(rule_ids))

    for collection in declaration["identifierCollections"]:
        for pointer in collection["identifierPointers"]:
            nodes = _resolve_pointer_tokens(artifact_schema, pointer)
            assert all(node.get("type") == "string" for node in nodes)

    for rule in declaration["rules"]:
        for field_name in (
            "sourceReferencePointer",
            "targetReferencePointer",
            "leftPointer",
            "rightPointer",
        ):
            pointer = rule.get(field_name)
            if pointer is not None:
                nodes = _resolve_pointer_tokens(artifact_schema, pointer)
                assert all(node.get("type") == "string" for node in nodes)

        for field_name in (
            "sourceCollectionId",
            "targetCollectionId",
            "leftCollectionId",
            "rightCollectionId",
        ):
            collection_id = rule.get(field_name)
            if collection_id is not None:
                assert collection_id in collection_ids
