"""Offline structural facts. Setup adapted from the existing audit input builder.

No dependency on another Suite; all judgments are made by production consumers.
These supplied facts do not prove research execution or scientific acceptance.
"""

import json
from copy import deepcopy

from app.models.research_orchestration import ResearchTaskV12

from .support import NOW, ROOT


def add_controlled_durable_facts(
    task, design, *, accepted_definitions, approve=True, omitted_objectives=()
):
    """Validated structural test data, never an execution or research assertion."""
    template = json.loads(
        (ROOT / "packages/shared/test-fixtures/research-orchestration-v1.2-valid.json").read_text(
            encoding="utf-8"
        )
    )["researchTask"]
    payload = task.model_dump(mode="json", by_alias=True)
    for index, instance in enumerate(design.instances, 1):
        if (
            instance.substage_definition_id not in accepted_definitions
            or instance.objective in omitted_objectives
        ):
            continue
        suffix = str(index)
        decision_id, assignment_id, work_id = (
            f"audit_decision_{suffix}",
            f"audit_assignment_{suffix}",
            f"audit_work_{suffix}",
        )
        row = instance.model_dump(mode="json", by_alias=True)
        criteria = row["acceptanceCriteria"]
        types = row["expectedArtifactTypes"]
        decision = deepcopy(template["executionDecisions"][0])
        decision.update(
            {
                "executionDecisionId": decision_id,
                "selectionRequestId": f"audit_selection_{suffix}",
                "decisionSequence": index,
                "taskId": task.task_id,
                "substageInstanceId": instance.substage_instance_id,
                "substageInstanceRevision": instance.instance_revision,
                "researchDesignVersion": design.version,
                "registrySnapshotRef": payload["registrySnapshotRef"],
                "status": "closed",
                "outcome": "succeeded",
                "closedAt": NOW.isoformat(),
            }
        )
        assignment = decision["assignments"][0]
        assignment.update(
            {
                "assignmentId": assignment_id,
                "objective": instance.objective,
                "expectedArtifactTypes": types,
                "acceptanceCriteria": criteria,
                "inputRefs": row["inputRefs"],
            }
        )
        decision["completionPolicy"]["requiredAssignmentIds"] = [assignment_id]
        decision["resultAssemblyPolicy"]["finalArtifactRequirements"] = [
            {"artifactType": kind, "minimumCount": 1, "sourceAssignmentIds": [assignment_id]}
            for kind in types
        ]
        work = deepcopy(template["workUnits"][0])
        work.update(
            {
                "workUnitId": work_id,
                "idempotencyKey": f"audit:{work_id}",
                "taskId": task.task_id,
                "executionDecisionId": decision_id,
                "assignmentId": assignment_id,
                "substageInstanceId": instance.substage_instance_id,
                "substageInstanceRevision": instance.instance_revision,
                "researchDesignVersion": design.version,
                "substageDefinitionId": instance.substage_definition_id,
                "substageDefinitionVersion": instance.substage_definition_version,
                "registrySnapshotRef": payload["registrySnapshotRef"],
                "expectedArtifactTypes": types,
                "acceptanceCriteria": criteria,
                "status": "completed",
                "finishedAt": NOW.isoformat(),
            }
        )
        work["authorizationSnapshot"]["confirmationRefs"] = [design.confirmation_ref]
        artifact_ids = []
        for number, kind in enumerate(types, 1):
            artifact_id = f"audit_artifact_{suffix}_{number}"
            artifact_ids.append(artifact_id)
            payload["artifacts"].append(
                {
                    "artifactId": artifact_id,
                    "workUnitId": work_id,
                    "substageInstanceId": instance.substage_instance_id,
                    "sourceCapabilityCallId": None,
                    "artifactType": kind,
                    "artifactState": "usable",
                    "version": 1,
                    "sourceRefs": ["isolated:test-material"],
                    "validatorResult": {},
                }
            )
        payload["executionDecisions"].append(decision)
        payload["workUnits"].append(work)
        if approve:
            payload["agentInteractionRecords"].append(
                {
                    "interactionId": f"audit_acceptance_{suffix}",
                    "idempotencyKey": f"audit:acceptance:{suffix}",
                    "taskId": task.task_id,
                    "executionDecisionId": decision_id,
                    "sourceAssignmentId": assignment_id,
                    "sourceWorkUnitId": work_id,
                    "targetAssignmentId": None,
                    "targetWorkUnitId": None,
                    "interactionType": "acceptance_recorded",
                    "inputArtifactRefs": [],
                    "outputArtifactRefs": artifact_ids,
                    "verdict": {
                        "verdictType": "acceptance",
                        "decision": "approved",
                        "rationaleCode": "isolated_test_input",
                    },
                    "summary": "Controlled audit input; no real research was executed.",
                    "createdAt": NOW.isoformat(),
                }
            )
    return ResearchTaskV12.model_validate(payload)
