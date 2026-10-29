from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.modules.artifact_field_projection import (
    ARTIFACT_FIELD_PROJECTION_KEY,
    merge_artifact_field_projection,
    project_scalar_fields_from_content,
)
from app.modules.binding_input_runtime_projection import (
    binding_input_fact_ref,
    project_binding_input_runtime_facts,
)
from app.modules.capability_applicability_producer import (
    produce_capability_applicability_results,
)
from app.modules.conditional_capability_binding import (
    POLICY_VERSION,
    CapabilityApplicabilityStatus,
    evaluate_skill_binding_eligibility,
)
from app.modules.scientific_state_fact_projection import (
    materialize_scientific_state_projection,
    project_validated_structured_result,
)

ROOT = Path(__file__).resolve().parents[3]
CONSUMER_SRC = (
    ROOT
    / "skills"
    / "cs_research"
    / "reproduction-execution-input-closure"
    / "src"
)
if str(CONSUMER_SRC) not in sys.path:
    sys.path.insert(0, str(CONSUMER_SRC))

from reproduction_execution_input_closure.applicability import (  # noqa: E402
    evaluate_capability_applicability,
)


SOURCE_BINDING_ID = (
    "bind:cs_reproduction:execution_readiness_validation:"
    "skill:execution_readiness_assessment"
)
CONSUMER_BINDING_ID = (
    "bind:cs_reproduction:real_execution_result_acquisition:"
    "skill:reproduction_execution_input_closure"
)
EVALUATOR_REF = (
    "python:reproduction_execution_input_closure.applicability:"
    "evaluate_capability_applicability"
)


def _consumer_binding() -> dict:
    return {
        "bindingId": CONSUMER_BINDING_ID,
        "bindingVersion": 3,
        "definitionId": "cs_reproduction:real_execution_result_acquisition",
        "definitionVersion": 3,
        "skillId": "reproduction_execution_input_closure",
        "skillDefinitionVersion": 1,
        "enabled": True,
        "conditionalPolicy": {
            "policyVersion": POLICY_VERSION,
            "evaluatorRef": EVALUATOR_REF,
            "requiredRuntimeFactRefs": [
                binding_input_fact_ref("execution_readiness_record")
            ],
        },
        "inputSources": {
            "contractVersion": 2,
            "fields": [
                {
                    "targetField": "execution_readiness_record",
                    "sourcePresence": "required",
                    "source": {
                        "kind": "artifact",
                        "bindingId": SOURCE_BINDING_ID,
                        "bindingVersion": 3,
                        "field": "$",
                    },
                }
            ],
        },
    }


def _research_task(status: str) -> dict:
    return {
        "executionDecisions": [
            {
                "assignments": [
                    {
                        "assignmentId": "assign-readiness",
                        "sourceBindingRefs": [
                            {
                                "bindingId": SOURCE_BINDING_ID,
                                "bindingVersion": 3,
                            }
                        ],
                    }
                ]
            }
        ],
        "workUnits": [
            {
                "workUnitId": "work-readiness",
                "assignmentId": "assign-readiness",
                "status": "completed",
                "executorRef": {
                    "executorType": "skill",
                    "executorId": "execution_readiness_assessment",
                    "definitionVersion": 1,
                },
            }
        ],
        "artifacts": [
            {
                "artifactId": "artifact-readiness",
                "workUnitId": "work-readiness",
                "artifactState": "usable",
                "validatorResult": {
                    "status": "passed",
                    ARTIFACT_FIELD_PROJECTION_KEY: {"status": status},
                },
            }
        ],
    }


def test_scalar_artifact_field_projection_keeps_only_top_level_scalars() -> None:
    projected = project_scalar_fields_from_content(
        '{"status":"not_ready","blocking_gaps":[{"id":"g1"}],"count":2,"ok":true}'
    )
    assert projected == {
        "status": "not_ready",
        "blocking_gaps": 1,
        "count": 2,
        "ok": True,
    }


def test_scalar_artifact_field_projection_projects_top_level_array_lengths() -> None:
    projected = project_scalar_fields_from_content(
        '{"status":"ready","resource_acquisition_requests":[],"requirements":[],"verify_imports":[]}'
    )
    assert projected == {
        "status": "ready",
        "resource_acquisition_requests": 0,
        "requirements": 0,
        "verify_imports": 0,
    }


def test_merge_artifact_field_projection_attaches_validator_metadata() -> None:
    merged = merge_artifact_field_projection(
        {"status": "passed", "contentSha256": "a" * 64},
        '{"status":"ready","executable_scope":["run"]}',
    )
    assert merged[ARTIFACT_FIELD_PROJECTION_KEY] == {
        "status": "ready",
        "executable_scope": 1,
    }


def test_binding_input_projection_reads_admitted_artifact_fields() -> None:
    consumer_binding = _consumer_binding()
    snapshot = {
        "bindings": {
            "skills": [
                {
                    "bindingId": SOURCE_BINDING_ID,
                    "bindingVersion": 3,
                    "skillId": "execution_readiness_assessment",
                    "skillDefinitionVersion": 1,
                    "enabled": True,
                },
                consumer_binding,
            ]
        }
    }
    projected = project_binding_input_runtime_facts(
        binding=consumer_binding,
        snapshot=snapshot,
        research_task=_research_task("not_ready"),
        required_refs={binding_input_fact_ref("execution_readiness_record")},
    )
    assert projected == {
        binding_input_fact_ref("execution_readiness_record"): {"status": "not_ready"}
    }


