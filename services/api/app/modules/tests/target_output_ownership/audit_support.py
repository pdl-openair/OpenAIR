"""Isolated audit inputs; no model calls, business database, or execution claims."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

from app.models.research_orchestration import ConfirmationV12, ResearchTaskV12
from app.modules.repo_paths import repo_root
from app.modules.research_completion_decision import expected_project_completion_evaluation
from app.modules.research_design_activation import (
    approved_design_activation_updates,
    design_versions_after_confirmation,
)
from app.modules.research_design_compiler import compile_research_design_proposal
from app.modules.research_design_planner_assembly import build_adaptive_workflow_semantics
from app.modules.research_design_service import ResearchDesignService, new_research_task
from app.modules.research_design_workflow_semantics import WorkflowProjectSemanticBriefV4
from app.modules.research_finalization import record_scientific_state_from_durable_facts
from app.modules.workflow_planning import workflow_planning_bundle
from app.modules.workflow_planning_admission import WorkflowPlanningAdmission

NOW = datetime(2026, 10, 9, tzinfo=UTC)
QUERY = (
    "我最近在做 agent 评测，发现 LLM judge 有时候很容易被回答风格带偏："
    "任务本身做得差不多，但如果回答更长、解释得更多，或者写得更像已经完成了，"
    "分数反而会更高。我想知道这个现象现在有没有比较可靠的研究证据，"
    "通常在什么情况下最明显，我们目前到底能下什么结论，还有哪些关键问题没搞清楚，"
    "下一步最值得验证什么？"
)


def bundle_for(source, workflow_id="opentrajectory_research_workflow"):
    return workflow_planning_bundle(
        selection=WorkflowPlanningAdmission(
            workflow_id=workflow_id,
            reason="Explicit isolated ownership audit",
            user_facing_summary="核查研究成果责任",
            suggested_title="研究成果责任核查",
        ),
        raw_goal=QUERY,
        definition_source=source,
    )


def brief_for(bundle, *, records="planned", split_owner=None, outputs=None, output_owners=None):
    context = bundle.planning_context
    rows = sorted(
        [*context["coreResponsibilities"], *context["conditionalResponsibilities"]],
        key=lambda row: row["displayOrder"],
    )
    deltas = []
    for position, row in enumerate(rows, 1):
        key = row["nodeKey"]
        if key == "research_record_establishment" and records == "omitted":
            continue
        if key == "research_record_establishment" and records == "reused":
            deltas.append(
                {
                    "responsibilityChoice": f"responsibility_{position}",
                    "reuseCandidateKey": "planning_source_1",
                }
            )
        elif row in context["coreResponsibilities"] or key == "research_record_establishment":
            contexts = ["围绕当前问题核查来源、条件、证据强度和未知。"]
            if key == split_owner:
                contexts = ["核查篇幅偏差证据。", "核查完成感表述偏差证据。"]
            deltas.append(
                {
                    "responsibilityChoice": f"responsibility_{position}",
                    "workItemScientificContexts": contexts,
                }
            )
    payload = {
        "researchGoal": QUERY,
        "primaryResearchQuestions": ["回答风格在什么条件下影响 judge 判断？"],
        "auxiliaryResearchDimensions": ["篇幅和完成感表述"],
        "targetOutputs": outputs
        if outputs is not None
        else [row["description"] for row in context.get("targetOutputSpecs", [])]
        or ["当前问题的结论、依据、限制和下一步。"],
        "scientificUnknowns": ["效应大小、适用条件和因果解释尚未确认。"],
        "responsibilityDeltas": deltas,
    }
    if output_owners is not None:
        assert outputs is not None and len(output_owners) == len(outputs)
        choice_by_node = {
            row["nodeKey"]: f"responsibility_{index}" for index, row in enumerate(rows, 1)
        }
        for number, owner in enumerate(output_owners, 1):
            choice = choice_by_node[owner]
            delta = next(row for row in deltas if row["responsibilityChoice"] == choice)
            delta.setdefault("targetOutputNumbers", []).append(number)
    sources = []
    if records == "reused":
        # Controlled candidate, not proof of an accepted real research result.
        sources = [
            {
                "sourceChoice": "planning_source_1",
                "sourceArtifactRef": "artifact:isolated-prior-records",
            }
        ]
        payload["planningSourceUses"] = [
            {
                "sourceChoice": "planning_source_1",
                "adoptedUnitNumbers": [1],
                "notAdoptedUnitNumbers": [],
                "revalidationUnitNumbers": [],
            }
        ]
    return WorkflowProjectSemanticBriefV4.model_validate(payload), sources


def compile_semantic(semantic, bundle):
    return compile_research_design_proposal(
        semantic,
        definition_catalog=bundle.definition_catalog,
        raw_input=QUERY,
        workflow_planning_context=bundle.planning_context,
    )


def materialize_design(proposal, bundle, source, *, task=None):
    # Only the admission read is replaced; production compiles and materializes
    # the Definition contracts, coverage claims, criteria, and requirements.
    repository = SimpleNamespace(load_planning_workflow_admission=lambda **_: bundle.admission)
    service = ResearchDesignService(
        repository=repository, definition_source=source, clock=lambda: NOW
    )
    task = task or new_research_task(
        task_id="task_ot_ownership_audit", registry_snapshot_ref=source.snapshot_ref()
    )
    design = service._build_design(
        task, proposal, "planning_ot_ownership_audit", owner_id="audit", workspace_id="audit"
    )
    return task, design


def confirmed_task(task, design):
    payload = task.model_dump(mode="json", by_alias=True)
    payload["researchDesignVersions"] = [design.model_dump(mode="json", by_alias=True)]
    payload["status"] = "awaiting_confirmation"
    template = json.loads(
        (
            repo_root() / "packages/shared/test-fixtures/research-orchestration-v1.2-valid.json"
        ).read_text(encoding="utf-8")
    )["researchTask"]
    attempt = deepcopy(template["planningAttempts"][0])
    attempt["taskId"] = task.task_id
    attempt["planningAttemptId"] = "planning_ot_ownership_audit"
    payload["planningAttempts"] = [attempt]
    base = ResearchTaskV12.model_validate(payload)
    versions = design_versions_after_confirmation(
        versions=base.research_design_versions,
        design_id=design.research_design_id,
        version=1,
        decision="approved",
        confirmed_at=NOW,
        confirmation_id="confirmation_ot_audit",
    )
    confirmed = versions[0]
    payload["researchDesignVersions"] = [
        row.model_dump(mode="json", by_alias=True) for row in versions
    ]
    payload["confirmations"] = [
        ConfirmationV12(
            confirmationId="confirmation_ot_audit",
            targetType="research_design",
            targetId=design.research_design_id,
            targetVersion=1,
            decision="approved",
            actorId="audit",
            createdAt=NOW,
        ).model_dump(mode="json", by_alias=True)
    ]
    for key, value in approved_design_activation_updates(
        task=base, design=confirmed, activated_at=NOW
    ).items():
        if isinstance(value, list):
            value = [
                row.model_dump(mode="json", by_alias=True) if hasattr(row, "model_dump") else row
                for row in value
            ]
        elif hasattr(value, "model_dump"):
            value = value.model_dump(mode="json", by_alias=True)
        payload[key] = value
    payload["status"] = "running"
    return ResearchTaskV12.model_validate(payload), confirmed


def add_controlled_durable_facts(task, design, *, accepted_definitions, approve=True):
    """Validated structural test data, never an execution or research assertion."""
    template = json.loads(
        (
            repo_root() / "packages/shared/test-fixtures/research-orchestration-v1.2-valid.json"
        ).read_text(encoding="utf-8")
    )["researchTask"]
    payload = task.model_dump(mode="json", by_alias=True)
    for index, instance in enumerate(design.instances, 1):
        if instance.substage_definition_id not in accepted_definitions:
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


def legacy_design_for_comparison(legacy_semantic, bundle, source):
    """Recreate the pre-fix projection through production building primitives.

    The valid current-source design provides immutable identities; controlled
    legacy target refs then use the old (no owner metadata) contract. Never saved.
    """
    from app.models.research_orchestration import ResearchDesignVersion
    from app.modules.research_design_planner_assembly import build_adaptive_workflow_semantics
    from app.modules.research_design_service import _stable_id
    from app.modules.target_output_ownership import project_completion_requirements

    brief, sources = brief_for(bundle)
    correct = build_adaptive_workflow_semantics(
        raw_input=QUERY,
        definition_catalog=bundle.definition_catalog,
        workflow_planning_context=bundle.planning_context,
        brief=brief,
        planning_sources=sources,
    )
    task, design = materialize_design(compile_semantic(correct, bundle), bundle, source)
    proposal = compile_research_design_proposal(
        legacy_semantic,
        definition_catalog=bundle.definition_catalog,
        raw_input=QUERY,
    )
    service = ResearchDesignService(repository=None, definition_source=source, clock=lambda: NOW)
    targets = service._coverage_targets(design.research_design_id, proposal)
    instances = service._build_instances(
        design_id=design.research_design_id,
        proposal=proposal,
        definitions={
            row.instance_key: source.resolve(row.definition_id) for row in proposal.instances
        },
        target_ids_by_path={row.source_path: row.target_id for row in targets},
        previous=None,
    )
    report = service._coverage_report(
        design_id=design.research_design_id,
        version=1,
        planning_attempt_id="planning_ot_ownership_audit",
        proposal=proposal,
        targets=targets,
        instances=instances,
    )
    payload = design.model_dump(mode="json", by_alias=True)
    payload["instances"] = [row.model_dump(mode="json", by_alias=True) for row in instances]
    payload["coverageReport"] = report.model_dump(mode="json", by_alias=True)
    payload["projectCompletionRequirements"] = [
        row.model_dump(mode="json", by_alias=True)
        for row in project_completion_requirements(
            design_id=design.research_design_id,
            target_outputs=proposal.understanding.target_outputs,
            target_output_owners={},
            id_factory=_stable_id,
        )
    ]
    return task, ResearchDesignVersion.model_validate(payload)


def assemble(bundle, **kwargs):
    brief, sources = brief_for(bundle, **kwargs)
    semantic = build_adaptive_workflow_semantics(
        raw_input=QUERY,
        definition_catalog=bundle.definition_catalog,
        workflow_planning_context=bundle.planning_context,
        brief=brief,
        planning_sources=sources,
    )
    return semantic


def evaluate_controlled_consumers(
    semantic, bundle, source, *, accepted_definitions, approve=True, legacy=False
):
    task, design = (
        legacy_design_for_comparison(semantic, bundle, source)
        if legacy
        else materialize_design(compile_semantic(semantic, bundle), bundle, source)
    )
    task, design = confirmed_task(task, design)
    task = add_controlled_durable_facts(
        task, design, accepted_definitions=accepted_definitions, approve=approve
    )
    _snapshotted, state = record_scientific_state_from_durable_facts(task, design, occurred_at=NOW)
    return design, state, expected_project_completion_evaluation(design, state)


def reused_task_and_acceptance(source, *, approve=True):
    bundle = bundle_for(source)
    semantic = assemble(bundle, records="reused")
    task, design = materialize_design(compile_semantic(semantic, bundle), bundle, source)
    task, design = confirmed_task(task, design)
    task = add_controlled_durable_facts(
        task,
        design,
        accepted_definitions={row.definition_id for row in semantic.work_items},
        approve=approve,
    )
    artifact = task.artifacts[0]
    return (
        task,
        design,
        {
            "researchDesignId": design.research_design_id,
            "researchDesignVersion": design.version,
            "targetOutputRef": "understanding.targetOutputs[0]",
            "responsibilityKey": "research_record_establishment",
            "reusedResultRefs": ["artifact:isolated-prior-records"],
            "acceptanceArtifactRef": artifact.artifact_id,
            "verdict": "accepted",
        },
    )


def checked_reuse_result(source, *, approve=True, review_inputs=False):
    import hashlib
    import json

    task, design, acceptance = reused_task_and_acceptance(source, approve=approve)
    payload = task.model_dump(mode="json", by_alias=True)
    work = next(
        row
        for row in payload["workUnits"]
        if row["substageDefinitionId"] == "opentrajectory:next_research_recommendation"
    )
    artifact = next(row for row in payload["artifacts"] if row["workUnitId"] == work["workUnitId"])
    executor = {
        "executorType": "agent",
        "executorId": "general_research_agent",
        "definitionVersion": 1,
    }
    actor = {"actorType": "agent", "actorId": "general_research_agent", "definitionVersion": 1}
    work.update(
        {
            "inputArtifactRefs": acceptance["reusedResultRefs"],
            "executorRef": executor,
            "responsibleActorRef": actor,
        }
    )
    decision = next(
        row
        for row in payload["executionDecisions"]
        if row["executionDecisionId"] == work["executionDecisionId"]
    )
    decision["strategy"] = "general_research_agent"
    decision["fallbackInvoked"] = True
    decision["fallbackReasonCode"] = "preferred_not_executable_runtime"
    decision["assignments"][0].update({"executorRef": executor, "responsibleActorRef": actor})
    result_id = "isolated-checked-reuse"
    if not approve:
        artifact["artifactState"] = "needs_review"
    artifact["sourceResultId"] = result_id
    artifact["validatorResult"] = {
        "status": "passed",
        "artifactContract": "general_research_reasoning_artifact_v1",
    }
    content = {
        "scope": {},
        "reasoning_summary": {},
        "assumptions": [],
        "uncertainty": [],
        "next_action": {},
        "targetOutputReuseJudgments": [
            {
                key: acceptance[key]
                for key in ["targetOutputRef", "responsibilityKey", "reusedResultRefs", "verdict"]
            }
        ],
    }
    content["targetOutputReuseJudgments"][0]["rationale"] = (
        "Isolated supplied records cover this target."
    )
    text = json.dumps(content)
    payload["results"].append(
        {
            "resultId": result_id,
            "workUnitId": work["workUnitId"],
            "capabilityCallId": None,
            "storageType": "local",
            "storageScope": None,
            "storageRef": "isolated:test-material",
            "contentSha256": hashlib.sha256(text.encode()).hexdigest(),
            "mimeType": "application/json",
            "checkStatus": "passed" if approve else "pending",
            "checkResult": {},
            "sourceRefs": [],
            "artifactRefs": [artifact["artifactId"]],
            "scientificStateVersions": [],
            "createdAt": NOW.isoformat(),
        }
    )
    if review_inputs:
        for row in payload["agentInteractionRecords"]:
            row["inputArtifactRefs"], row["outputArtifactRefs"] = row["outputArtifactRefs"], []
    return payload, design, text
