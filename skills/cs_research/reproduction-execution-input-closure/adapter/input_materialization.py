"""Build source tools' inputs from actual evidence, not Agent-supplied identities."""

import json
from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256

SOURCE_TOOL_FIELDS = {
    "official_reference_source_acquisition": "source_package",
    "execution_plan_interpretation": "interpretation",
    "execution_plan_fact_assembly": "execution_plan_facts",
}


class RepairInputFeedback(ValueError):
    """Missing preparation material is observable feedback for the same Agent."""


def _required(context, fields):
    missing = [field for field in fields if context.get(field) is None]
    if missing:
        raise RepairInputFeedback("repair_tool_material_missing:" + ",".join(missing))
    return {key: deepcopy(context[key]) for key in fields}


def materialize_source_tool_parameters(skill_id, proposal, context):
    if skill_id == "official_reference_source_acquisition":
        return {"code_source": _required(context, ["code_identity"])["code_identity"]}
    if skill_id == "execution_plan_interpretation":
        result = _required(context, ["source_package", "reference_metrics"])
        goal_field = (
            "reproduction_baseline_and_criteria"
            if context.get("reproduction_baseline_and_criteria") is not None
            else "research_goal"
        )
        result.update(_required(context, [goal_field]))
        feedback = proposal.get("repair_feedback")
        if feedback is not None:
            if not isinstance(feedback, str) or not feedback.strip() or len(feedback) > 8000:
                raise RepairInputFeedback("repair_tool_feedback_invalid")
            # Instructions are not evidence: code-owned source and identity
            # fields above cannot be replaced by this correction feedback.
            result["repair_feedback"] = feedback
        return result
    if skill_id == "execution_plan_fact_assembly":
        return _required(context, ["source_package", "interpretation", "reference_metrics"])
    return deepcopy(dict(proposal))


def install_source_tool_evidence(skill_id, output, context):
    """Return whether this is source preparation; preserve artifact content exactly.

    Interpretation has no state field in its actual output contract. It is an
    interpretation, not an acquired identity; only the formal assembler produces
    the plan consumed by subsequent tools. Receipt/schema checks belong to the
    canonical executor. This function does not approve research quality.
    """
    field = SOURCE_TOOL_FIELDS.get(skill_id)
    if field is None:
        return False
    if skill_id == "execution_plan_interpretation" or output.get("state") == "succeeded":
        context[field] = deepcopy(dict(output))
    return True


def project_design_execution_fields(design, plan):
    """Synchronize duplicated machine inputs, preserving original scientific design.

    The caller must establish correction scope against formal source evidence
    before using this projection. It grants no action or scope by itself.
    """
    if not isinstance(design, Mapping) or not isinstance(plan, Mapping):
        raise RepairInputFeedback("repair_design_or_plan_material_missing")
    fields = {
        "resource_acquisition_requests",
        "execution_resource_binding_plan",
        "executable",
        "argv",
        "working_directory",
        "timeout_seconds",
        "environment_variables",
        "actual_metric_result_source",
        "actual_metric_requests",
    }
    result = deepcopy(dict(design))
    result.update({key: deepcopy(plan.get(key)) for key in fields})
    return result


def resolve_repair_source_context(repository, *, owner, original_inputs):
    """Read the exact original plan producer, not a latest same-named result.

    The repository's existing input reader verifies artifact ownership, frozen
    version/hash, live production and storage content. The exact original plan
    artifact identifies the producer whose frozen parameters supplied sources.
    """
    task = repository.load_read_only(
        task_id=owner.task_id,
        owner_id=owner.owner_id,
        workspace_id=owner.workspace_id,
    )
    calls = {row.capability_call_id: row for row in task.capability_calls}
    parent = calls.get(owner.parent_call_id)
    work = next(
        (
            row
            for row in task.work_units
            if parent is not None and row.work_unit_id == parent.work_unit_id
        ),
        None,
    )
    if work is None:
        raise RepairInputFeedback("repair_original_work_material_missing")
    context = repository.read_direct_skill_input_context_for_dispatch(
        task_id=owner.task_id,
        owner_id=owner.owner_id,
        workspace_id=owner.workspace_id,
        execution_decision_id=work.execution_decision_id,
        work_unit_id=work.work_unit_id,
    )
    original_plan = original_inputs.get("execution_plan_facts")
    matches = []
    for material in context["artifacts"]:
        if material.get("sourceCapabilityId") != "execution_plan_fact_assembly":
            continue
        blob = material.get("content")
        if not isinstance(blob, bytes) or sha256(blob).hexdigest() != material.get("contentSha256"):
            raise RepairInputFeedback("repair_original_plan_artifact_integrity_invalid")
        try:
            value = json.loads(blob)
        except (ValueError, UnicodeDecodeError) as exc:
            raise RepairInputFeedback("repair_original_plan_artifact_invalid") from exc
        if value == original_plan:
            matches.append(material)
    if len(matches) != 1:
        raise RepairInputFeedback("repair_original_plan_producer_ambiguous_or_missing")
    producer_call = calls.get(matches[0].get("sourceCapabilityCallId"))
    producer = next(
        (
            row
            for row in task.work_units
            if producer_call is not None and row.work_unit_id == producer_call.work_unit_id
        ),
        None,
    )
    if producer is None or producer.research_design_version != work.research_design_version:
        raise RepairInputFeedback("repair_original_source_design_mismatch")
    result = _required(producer.resolved_parameters, ["source_package", "reference_metrics"])
    result["research_goal"] = context["canonicalUserQuery"]
    return result