def test_ready_only_conditional_binding_release_and_withhold() -> None:
    definition_identity = ("cs_reproduction:real_execution_result_acquisition", 3)
    capability_ref = "skill:reproduction_execution_input_closure:1"
    consumer_binding = _consumer_binding()
    snapshot = {
        "definitions": {"skills": []},
        "bindings": {
            "skills": [
                {
                    "bindingId": SOURCE_BINDING_ID,
                    "bindingVersion": 3,
                    "skillId": "execution_readiness_assessment",
                    "skillDefinitionVersion": 1,
                    "enabled": True,
                },
                consumer_binding,
            ]
        },
    }
    cases = {
        "ready": CapabilityApplicabilityStatus.ELIGIBLE,
        "not_ready": CapabilityApplicabilityStatus.INELIGIBLE,
        "partially_ready": CapabilityApplicabilityStatus.INELIGIBLE,
    }
    for producer_status, expected in cases.items():
        results = produce_capability_applicability_results(
            snapshot=snapshot,
            definition_identity=definition_identity,
            authoritative_runtime_facts={
                "runtime:research-task:v1": _research_task(producer_status)
            },
            evaluator_resolver=lambda _ref: evaluate_capability_applicability,
        )
        result = results[capability_ref]
        assert result.status is expected, producer_status
        eligibility = evaluate_skill_binding_eligibility(
            consumer_binding,
            capability_ref=capability_ref,
            authoritative_runtime_fact_refs={"runtime:research-task:v1"},
            applicability_results=results,
        )
        assert eligibility is expected, producer_status
        assert result.status is not CapabilityApplicabilityStatus.ELIGIBLE or (
            producer_status == "ready"
        )


def test_registry_declares_ready_only_conditional_binding() -> None:
    import yaml

    from app.modules.conditional_capability_binding import validate_conditional_policy

    registry = yaml.safe_load((ROOT / "skills" / "registry.yaml").read_text(encoding="utf-8"))
    row = next(
        item
        for item in registry["skills"]
        if item["skill_id"] == "reproduction_execution_input_closure"
    )
    assert row["conditional_binding"] == {
        "policy_version": "conditional_capability_binding_v1",
        "evaluator_ref": EVALUATOR_REF,
        "required_runtime_fact_refs": [
            "binding-input:execution_readiness_record",
        ],
    }
    policy = validate_conditional_policy(
        {
            "policyVersion": row["conditional_binding"]["policy_version"],
            "evaluatorRef": row["conditional_binding"]["evaluator_ref"],
            "requiredRuntimeFactRefs": list(
                row["conditional_binding"]["required_runtime_fact_refs"]
            ),
        }
    )
    assert policy["requiredRuntimeFactRefs"] == [
        "binding-input:execution_readiness_record"
    ]


def test_registry_projects_readiness_result_into_scientific_state() -> None:
    import yaml

    registry = yaml.safe_load((ROOT / "skills" / "registry.yaml").read_text(encoding="utf-8"))
    row = next(
        item
        for item in registry["skills"]
        if item["skill_id"] == "execution_readiness_assessment"
    )

    assert row["scientific_state_projection"] == {
        "contract_version": 2,
        "materializer_ref": "research:declared-scientific-facts:v1",
        "facts": [
            {"fact_key": "execution_readiness_status", "source_field": "status"},
            {
                "fact_key": "execution_plan_resource_relation_status",
                "source_field": "plan_resource_relation_status",
            },
            {
                "fact_key": "execution_readiness_executable_scope",
                "source_field": "executable_scope",
            },
            {
                "fact_key": "execution_readiness_blocking_gaps",
                "source_field": "blocking_gaps",
            },
            {"fact_key": "execution_readiness_unknowns", "source_field": "unknowns"},
        ],
    }


def test_not_ready_gaps_and_unknowns_materialize_into_scientific_state() -> None:
    import yaml

    registry = yaml.safe_load((ROOT / "skills" / "registry.yaml").read_text(encoding="utf-8"))
    declaration = next(
        item["scientific_state_projection"]
        for item in registry["skills"]
        if item["skill_id"] == "execution_readiness_assessment"
    )
    result = {
        "status": "not_ready",
        "plan_resource_relation_status": "not_verified",
        "executable_scope": [],
        "code": {},
        "resources": {},
        "environment": {},
        "entry_verification": {},
        "blocking_gaps": [{"kind": "missing_checkpoint", "detail": "checkpoint absent"}],
        "unknowns": ["CUDA compatibility is unverified"],
    }
    projected = project_validated_structured_result(json.dumps(result), declaration)
    task = materialize_scientific_state_projection(
        task_payload={
            "taskId": "task-readiness-not-ready",
            "status": "running",
            "finishedAt": None,
            "finalResultAuthority": None,
            "scientificStates": [],
        },
        declaration=declaration,
        projected_facts=projected,
        source_artifact_id="artifact-readiness",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
    )

    state = task["scientificStates"][-1]
    assert state["scientificFacts"] == {
        "execution_readiness_status": "not_ready",
        "execution_plan_resource_relation_status": "not_verified",
        "execution_readiness_executable_scope": [],
        "execution_readiness_blocking_gaps": result["blocking_gaps"],
        "execution_readiness_unknowns": result["unknowns"],
    }
    assert not {
        "execution_readiness_code",
        "execution_readiness_resources",
        "execution_readiness_environment",
        "execution_readiness_entry_verification",
    }.intersection(state["scientificFacts"])
