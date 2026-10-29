"""Registered Skill entry using the original frozen WorkUnit and Dispatch owner."""

import json
import os
from collections.abc import Mapping

from app.execution.research_internal_skill_call_store import InternalExecutionOwner
from app.modules.research_orchestration_repository import ResearchOrchestrationRepository

from adapter.formal_bridge import formal_repair_invoker, frozen_repair_tools
from adapter.input_materialization import resolve_repair_source_context
from adapter.input_repair import (
    RepairImplementationError,
    run_input_repair_controller,
)


def read_private_execution_owner():
    raw = os.environ.get("OPENAIR_INTERNAL_SKILL_EXECUTION_OWNER")
    try:
        value = json.loads(raw) if raw else None
    except (TypeError, ValueError) as exc:
        raise RepairImplementationError("repair_private_owner_invalid") from exc
    fields = set(InternalExecutionOwner.__dataclass_fields__)
    if not isinstance(value, dict) or set(value) != fields:
        raise RepairImplementationError("repair_private_owner_invalid")
    if type(value["attempt_number"]) is not int or value["attempt_number"] < 1:
        raise RepairImplementationError("repair_private_owner_invalid")
    if any(
        not isinstance(value[key], str) or not value[key].strip()
        for key in fields - {"attempt_number"}
    ):
        raise RepairImplementationError("repair_private_owner_invalid")
    return InternalExecutionOwner(**value)


def run_formal_repair_entry(payload, *, owner, repository):
    task = repository.load_read_only(
        task_id=owner.task_id, owner_id=owner.owner_id, workspace_id=owner.workspace_id
    )
    parent = next(
        (call for call in task.capability_calls if call.capability_call_id == owner.parent_call_id),
        None,
    )
    work = next(
        (
            unit
            for unit in task.work_units
            if parent is not None and unit.work_unit_id == parent.work_unit_id
        ),
        None,
    )
    if work is None or not isinstance(work.resolved_parameters, Mapping):
        raise RepairImplementationError("repair_frozen_work_input_missing")
    original_inputs = dict(work.resolved_parameters)
    if not isinstance(payload, Mapping) or dict(payload) != original_inputs:
        raise RepairImplementationError("repair_frozen_work_input_mismatch")
    decision = next(
        (
            row
            for row in task.execution_decisions
            if row.execution_decision_id == work.execution_decision_id
        ),
        None,
    )
    if decision is None:
        raise RepairImplementationError("repair_original_decision_missing")
    snapshot = repository.load_registry_snapshot(decision.registry_snapshot_ref.content_hash)
    definitions = [
        row
        for row in snapshot["definitions"]["skills"]
        if row["skillId"] == parent.capability_id
        and row["definitionVersion"] == work.executor_ref.definition_version
    ]
    if len(definitions) != 1:
        raise RepairImplementationError("repair_parent_definition_ambiguous")
    tools, limit = frozen_repair_tools(snapshot, definitions[0], work)
    source_context = resolve_repair_source_context(
        repository, owner=owner, original_inputs=original_inputs
    )
    invoke = formal_repair_invoker(
        repository, owner=owner, original_inputs=original_inputs, source_context=source_context
    )
    return run_input_repair_controller(
        original_inputs, tools=tools, invoke=invoke, max_calls=limit, source_context=source_context
    )


def run_reproduction_execution_input_repair(payload):
    owner = read_private_execution_owner()
    return run_formal_repair_entry(
        payload, owner=owner, repository=ResearchOrchestrationRepository()
    )
