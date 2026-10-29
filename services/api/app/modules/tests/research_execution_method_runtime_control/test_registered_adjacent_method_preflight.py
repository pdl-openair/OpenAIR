"""Actual registered adjacent Method; production contracts remain unchanged.

Only repository IO and runtime availability are fixtures. The Registry builder,
resolver, selection, input gate, role release and start/commit are production code.
No Provider, Skill execution or application database is used.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from app.models.research_orchestration import (
    InstanceRef, RegistrySnapshotRef, ResearchTaskV12, RuntimeReadinessSnapshot,
)
from app.modules import research_execution_decision_service as decisions
from app.modules.registry_snapshot_repository import InMemoryRegistrySnapshotRepository
from app.modules.research_orchestration_resolver import (
    ResearchOrchestrationResolver, ResolutionFacts, build_registry_snapshot,
)
from tests.support.research_lifecycle import build_confirmed_research_task
from tests.test_cs_reproduction_execution_infrastructure import _design_for_definition
from app.modules.tests.research_execution_method_runtime_control.test_execution_method_preflight_outcomes import _service, _outcomes_by_role


NOW = datetime(2026, 7, 28, 0, 0, 4, tzinfo=timezone.utc)
DEFINITION_ID = "cs_reproduction:execution_readiness_validation"


def test_registered_adjacent_readiness_preserves_local_preflight_and_closure_policy():
    snapshot = build_registry_snapshot()
    envelope = snapshot.envelope
    original_envelope = deepcopy(envelope)
    definition = max(
        (row for row in envelope["definitions"]["substages"]
         if row["definitionId"] == DEFINITION_ID and row["enabled"]),
        key=lambda row: row["definitionVersion"],
    )

    method = definition["executionMethod"]
    assert method["methodId"] == "cs_reproduction_execution_readiness"
    expected_policy = {
        "code_identity_acquisition": "continue_degraded",
        "resource_identity_acquisition": "continue_degraded",
        "environment_setup": "continue_degraded",
        "readiness_closure": "fail_closed",
    }
    assert {row["roleId"]: row["failurePolicy"] for row in method["roles"]} == expected_policy

    task = build_confirmed_research_task()
    design = _design_for_definition(task.research_design_versions[0], definition)
    payload = task.model_dump(mode="json", by_alias=True)
    payload["researchDesignVersions"] = [design.model_dump(mode="json", by_alias=True)]
    coverage = payload["researchDesignVersions"][0]["coverageReport"]
    coverage["claims"][0]["acceptanceCriterionRefs"] = [
        row.criterion_id for row in design.instances[0].acceptance_criteria
    ]
    coverage["targets"][0]["requiredArtifactTypes"] = list(design.instances[0].expected_artifact_types)
    payload["registrySnapshotRef"] = {
        "schemaVersion": snapshot.schema_version, "contentHash": snapshot.content_hash,
    }
    # Contract-level inputs are declared at selection. Their execution artifacts
    # are deliberately unavailable, so each original Binding is checked by gate.
    payload["scientificStates"][-1]["scientificFacts"] = {}
    task = ResearchTaskV12.model_validate(payload)
    instance = task.research_design_versions[0].instances[0]

    bindings = [row for row in envelope["bindings"]["skills"]
                if row["definitionId"] == definition["definitionId"]
                and row["definitionVersion"] == definition["definitionVersion"]
                and row["enabled"]]
    assert len(bindings) == 4
    skills = {(row["skillId"], row["definitionVersion"]): row
              for row in envelope["definitions"]["skills"]}
    for binding in bindings:
        skill = skills[binding["skillId"], binding["skillDefinitionVersion"]]
        assert skill["enabled"] and skill["status"] == "ready"
    ids = {f"skill:{row['skillId']}:{row['skillDefinitionVersion']}" for row in bindings}
    facts = ResolutionFacts(
        runtimeReadiness={identity: True for identity in ids}, providerReadiness={},
        authorizedCandidateRefs=ids, availableInputRefs=set(instance.input_refs),
        applicableRuleRefs=set(), riskAllowed=True, budgetAvailable=True,
        policyVersion=envelope["staticPolicies"]["policyVersion"],
    )
    readiness = {
        f"skill:{row['skillId']}:{row['skillDefinitionVersion']}": RuntimeReadinessSnapshot(
            capabilityRef={"skillId": row["skillId"], "definitionVersion": row["skillDefinitionVersion"]},
            ready=True, runtimeBindingId=f"python_adapter:{row['skillId']}",
            checkedAt=NOW, expiresAt=NOW + timedelta(hours=1), reason=None,
        ) for row in bindings
    }
    registry = InMemoryRegistrySnapshotRepository()
    registry.put(snapshot)
    resolver = ResearchOrchestrationResolver(registry)
    resolution = resolver.resolve(
        design=task.research_design_versions[0], designRef=task.active_research_design_ref,
        instanceRef=InstanceRef(substageInstanceId=instance.substage_instance_id,
                                instanceRevision=instance.instance_revision),
        registrySnapshotRef=RegistrySnapshotRef(schemaVersion=snapshot.schema_version,
                                               contentHash=snapshot.content_hash),
        facts=facts,
    )
    prepared = decisions.prepare_selection(
        task, instance_id=instance.substage_instance_id, resolution=resolution,
        snapshot_envelope=envelope, facts=facts, runtime_readiness=readiness,
        selection_request_id="selection-registered-adjacent-missing-input",
        selection_command_hash="command-registered-adjacent-missing-input",
        expected_task_revision=task.revision, owner_id="owner", workspace_id="workspace",
        decided_at=NOW,
    )
    assert prepared.execution_method == method
    service, repository = _service((task, prepared, envelope, {}))
    result = service.start(
        task_id=task.task_id, instance_id=instance.substage_instance_id,
        owner_id="owner", workspace_id="workspace",
        selection_request_id="selection-registered-adjacent-missing-input",
        expected_task_revision=task.revision,
    )
    after = result.task
    decision = after.execution_decisions[-1]
    outcomes = _outcomes_by_role(after)
    # Reaching closure proves both parallel roots and the dependent environment
    # role were locally isolated instead of aborting the Method at the first gap.
    assert set(outcomes) == set(expected_policy)
    assert {row.failure_kind for row in outcomes.values()} == {"material_unavailable"}
    assert {row.reason_code for row in outcomes.values()} == {"input_composition_source_artifact_missing"}
    assert decision.status.value == "closed" and decision.outcome.value == "blocked"
    assert after.substage_runtime_states[0].status.value == "blocked_recoverable"
    assert after.work_units == [] and after.artifacts == [] and repository.dispatches == []
    assert repository.commit_calls == 1
    assert envelope == original_envelope
    assert decision.execution_method_ref == {
        "methodId": method["methodId"], "methodVersion": method["methodVersion"],
    }
    assert {
        row.execution_method_role_id: row.execution_method_role_control["failurePolicy"]
        for row in decision.assignments
    } == expected_policy
