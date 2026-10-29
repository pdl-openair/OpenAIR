from __future__ import annotations

from app.modules.capability_applicability_producer import (
    EXECUTION_INSTANCE_FACT_REF,
    RESEARCH_TASK_FACT_REF,
    SCIENTIFIC_STATE_FACT_REF,
    produce_capability_applicability_results,
)
from app.modules.conditional_capability_binding import (
    POLICY_VERSION,
    CapabilityApplicabilityStatus,
)


def test_conditional_binding_missing_required_runtime_facts_fails_closed() -> None:
    definition_identity = ("test:definition", 1)
    capability_ref = "skill:test_conditional_skill:1"
    evaluator_ref = "python:test_capability_applicability:evaluate"

    required_runtime_fact_refs = [
        RESEARCH_TASK_FACT_REF,
        EXECUTION_INSTANCE_FACT_REF,
        SCIENTIFIC_STATE_FACT_REF,
    ]

    snapshot = {
        "definitions": {
            "skills": [],
        },
        "bindings": {
            "skills": [
                {
                    "definitionId": definition_identity[0],
                    "definitionVersion": definition_identity[1],
                    "skillId": "test_conditional_skill",
                    "skillDefinitionVersion": 1,
                    "enabled": True,
                    "conditionalPolicy": {
                        "policyVersion": POLICY_VERSION,
                        "evaluatorRef": evaluator_ref,
                        "requiredRuntimeFactRefs": required_runtime_fact_refs,
                    },
                }
            ]
        },
    }

    results = produce_capability_applicability_results(
        snapshot=snapshot,
        definition_identity=definition_identity,
        authoritative_runtime_facts={},
    )

    result = results[capability_ref]

    assert result.status == CapabilityApplicabilityStatus.EVALUATION_FAILED
    assert result.evaluator_ref == evaluator_ref
    assert result.evaluated_runtime_fact_refs == []

def test_conditional_binding_with_complete_runtime_facts_calls_evaluator_and_accepts_result() -> None:
    definition_identity = ("test:definition", 1)
    capability_ref = "skill:test_conditional_skill:1"
    evaluator_ref = "python:test_capability_applicability:evaluate"

    required_runtime_fact_refs = [
        RESEARCH_TASK_FACT_REF,
        EXECUTION_INSTANCE_FACT_REF,
        SCIENTIFIC_STATE_FACT_REF,
    ]

    authoritative_runtime_facts = {
        RESEARCH_TASK_FACT_REF: {"taskId": "task-1"},
        EXECUTION_INSTANCE_FACT_REF: {"substageInstanceId": "instance-1"},
        SCIENTIFIC_STATE_FACT_REF: {"scientificStateVersion": 1},
        "runtime:unrelated:v1": {"ignored": True},
    }

    snapshot = {
        "definitions": {"skills": []},
        "bindings": {
            "skills": [
                {
                    "definitionId": definition_identity[0],
                    "definitionVersion": definition_identity[1],
                    "skillId": "test_conditional_skill",
                    "skillDefinitionVersion": 1,
                    "enabled": True,
                    "conditionalPolicy": {
                        "policyVersion": POLICY_VERSION,
                        "evaluatorRef": evaluator_ref,
                        "requiredRuntimeFactRefs": required_runtime_fact_refs,
                    },
                }
            ]
        },
    }

    captured: dict[str, object] = {}

    def evaluator(request: dict[str, object]) -> dict[str, object]:
        captured["request"] = request
        return {
            "capabilityRef": capability_ref,
            "evaluatorRef": evaluator_ref,
            "status": "eligible",
            "evaluatedRuntimeFactRefs": sorted(required_runtime_fact_refs),
        }

    def evaluator_resolver(ref: str):
        captured["resolved_ref"] = ref
        return evaluator

    results = produce_capability_applicability_results(
        snapshot=snapshot,
        definition_identity=definition_identity,
        authoritative_runtime_facts=authoritative_runtime_facts,
        evaluator_resolver=evaluator_resolver,
    )

    result = results[capability_ref]

    assert captured["resolved_ref"] == evaluator_ref

    assert captured["request"] == {
        "capabilityRef": capability_ref,
        "evaluatorRef": evaluator_ref,
        "runtimeFacts": {
            ref: authoritative_runtime_facts[ref]
            for ref in sorted(required_runtime_fact_refs)
        },
    }

    assert result.status == CapabilityApplicabilityStatus.ELIGIBLE
    assert result.evaluator_ref == evaluator_ref
    assert result.evaluated_runtime_fact_refs == sorted(required_runtime_fact_refs)
