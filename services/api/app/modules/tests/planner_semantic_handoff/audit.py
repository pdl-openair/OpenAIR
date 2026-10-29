"""Observe unresolved policy branches without making them a passing test policy."""

from copy import deepcopy

from app.modules.research_design_service import ResearchDesignConfirmationError

from .support import build, bundle_for, confirm, decode, formal_text, freeform_payload, real_payload


def planning_source():
    return {
        "sourceChoice": "planning_source_1",
        "sourceArtifactRef": "artifact:pv2-prior",
        "sourceKind": "research_package",
        "sourceId": "pv2-prior",
        "sourceVersion": "1.0",
        "sourceContentHash": "sha256:" + "c" * 64,
        "name": "Offline software control",
        "units": [
            {
                "unitNumber": 1,
                "sourcePath": "/results/0",
                "category": "reference_result",
                "content": "Supplied software test premise.",
                "supportingSources": [],
            }
        ],
    }


def observe(
    source,
    payload,
    *,
    workflow_id="opentrajectory_research_workflow",
    freeform=False,
    planning_sources=(),
):
    bundle = bundle_for(source, workflow_id)
    proposal = decode(payload, bundle, freeform=freeform, planning_sources=planning_sources)
    service, repository, design = build(proposal, bundle, source, freeform=freeform)
    observation = {
        "input": deepcopy(payload),
        "formalDesign": design.model_dump(mode="json", by_alias=True),
        "coverageStatus": design.coverage_report.status.value,
        "instanceCount": len(design.instances),
        "targets": design.understanding.target_outputs,
        "confirmation": None,
    }
    try:
        _task, confirmed = confirm(service, repository, design)
        observation["confirmation"] = confirmed.status.value
    except ResearchDesignConfirmationError as exc:
        observation["confirmation"] = {
            "stage": "ResearchDesignService.confirm/_confirmation_target",
            "exception": type(exc).__name__,
            "message": str(exc),
        }
    observation["contentReadable"] = payload.get("targetOutputs", [""])[-1] in formal_text(design)
    return observation


def blocking_probes(source):
    bundle = bundle_for(source)
    payload = real_payload()
    for row in payload["responsibilityDeltas"]:
        row.pop("targetOutputNumbers")
    freeform = freeform_payload(bundle, ["有当前合法工作关联的成果。", "关联缺口的成果。"], [[1]])
    composite = real_payload()
    row = composite["responsibilityDeltas"][4]
    row.pop("currentProjectWorkContext")
    row["reuseCandidateKey"] = "planning_source_1"
    composite["planningSourceUses"] = [
        {
            "sourceChoice": "planning_source_1",
            "adoptedUnitNumbers": [1],
            "notAdoptedUnitNumbers": [],
            "revalidationUnitNumbers": [],
        }
    ]
    return {
        "unassociatedWorkflow": observe(source, payload),
        "unassociatedFreeform": observe(source, freeform, freeform=True),
        "compositeReuse": observe(source, composite, planning_sources=[planning_source()]),
    }
