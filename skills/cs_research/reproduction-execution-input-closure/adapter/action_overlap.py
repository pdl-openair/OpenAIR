"""Skill-owned physical target overlap across original and internal calls."""

from collections.abc import Mapping

from app.execution.research_internal_skill_call import InternalSkillCallError


def _action_targets(skill_id, parameters):
    if not isinstance(parameters, Mapping):
        return None
    if skill_id == "environment_setup":
        # The existing installation operates in one execution workspace.
        return frozenset({("execution_environment",)})
    if skill_id == "repository_revision_acquisition":
        repository = parameters.get("repository")
        return frozenset({("repository", repository)}) if isinstance(repository, str) else None
    if skill_id != "resource_identity_acquisition":
        raise InternalSkillCallError("repair_external_action_not_declared")
    resources = parameters.get("resources")
    if not isinstance(resources, list) or not resources:
        return None
    targets = set()
    for resource in resources:
        if not isinstance(resource, Mapping):
            return None
        kind, repo_type, repo_id = (
            resource.get(key) for key in ("resource_kind", "repo_type", "repo_id")
        )
        if not all(isinstance(value, str) and value for value in (kind, repo_type, repo_id)):
            return None
        # Snapshots cover all files, so a file request to the same repo overlaps.
        targets.add(("resource", repo_type, repo_id))
    return frozenset(targets)


def require_action_target_available(request, task):
    """Observe canonical calls under the caller's Task lock; never execute I/O.

    Pins, parameters, Binding versions and parent identities are deliberately
    absent from target identity: changing them cannot evade an unknown action.
    """
    skill_id = request["skillId"]
    targets = _action_targets(skill_id, request["parameters"])
    if targets is None:
        raise InternalSkillCallError("repair_action_target_unresolved")
    work_by_id = {unit.work_unit_id: unit for unit in task.work_units}
    for call in task.capability_calls:
        if call.capability_id != skill_id or call.capability_call_id == request["capabilityCallId"]:
            continue
        status = getattr(call.status, "value", call.status)
        if status not in {"queued", "running", "external_result_unknown"}:
            continue
        frozen = call.internal_call_input_snapshot
        if frozen is not None:
            parameters = frozen.parameters
        else:
            unit = work_by_id.get(call.work_unit_id)
            parameters = unit.resolved_parameters if unit is not None else None
        previous_targets = _action_targets(skill_id, parameters)
        if previous_targets is None or targets.intersection(previous_targets):
            reason = "unknown" if status == "external_result_unknown" else "active"
            raise InternalSkillCallError(f"repair_{reason}_action_requires_reconciliation")
