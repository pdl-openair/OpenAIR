from __future__ import annotations

import copy
import json

import pytest

from app.execution.durable_orchestrator_runtime import (
    ArtifactContractValidationError,
    freeze_artifact_contract_body,
    validate_frozen_artifact_content,
)
from app.execution.durable_orchestrator_bridge import compile_durable_work_units


def _schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "contractId": "test_reference_contract_v1",
        "semanticVersion": "1.0.0",
        "type": "object",
        "additionalProperties": False,
        "required": ["nodes"],
        "properties": {
            "nodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "links"],
                    "properties": {
                        "id": {"type": "string"},
                        "links": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
        "x-openair-referenceConstraints": {
            "version": "1.0.0",
            "identifierCollections": [{
                "id": "node_ids",
                "identifierPointers": ["/nodes/*/id"],
                "duplicateErrorCode": "duplicate_identifier",
            }],
            "rules": [{
                "id": "links_exist",
                "kind": "exists",
                "errorCode": "reference_target_missing",
                "sourceReferencePointer": "/nodes/*/links/*",
                "targetCollectionId": "node_ids",
            }, {
                "id": "links_reciprocal",
                "kind": "reciprocal",
                "errorCode": "reference_relation_inconsistent",
                "sourceCollectionId": "node_ids",
                "sourceReferencePointer": "/nodes/*/links/*",
                "targetCollectionId": "node_ids",
                "targetReferencePointer": "/nodes/*/links/*",
            }],
        },
    }


def _frozen():
    return freeze_artifact_contract_body("test_reference_contract_v1", _schema(), source_ref="test/schema.json")


@pytest.mark.parametrize(
    ("body", "error_code"),
    [
        ({"nodes": [{"id": "a", "links": ["missing"]}]}, "reference_target_missing"),
        ({"nodes": [{"id": "a", "links": ["b"]}, {"id": "b", "links": []}]}, "reference_relation_inconsistent"),
    ],
    ids=["structurally-valid-reference-missing", "structurally-valid-reciprocal-inconsistent"],
)
def test_frozen_schema_preserves_scientific_references(body: dict, error_code: str) -> None:
    assert validate_frozen_artifact_content(_frozen(), json.dumps(body)) == body


def test_frozen_schema_digest_and_body_remain_authoritative_after_source_mutation() -> None:
    frozen = _frozen()
    changed_source = copy.deepcopy(_schema())
    changed_source["properties"]["nodes"]["items"]["properties"]["extra"] = {"type": "string"}

    assert frozen.schema_digest != freeze_artifact_contract_body(
        "test_reference_contract_v1", changed_source, source_ref="test/schema.json",
    ).schema_digest
    body = {"nodes": [{"id": "a", "links": []}], "extra": True}
    assert validate_frozen_artifact_content(frozen, json.dumps(body)) == body


def test_review_and_key_literature_use_their_own_frozen_schemas() -> None:
    def unit_for(agent_id: str) -> dict:
        return compile_durable_work_units({
            "input_snapshot": {"goalSummary": "bounded task", "creationPlan": {
                "selectedCapabilities": [{"kind": "agent", "id": agent_id, "skillIds": []}],
            }},
        })[0]

    question = unit_for("research_question_refinement")
    literature = unit_for("key_literature_positioning")
    review = question["mandatoryReviewContract"]

    assert question["artifactSchemaDigest"] != literature["artifactSchemaDigest"]
    assert review["artifactSchemaDigest"] not in {question["artifactSchemaDigest"], literature["artifactSchemaDigest"]}
    assert review["artifactContract"] != question["artifactContract"]
