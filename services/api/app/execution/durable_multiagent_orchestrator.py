"""Server-owned, synchronous execution loop for frozen multi-Agent plans.

The loop has no provider, environment, or routing dependency.  It relies on
the durable collaboration repository to fence every mutation before a caller
supplied execution adapter is allowed to make one external request.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import logging
from typing import Any, Callable

from app.modules.durable_execution import DurableExecutionError
from app.modules.controller_command_repository import ControllerCommandRepository
from app.modules.material_memory import build_specialist_context_envelope
from app.execution.durable_orchestrator_runtime import frozen_artifact_contract_from_snapshot


_logger = logging.getLogger("openair.durable_orchestrator")


class ExternalResultUnknown(RuntimeError):
    """The adapter cannot establish whether its one external request ran."""


ExecuteAgentUnit = Callable[[dict[str, Any]], dict[str, Any]]
PreflightAgentUnit = Callable[[dict[str, Any]], dict[str, Any] | None]
PersistArtifact = Callable[[str, dict[str, Any]], str]
ExecuteSkillUnit = Callable[[dict[str, Any]], dict[str, Any]]


class DurableMultiAgentOrchestrator:
    """Execute a persisted frozen work-unit graph without local recovery state."""

    def __init__(
        self,
        *,
        collaboration: Any,
        execute_agent_unit: ExecuteAgentUnit,
        persist_artifact: PersistArtifact,
        execute_skill_unit: ExecuteSkillUnit | None = None,
        preflight_agent_unit: PreflightAgentUnit | None = None,
        controller_commands: Any | None = None,
    ) -> None:
        self._collaboration = collaboration
        self._execute_agent_unit = execute_agent_unit
        self._persist_artifact = persist_artifact
        self._execute_skill_unit = execute_skill_unit
        self._preflight_agent_unit = preflight_agent_unit
        self._controller_commands = controller_commands or ControllerCommandRepository()

    def run(self, *, frozen_run: dict[str, Any], persisted_plan: dict[str, Any], worker_id: str, lease_epoch: int) -> dict[str, Any]:
        """Persist and execute the frozen plan once, stopping on any durable terminal state."""
        context = _required_mapping(persisted_plan.get("context"), "plan.context")
        run_id = _required_text(context.get("runId"), "plan.context.runId")
        if run_id != _required_text(frozen_run.get("run_id"), "run.run_id"):
            raise ValueError("frozen run and persisted plan identify different runs")
        task_id = _required_text(context.get("taskId"), "plan.context.taskId")
        if task_id != _required_text(frozen_run.get("task_id"), "run.task_id"):
            raise ValueError("frozen run and persisted plan identify different tasks")
        owner_id = _required_text(context.get("ownerId"), "plan.context.ownerId")
        if owner_id != _required_text(frozen_run.get("owner_id"), "run.owner_id"):
            raise ValueError("frozen run and persisted plan identify different owners")
        workspace_id = _required_text(context.get("workspaceId"), "plan.context.workspaceId")
        if workspace_id != _required_text(frozen_run.get("workspace_id"), "run.workspace_id"):
            raise ValueError("frozen run and persisted plan identify different workspaces")
        work_specs = _work_specs(persisted_plan.get("workUnits"))
        if not work_specs:
            # The confirmed plan did not authorize a specialist.  This is a
            # planning block, not a reason to invent a provider request.
            return _outcome([], terminal=True, state="blocked")
        existing_by_key = {
            row["unit_key"]: row
            for row in self._collaboration.list_work_units(
                run_id=run_id, owner_id=owner_id, workspace_id=workspace_id,
            )
        }
        artifacts: list[str] = []
        previous_unit: dict[str, Any] | None = None
        for spec in work_specs:
            try:
                if previous_unit is None:
                    unit = self._load_or_create_unit(
                        spec=spec,
                        existing_by_key=existing_by_key,
                        parent=None,
                        input_artifact_refs=spec["inputArtifactRefs"],
                        run_id=run_id,
                        owner_id=owner_id,
                        workspace_id=workspace_id,
                        worker_id=worker_id,
                        lease_epoch=lease_epoch,
                        boundary_kind="initial_dispatch",
                    )
                else:
                    upstream_artifacts = _refs(previous_unit.get("output_artifact_refs"))
                    if not upstream_artifacts:
                        return _outcome(artifacts, terminal=True, state="failed")
                    unit = self._load_or_create_unit(
                        spec=spec,
                        existing_by_key=existing_by_key,
                        parent=previous_unit,
                        input_artifact_refs=upstream_artifacts,
                        run_id=run_id,
                        owner_id=owner_id,
                        workspace_id=workspace_id,
                        worker_id=worker_id,
                        lease_epoch=lease_epoch,
                        boundary_kind="handoff_dispatch",
                    )
                    if unit.get("dispatch_paused"):
                        return _outcome(artifacts, terminal=True, state="awaiting_human")
                    self._ensure_handoff(
                        source=previous_unit,
                        target=unit,
                        run_id=run_id,
                        owner_id=owner_id,
                        workspace_id=workspace_id,
                        worker_id=worker_id,
                        lease_epoch=lease_epoch,
                        artifact_ids=upstream_artifacts,
                    )
            except DurableExecutionError as error:
                if error.detail in {"stop_won", "stale_or_expired_lease"}:
                    return _outcome(artifacts, terminal=True, state="stopped_by_user")
                raise
            state = str(unit.get("state") or "")
            if state == "succeeded":
                artifacts.extend(_refs(unit.get("output_artifact_refs")))
                previous_unit = unit
                continue
            if state in {"failed", "cancelled", "unknown"}:
                terminal_state = {
                    "cancelled": "stopped_by_user",
                    "unknown": "external_result_unknown",
                }.get(state, state)
                return _outcome(artifacts, terminal=True, state=terminal_state)
            try:
                outcome = self._execute_unit(
                    spec=spec,
                    unit=unit,
                    frozen_run=frozen_run,
                    persisted_plan=persisted_plan,
                    run_id=run_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    worker_id=worker_id,
                    lease_epoch=lease_epoch,
                )
            except DurableExecutionError as error:
                if error.detail in {"stop_won", "stale_or_expired_lease"}:
                    _logger.info(
                        "durable_orchestrator unit_stopped runId=%s unitKey=%s agentId=%s reason=%s",
                        run_id,
                        spec.get("unitKey"),
                        spec.get("agentId"),
                        error.detail,
                    )
                    return _outcome(artifacts, terminal=True, state="stopped_by_user")
                raise
            artifacts.extend(outcome["artifactIds"])
            if outcome["terminal"]:
                _logger.warning(
                    "durable_orchestrator unit_terminal runId=%s unitKey=%s agentId=%s "
                    "workUnitId=%s state=%s artifactCount=%s",
                    run_id,
                    spec.get("unitKey"),
                    spec.get("agentId"),
                    unit.get("work_unit_id") or unit.get("workUnitId"),
                    outcome.get("state"),
                    len(outcome.get("artifactIds") or ()),
                )
                return _outcome(artifacts, terminal=True, state=outcome["state"])
            previous_unit = next(
                row for row in self._collaboration.list_work_units(
                    run_id=run_id, owner_id=owner_id, workspace_id=workspace_id,
                )
                if row.get("unit_key") == spec["unitKey"]
            )
        return _outcome(artifacts, terminal=True, state="succeeded")

    def _load_or_create_unit(
        self,
        *,
        spec: dict[str, Any],
        existing_by_key: dict[str, dict[str, Any]],
        parent: dict[str, Any] | None,
        input_artifact_refs: list[str],
        run_id: str,
        owner_id: str,
        workspace_id: str,
        worker_id: str,
        lease_epoch: int,
        boundary_kind: str,
    ) -> dict[str, Any]:
        parent_key = spec.get("parentUnitKey")
        if bool(parent_key) != bool(parent):
            raise ValueError("work unit parent must precede its child")
        expected_parent_id = parent.get("work_unit_id") if parent else None
        expected_inputs = _refs(input_artifact_refs)
        unit = existing_by_key.get(spec["unitKey"])
        if unit:
            if (
                unit.get("parent_work_unit_id") != expected_parent_id
                or unit.get("agent_id") != spec["agentId"]
                or unit.get("kind") != spec["kind"]
                or _refs(unit.get("input_artifact_refs")) != expected_inputs
            ):
                raise DurableExecutionError(409, "work_unit_idempotency_conflict")
            if boundary_kind != "handoff_dispatch":
                return unit
        unit = self._collaboration.create_work_unit(
            run_id=run_id, owner_id=owner_id, workspace_id=workspace_id,
            worker_id=worker_id, fence_epoch=lease_epoch, unit_key=spec["unitKey"],
            parent_work_unit_id=expected_parent_id, agent_id=spec["agentId"], kind=spec["kind"],
            title=_title(spec), stage_key=spec.get("stageKey"), substage_key=spec.get("substageKey"),
            model_profile=spec["modelMode"], objective=spec["objective"], visibility="main",
            input_memory_refs=spec["inputMemoryRefs"], input_artifact_refs=expected_inputs,
            boundary_kind=boundary_kind,
            enforce_execution_policy=boundary_kind == "handoff_dispatch",
        )
        existing_by_key[spec["unitKey"]] = unit
        return unit

    def _execute_unit(
        self,
        *,
        spec: dict[str, Any],
        unit: dict[str, Any],
        frozen_run: dict[str, Any],
        persisted_plan: dict[str, Any],
        run_id: str,
        owner_id: str,
        workspace_id: str,
        worker_id: str,
        lease_epoch: int,
    ) -> dict[str, Any]:
        unit_id = _required_text(unit.get("work_unit_id"), "persisted work unit id")
        self._collaboration.transition_work_unit(
            work_unit_id=unit_id, owner_id=owner_id, workspace_id=workspace_id,
            worker_id=worker_id, fence_epoch=lease_epoch, state="running",
        )
        controller_inputs = []
        if spec["kind"] == "specialist":
            controller_inputs = self._controller_commands.consume_at_work_unit_boundary(
                task_id=_required_text(frozen_run.get("task_id"), "run.task_id"), owner_id=owner_id,
                workspace_id=workspace_id, run_id=run_id, work_unit_id=unit_id,
                worker_id=worker_id, fence_epoch=lease_epoch,
            )
        request = self._request_spec(
            spec=spec, unit=unit, frozen_run=frozen_run, persisted_plan=persisted_plan,
            run_id=run_id, owner_id=owner_id, workspace_id=workspace_id, controller_inputs=controller_inputs,
        )
        if spec["kind"] == "skill_route":
            return self._execute_frozen_skill_route(
                request=request, unit_id=unit_id, run_id=run_id,
                owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, lease_epoch=lease_epoch,
            )
        idempotency_key = _fingerprint({"runId": run_id, "unitKey": spec["unitKey"], "kind": "agent_brain"})
        request_fingerprint = _fingerprint(request)
        call = self._collaboration.create_capability_invocation(
            run_id=run_id, owner_id=owner_id, workspace_id=workspace_id,
            worker_id=worker_id, fence_epoch=lease_epoch, work_unit_id=unit_id,
            requesting_agent_id=spec["agentId"], invocation_kind="agent_brain",
            idempotency_key=idempotency_key, request_fingerprint=request_fingerprint,
            input_memory_refs=request["inputMemoryRefs"], input_artifact_refs=request["inputArtifactRefs"],
        )
        call_id = _required_text(call.get("call_id"), "persisted invocation id")
        call_state = str(call.get("state") or "")
        if call_state == "succeeded":
            artifact_ids = _refs(call.get("output_artifact_refs"))
            if not artifact_ids:
                recovered_skill = self._collaboration.find_recoverable_requested_skill_call(
                    run_id=run_id, owner_id=owner_id, workspace_id=workspace_id,
                    work_unit_id=unit_id, requesting_agent_id=spec["agentId"],
                )
                if recovered_skill is not None:
                    return self._recover_requested_skill(
                        request=request, unit_id=unit_id, run_id=run_id, owner_id=owner_id,
                        workspace_id=workspace_id, worker_id=worker_id, lease_epoch=lease_epoch,
                        skill_call=recovered_skill,
                    )
            return self._review_specialist_artifacts(
                source_request=request, source_spec=spec, source_unit=unit,
                source_summary=_required_text(call.get("result_summary") or "Specialist completed this research item.", "agent result summary"),
                artifact_ids=artifact_ids, run_id=run_id, owner_id=owner_id,
                workspace_id=workspace_id, worker_id=worker_id, lease_epoch=lease_epoch,
            )
        if call_state in {"failed", "blocked", "unknown"}:
            return _outcome([], terminal=True, state="external_result_unknown" if call_state == "unknown" else "failed")
        persisted_artifact_ids = _refs(call.get("output_artifact_refs"))
        if call.get("external_action_started") and persisted_artifact_ids:
            finished = self._collaboration.finish_capability_invocation(
                call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch, state="succeeded",
                summary=_required_text(call.get("result_summary") or "专业智能体 已完成本项研究。", "agent result summary"),
                output_artifact_refs=persisted_artifact_ids,
            )
            return self._review_specialist_artifacts(
                source_request=request, source_spec=spec, source_unit=unit,
                source_summary=_required_text(finished.get("result_summary") or "Specialist completed this research item.", "agent result summary"),
                artifact_ids=persisted_artifact_ids, run_id=run_id, owner_id=owner_id,
                workspace_id=workspace_id, worker_id=worker_id, lease_epoch=lease_epoch,
            )

        if self._preflight_agent_unit is not None:
            preflight_failure = self._preflight_agent_unit(request)
            if preflight_failure is not None:
                summary = _required_text(
                    preflight_failure.get("summary") if isinstance(preflight_failure, dict) else None,
                    "provider preflight summary",
                )
                self._collaboration.finish_capability_invocation(
                    call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
                    worker_id=worker_id, fence_epoch=lease_epoch, state="failed", summary=summary,
                    output_artifact_refs=[],
                    failure_classification=_failure_classification(preflight_failure),
                )
                self._collaboration.transition_work_unit(
                    work_unit_id=unit_id, owner_id=owner_id, workspace_id=workspace_id,
                    worker_id=worker_id, fence_epoch=lease_epoch, state="failed", summary=summary,
                )
                return _outcome([], terminal=True, state="failed")

        begun, already_started = self._collaboration.begin_capability_invocation(
            call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
            worker_id=worker_id, fence_epoch=lease_epoch,
            idempotency_key=idempotency_key, request_fingerprint=request_fingerprint,
        )
        if already_started:
            # A process may have died after the callback was dispatched.  The
            # outcome is unknowable, so no second provider request is allowed.
            self._collaboration.mark_capability_result_unknown(
                call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch,
            )
            return _outcome([], terminal=True, state="external_result_unknown")

        request["providerEvidence"] = {
            "executionDecisionId": run_id,
            "workUnitId": unit_id,
            "capabilityCallId": call_id,
            "dispatchId": f"dispatch:{call_id}",
            "attemptNumber": max(1, int(begun.get("attempt_number") or 1)),
            "canonicalPayloadFingerprint": request_fingerprint,
        }
        _logger.info(
            "durable_orchestrator specialist_execute_start runId=%s workUnitId=%s agentId=%s "
            "capabilityCallId=%s artifactContract=%s",
            run_id,
            unit_id,
            spec.get("agentId"),
            call_id,
            request.get("artifactContract") or spec.get("artifactContract"),
        )
        try:
            result = self._execute_agent_unit(request)
        except ExternalResultUnknown:
            _logger.warning(
                "durable_orchestrator specialist_external_unknown runId=%s workUnitId=%s "
                "agentId=%s capabilityCallId=%s",
                run_id,
                unit_id,
                spec.get("agentId"),
                call_id,
            )
            self._collaboration.mark_capability_result_unknown(
                call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch,
            )
            return _outcome([], terminal=True, state="external_result_unknown")
        if not isinstance(result, dict):
            raise ValueError("agent execution callback must return a mapping")
        result_state = str(result.get("state") or "succeeded")
        summary = _required_text(result.get("summary") or "专业智能体 未提供面向用户的摘要。", "agent result summary")
        if result_state == "unknown" or result.get("externalResultUnknown") is True:
            _logger.warning(
                "durable_orchestrator specialist_result_unknown runId=%s workUnitId=%s "
                "agentId=%s capabilityCallId=%s",
                run_id,
                unit_id,
                spec.get("agentId"),
                call_id,
            )
            self._collaboration.mark_capability_result_unknown(
                call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch,
            )
            return _outcome([], terminal=True, state="external_result_unknown")
        if result_state != "succeeded":
            failure_classification = _failure_classification(result)
            _logger.warning(
                "durable_orchestrator specialist_failed runId=%s workUnitId=%s agentId=%s "
                "capabilityCallId=%s state=%s failureClassification=%s summary=%s",
                run_id,
                unit_id,
                spec.get("agentId"),
                call_id,
                result_state,
                failure_classification or "",
                summary[:240],
            )
            self._collaboration.finish_capability_invocation(
                call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch, state="failed", summary=summary,
                output_artifact_refs=[],
                failure_classification=failure_classification,
            )
            self._collaboration.transition_work_unit(
                work_unit_id=unit_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch, state="failed", summary=summary,
            )
            return _outcome([], terminal=True, state="failed")

        skill_request = _authorized_skill_request(result, request)
        if skill_request is not None:
            skill_idempotency_key, _, skill_fingerprint = _requested_skill_dispatch(
                request=request, run_id=run_id, unit_id=unit_id,
                owner_id=owner_id, workspace_id=workspace_id, skill_request=skill_request,
            )
            persisted = self._collaboration.finish_agent_and_create_skill_call(
                call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch, summary=summary,
                skill_id=skill_request["skillId"], skill_idempotency_key=skill_idempotency_key,
                skill_request_fingerprint=skill_fingerprint, skill_request=skill_request,
                input_memory_refs=skill_request["inputMemoryRefs"],
                input_artifact_refs=skill_request["inputArtifactRefs"],
            )
            return self._execute_requested_skill(
                request=request, unit_id=unit_id, run_id=run_id, owner_id=owner_id,
                workspace_id=workspace_id, worker_id=worker_id, lease_epoch=lease_epoch,
                skill_request=skill_request, persisted_call=persisted["skillCall"],
            )

        artifact_id = _required_text(self._persist_artifact(call_id, result), "persisted artifact id")
        finished = self._collaboration.finish_capability_invocation(
            call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
            worker_id=worker_id, fence_epoch=lease_epoch, state="succeeded", summary=summary,
            output_artifact_refs=[artifact_id],
        )
        return self._review_specialist_artifacts(
            source_request=request, source_spec=spec, source_unit=unit, source_summary=summary,
            artifact_ids=[artifact_id], run_id=run_id, owner_id=owner_id,
            workspace_id=workspace_id, worker_id=worker_id, lease_epoch=lease_epoch,
        )

    def _review_specialist_artifacts(
        self,
        *,
        source_request: dict[str, Any],
        source_spec: dict[str, Any],
        source_unit: dict[str, Any],
        source_summary: str,
        artifact_ids: list[str],
        run_id: str,
        owner_id: str,
        workspace_id: str,
        worker_id: str,
        lease_epoch: int,
    ) -> dict[str, Any]:
        """Require one distinct result-review Agent before a source unit can complete."""
        source_unit_id = _required_text(source_unit.get("work_unit_id"), "review source work unit id")
        reviewer_id = _required_text(source_spec["mandatoryReviewContract"].get("reviewerAgentId"), "mandatory review agent")
        if source_spec["agentId"] == reviewer_id:
            raise DurableExecutionError(409, "independent_reviewer_required")
        review_key = f"review:{source_spec['unitKey']}"
        review_unit = next(
            (row for row in self._collaboration.list_work_units(
                run_id=run_id, owner_id=owner_id, workspace_id=workspace_id,
            ) if row.get("unit_key") == review_key),
            None,
        )
        if review_unit is None:
            review_unit = self._collaboration.create_work_unit(
                run_id=run_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch, unit_key=review_key,
                parent_work_unit_id=source_unit_id, agent_id=reviewer_id, kind="review",
                title="Result reliability review", stage_key=source_spec.get("stageKey"),
                substage_key=source_spec.get("substageKey"), model_profile="configured_default",
                objective="Independently decide whether the specialist artifact is usable downstream.", visibility="main",
                input_memory_refs=source_spec["inputMemoryRefs"], input_artifact_refs=artifact_ids,
                boundary_kind="result_review",
            )
        review_unit_id = _required_text(review_unit.get("work_unit_id"), "review work unit id")
        review_state = str(review_unit.get("state") or "")
        if review_state == "unknown":
            return _outcome([], terminal=True, state="external_result_unknown")
        if review_state == "blocked":
            return _outcome(artifact_ids, terminal=True, state="awaiting_human")
        if review_state in {"failed", "cancelled"}:
            return _outcome(artifact_ids, terminal=True, state="failed")
        if review_state == "succeeded":
            completed = self._collaboration.complete_specialist_after_review(
                work_unit_id=source_unit_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch, artifact_refs=artifact_ids,
                summary=source_summary,
            )
            return _outcome(_refs(completed.get("output_artifact_refs")), terminal=False, state="running")
        if review_state == "queued":
            self._collaboration.transition_work_unit(
                work_unit_id=review_unit_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch, state="running",
            )
        review_materials = _validated_review_materials(
            self._collaboration.read_review_materials(
                run_id=run_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
                artifact_refs=artifact_ids,
            ),
            artifact_ids,
        )
        review_request = {
            **source_request,
            "phase": "review_artifact",
            "agentId": source_spec["mandatoryReviewContract"]["reviewerAgentId"],
            "kind": "review",
            "unitKey": review_key,
            "selectedSkillIds": [],
            "reviewedAgentId": source_spec["agentId"],
            "sourceWorkUnitId": source_unit_id,
            "inputArtifactRefs": list(artifact_ids),
            "reviewMaterials": review_materials,
            "reviewContract": dict(source_spec["mandatoryReviewContract"]),
            "reviewedArtifactContract": _frozen_contract_spec(source_spec),
            **_frozen_contract_spec(source_spec["mandatoryReviewContract"]),
        }
        review_key_fingerprint = _fingerprint({
            "runId": run_id, "reviewUnitKey": review_key, "artifactIds": artifact_ids,
            "kind": "independent_result_review",
        })
        review_call = self._collaboration.create_capability_invocation(
            run_id=run_id, owner_id=owner_id, workspace_id=workspace_id,
            worker_id=worker_id, fence_epoch=lease_epoch, work_unit_id=review_unit_id,
            requesting_agent_id=reviewer_id, invocation_kind="review",
            idempotency_key=review_key_fingerprint, request_fingerprint=_fingerprint(review_request),
            input_memory_refs=source_spec["inputMemoryRefs"], input_artifact_refs=artifact_ids,
        )
        review_call_id = _required_text(review_call.get("call_id"), "review invocation id")
        state = str(review_call.get("state") or "")
        if state == "unknown":
            return _outcome([], terminal=True, state="external_result_unknown")
        if state in {"failed", "blocked", "cancelled"}:
            return _outcome(artifact_ids, terminal=True, state="awaiting_human")
        if state == "succeeded":
            completed = self._collaboration.complete_specialist_after_review(
                work_unit_id=source_unit_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch, artifact_refs=artifact_ids,
                summary=source_summary,
            )
            return _outcome(_refs(completed.get("output_artifact_refs")), terminal=False, state="running")
        if self._preflight_agent_unit is not None:
            preflight_failure = self._preflight_agent_unit(review_request)
            if preflight_failure is not None:
                summary = _required_text(
                    preflight_failure.get("summary") if isinstance(preflight_failure, dict) else None,
                    "provider preflight summary",
                )
                self._collaboration.finish_capability_invocation(
                    call_id=review_call_id, owner_id=owner_id, workspace_id=workspace_id,
                    worker_id=worker_id, fence_epoch=lease_epoch, state="failed", summary=summary,
                    output_artifact_refs=[], failure_classification=_failure_classification(preflight_failure),
                )
                self._collaboration.transition_work_unit(
                    work_unit_id=review_unit_id, owner_id=owner_id, workspace_id=workspace_id,
                    worker_id=worker_id, fence_epoch=lease_epoch, state="failed", summary=summary,
                )
                return _outcome(artifact_ids, terminal=True, state="failed")
        begun, already_started = self._collaboration.begin_capability_invocation(
            call_id=review_call_id, owner_id=owner_id, workspace_id=workspace_id,
            worker_id=worker_id, fence_epoch=lease_epoch,
            idempotency_key=review_key_fingerprint, request_fingerprint=_fingerprint(review_request),
        )
        if already_started:
            self._collaboration.mark_capability_result_unknown(
                call_id=review_call_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch,
            )
            return _outcome([], terminal=True, state="external_result_unknown")
        review_request["providerEvidence"] = {
            "executionDecisionId": run_id,
            "workUnitId": review_unit_id,
            "capabilityCallId": review_call_id,
            "dispatchId": f"dispatch:{review_call_id}",
            "attemptNumber": max(1, int(begun.get("attempt_number") or 1)),
            "canonicalPayloadFingerprint": _fingerprint(review_request),
        }
        try:
            reviewed = self._execute_agent_unit(review_request)
        except ExternalResultUnknown:
            self._collaboration.mark_capability_result_unknown(
                call_id=review_call_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch,
            )
            return _outcome([], terminal=True, state="external_result_unknown")
        try:
            review_result = _strict_review_result(reviewed)
        except DurableExecutionError:
            summary = "The independent reviewer returned an invalid review conclusion."
            _logger.warning(
                "durable_orchestrator review_invalid runId=%s reviewUnitId=%s sourceUnitId=%s "
                "capabilityCallId=%s",
                run_id,
                review_unit_id,
                source_unit_id,
                review_call_id,
            )
            self._collaboration.finish_capability_invocation(
                call_id=review_call_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch, state="failed", summary=summary,
                output_artifact_refs=[], failure_classification="provider_response_contract_invalid",
            )
            self._collaboration.transition_work_unit(
                work_unit_id=review_unit_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch, state="failed", summary=summary,
            )
            return _outcome(artifact_ids, terminal=True, state="failed")
        if review_result["verdict"] == "external_result_unknown":
            self._collaboration.mark_capability_result_unknown(
                call_id=review_call_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch,
            )
            return _outcome([], terminal=True, state="external_result_unknown")
        from app.modules.research_blocker_semantics import reviewer_disposition

        disposition = reviewer_disposition(
            verdict=review_result["verdict"],
            basis=review_result["basis"],
            issues=review_result["issues"],
            missing_inputs=review_result["missingInputs"],
            research_objective=source_request.get("objective"),
        )
        persisted = self._collaboration.finish_artifact_review(
            call_id=review_call_id, owner_id=owner_id, workspace_id=workspace_id,
            worker_id=worker_id, fence_epoch=lease_epoch, verdict=review_result["verdict"],
            basis=review_result["basis"], issues=review_result["issues"],
            missing_inputs=review_result["missingInputs"], artifact_refs=artifact_ids,
            continue_allowed=disposition.continue_allowed,
            disposition=disposition.kind,
            optional_input=disposition.optional_input,
        )
        if not disposition.continue_allowed:
            return _outcome(artifact_ids, terminal=True, state="awaiting_human")
        completed = self._collaboration.complete_specialist_after_review(
            work_unit_id=source_unit_id, owner_id=owner_id, workspace_id=workspace_id,
            worker_id=worker_id, fence_epoch=lease_epoch, artifact_refs=artifact_ids,
            summary=source_summary,
        )
        return _outcome(_refs(completed.get("output_artifact_refs")), terminal=False, state="running")

    def _execute_frozen_skill_route(
        self,
        *,
        request: dict[str, Any],
        unit_id: str,
        run_id: str,
        owner_id: str,
        workspace_id: str,
        worker_id: str,
        lease_epoch: int,
    ) -> dict[str, Any]:
        """Run an ordered frozen Skill list, then review all outputs once."""
        if self._execute_skill_unit is None:
            raise DurableExecutionError(409, "durable_skill_runtime_unavailable")
        artifact_ids: list[str] = []
        call_ids: list[str] = []
        for index, skill_id in enumerate(request["selectedSkillIds"], start=1):
            definition, binding = _selected_frozen_skill_authority(request, skill_id)
            skill_request = {
                "skillId": skill_id,
                "definitionVersion": definition["definitionVersion"],
                "snapshotDefinition": definition,
                "sourceBindingRefs": [_binding_ref(binding)],
                "selectedSkillBindings": [binding],
                "inputMemoryRefs": list(request["inputMemoryRefs"]),
                "inputArtifactRefs": list(request["inputArtifactRefs"]),
                "controllerInputContext": list(request["controllerInputContext"]),
                "logicalProviderId": request["logicalProviderId"],
                "modelProfile": request["modelMode"],
                "frozenGoal": request["frozenGoal"],
                "frozenConstraints": list(request["frozenConstraints"]),
                "parameters": {
                    "goal": request["frozenGoal"],
                    "task_instruction": request["objective"],
                },
                "reason": request["objective"],
                "idempotencyKey": f"frozen-skill-{index}",
            }
            idempotency_key = _fingerprint({
                "runId": run_id, "workUnitId": unit_id, "agentId": request["agentId"],
                "skillId": skill_id, "position": index, "kind": "skill_route",
            })
            skill_payload = {
                **skill_request,
                "runId": run_id,
                "taskId": request["taskId"],
                "ownerId": owner_id,
                "workspaceId": workspace_id,
                "workUnitId": unit_id,
                "requestingAgentId": request["agentId"],
            }
            fingerprint = _fingerprint(skill_payload)
            call = self._collaboration.create_skill_call(
                run_id=run_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch, work_unit_id=unit_id,
                requesting_agent_id=request["agentId"], skill_id=skill_id,
                idempotency_key=idempotency_key, request_fingerprint=fingerprint,
                input_memory_refs=skill_request["inputMemoryRefs"],
                input_artifact_refs=skill_request["inputArtifactRefs"],
            )
            call_id = _required_text(call.get("call_id"), "persisted Skill invocation id")
            call_ids.append(call_id)
            state = str(call.get("state") or "")
            if state == "unknown":
                return _outcome([], terminal=True, state="external_result_unknown")
            if state in {"failed", "blocked", "cancelled"}:
                return _outcome([], terminal=True, state="failed")
            current_artifacts = _refs(call.get("output_artifact_refs"))
            if call.get("external_action_started") and current_artifacts and call.get("result_summary"):
                completed = self._collaboration.finish_capability_invocation(
                    call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
                    worker_id=worker_id, fence_epoch=lease_epoch, state="succeeded",
                    summary=_required_text(call.get("result_summary"), "persisted Skill result summary"),
                    output_artifact_refs=current_artifacts,
                )
                current_artifacts = _refs(completed.get("output_artifact_refs"))
            elif state != "succeeded":
                begun, already_started = self._collaboration.begin_capability_invocation(
                    call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
                    worker_id=worker_id, fence_epoch=lease_epoch,
                    idempotency_key=idempotency_key, request_fingerprint=fingerprint,
                )
                if already_started:
                    self._collaboration.mark_capability_result_unknown(
                        call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
                        worker_id=worker_id, fence_epoch=lease_epoch,
                    )
                    return _outcome([], terminal=True, state="external_result_unknown")
                skill_payload["providerEvidence"] = {
                    "executionDecisionId": run_id,
                    "workUnitId": unit_id,
                    "capabilityCallId": call_id,
                    "dispatchId": f"dispatch:{call_id}",
                    "attemptNumber": max(1, int(begun.get("attempt_number") or 1)),
                    "canonicalPayloadFingerprint": fingerprint,
                }
                try:
                    skill_result = self._execute_skill_unit(skill_payload)
                except ExternalResultUnknown:
                    self._collaboration.mark_capability_result_unknown(
                        call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
                        worker_id=worker_id, fence_epoch=lease_epoch,
                    )
                    return _outcome([], terminal=True, state="external_result_unknown")
                if not isinstance(skill_result, dict):
                    raise ValueError("Skill execution callback must return a mapping")
                skill_state = str(skill_result.get("state") or "succeeded")
                summary = _required_text(
                    skill_result.get("summary") or "Skill did not provide a reviewable summary.",
                    "Skill result summary",
                )
                if skill_state == "unknown" or skill_result.get("externalResultUnknown") is True:
                    self._collaboration.mark_capability_result_unknown(
                        call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
                        worker_id=worker_id, fence_epoch=lease_epoch,
                    )
                    return _outcome([], terminal=True, state="external_result_unknown")
                if skill_state != "succeeded":
                    self._collaboration.finish_capability_invocation(
                        call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
                        worker_id=worker_id, fence_epoch=lease_epoch,
                        state="failed", summary=summary, output_artifact_refs=[],
                        failure_classification=_failure_classification(skill_result),
                    )
                    self._collaboration.transition_work_unit(
                        work_unit_id=unit_id, owner_id=owner_id, workspace_id=workspace_id,
                        worker_id=worker_id, fence_epoch=lease_epoch,
                        state="failed", summary=summary,
                    )
                    return _outcome([], terminal=True, state="failed")
                artifact_id = _required_text(
                    self._persist_artifact(call_id, {**skill_result, "artifactState": "partial_success"}),
                    "persisted Skill artifact id",
                )
                completed = self._collaboration.finish_capability_invocation(
                    call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
                    worker_id=worker_id, fence_epoch=lease_epoch,
                    state="succeeded", summary=summary, output_artifact_refs=[artifact_id],
                )
                current_artifacts = _refs(completed.get("output_artifact_refs"))
            artifact_ids.extend(current_artifacts)

        aggregate_request = {
            "skillId": "ordered_skill_route",
            "inputMemoryRefs": list(request["inputMemoryRefs"]),
            "inputArtifactRefs": list(request["inputArtifactRefs"]),
            "reason": request["objective"],
        }
        return self._review_skill_result(
            request=request, unit_id=unit_id, run_id=run_id, owner_id=owner_id,
            workspace_id=workspace_id, worker_id=worker_id, lease_epoch=lease_epoch,
            skill_request=aggregate_request,
            skill_call_id=_fingerprint({"orderedSkillCallIds": call_ids}),
            skill_artifact_ids=list(dict.fromkeys(artifact_ids)),
        )

    def _execute_requested_skill(
        self,
        *,
        request: dict[str, Any],
        unit_id: str,
        run_id: str,
        owner_id: str,
        workspace_id: str,
        worker_id: str,
        lease_epoch: int,
        skill_request: dict[str, Any],
        persisted_call: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run one authorized Skill, then require its requesting Agent to review it.

        Frozen plans currently provide no reusable per-unit Skill-call budget.
        Keeping this to one request/review round is therefore deliberate: it
        proves the durable boundary without inventing an unbounded loop.
        """
        if self._execute_skill_unit is None:
            raise DurableExecutionError(409, "durable_skill_runtime_unavailable")
        skill_id = _required_text(skill_request["skillId"], "skill request skillId")
        idempotency_key, skill_payload, fingerprint = _requested_skill_dispatch(
            request=request, run_id=run_id, unit_id=unit_id,
            owner_id=owner_id, workspace_id=workspace_id, skill_request=skill_request,
        )
        call = persisted_call or self._collaboration.create_skill_call(
            run_id=run_id, owner_id=owner_id, workspace_id=workspace_id,
            worker_id=worker_id, fence_epoch=lease_epoch, work_unit_id=unit_id,
            requesting_agent_id=request["agentId"], skill_id=skill_id,
            idempotency_key=idempotency_key, request_fingerprint=fingerprint,
            input_memory_refs=skill_request["inputMemoryRefs"], input_artifact_refs=skill_request["inputArtifactRefs"],
        )
        call_id = _required_text(call.get("call_id"), "persisted Skill invocation id")
        state = str(call.get("state") or "")
        if state == "unknown":
            return _outcome([], terminal=True, state="external_result_unknown")
        if state in {"failed", "blocked", "cancelled"}:
            return _outcome([], terminal=True, state="failed")
        artifact_ids = _refs(call.get("output_artifact_refs"))
        if call.get("external_action_started") and artifact_ids and call.get("result_summary"):
            completed = self._collaboration.finish_capability_invocation(
                call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch, state="succeeded",
                summary=_required_text(call.get("result_summary"), "persisted Skill result summary"),
                output_artifact_refs=artifact_ids,
            )
            artifact_ids = _refs(completed.get("output_artifact_refs"))
        elif state != "succeeded":
            begun, already_started = self._collaboration.begin_capability_invocation(
                call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch,
                idempotency_key=idempotency_key, request_fingerprint=fingerprint,
            )
            if already_started:
                self._collaboration.mark_capability_result_unknown(
                    call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
                    worker_id=worker_id, fence_epoch=lease_epoch,
                )
                return _outcome([], terminal=True, state="external_result_unknown")
            skill_payload["providerEvidence"] = {
                "executionDecisionId": run_id,
                "workUnitId": unit_id,
                "capabilityCallId": call_id,
                "dispatchId": f"dispatch:{call_id}",
                "attemptNumber": max(1, int(begun.get("attempt_number") or 1)),
                "canonicalPayloadFingerprint": fingerprint,
            }
            try:
                skill_result = self._execute_skill_unit(skill_payload)
            except ExternalResultUnknown:
                self._collaboration.mark_capability_result_unknown(
                    call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
                    worker_id=worker_id, fence_epoch=lease_epoch,
                )
                return _outcome([], terminal=True, state="external_result_unknown")
            if not isinstance(skill_result, dict):
                raise ValueError("Skill execution callback must return a mapping")
            skill_state = str(skill_result.get("state") or "succeeded")
            skill_summary = _required_text(skill_result.get("summary") or "Skill did not provide a reviewable summary.", "Skill result summary")
            if skill_state == "unknown" or skill_result.get("externalResultUnknown") is True:
                self._collaboration.mark_capability_result_unknown(
                    call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
                    worker_id=worker_id, fence_epoch=lease_epoch,
                )
                return _outcome([], terminal=True, state="external_result_unknown")
            if skill_state != "succeeded":
                self._collaboration.finish_capability_invocation(
                    call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
                    worker_id=worker_id, fence_epoch=lease_epoch, state="failed", summary=skill_summary,
                    output_artifact_refs=[],
                    failure_classification=_failure_classification(skill_result),
                )
                self._collaboration.transition_work_unit(
                    work_unit_id=unit_id, owner_id=owner_id, workspace_id=workspace_id,
                    worker_id=worker_id, fence_epoch=lease_epoch, state="failed", summary=skill_summary,
                )
                return _outcome([], terminal=True, state="failed")
            artifact_id = _required_text(
                self._persist_artifact(call_id, {**skill_result, "artifactState": "partial_success"}),
                "persisted Skill artifact id",
            )
            persisted_request_snapshot = str(call.get("result_summary") or "")
            durable_summary = (
                persisted_request_snapshot
                if persisted_request_snapshot.startswith(("skill-request.v1:", "skill-request.v2:"))
                else skill_summary
            )
            completed = self._collaboration.finish_capability_invocation(
                call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch, state="succeeded", summary=durable_summary,
                output_artifact_refs=[artifact_id],
            )
            artifact_ids = _refs(completed.get("output_artifact_refs"))
        return self._review_skill_result(
            request=request, unit_id=unit_id, run_id=run_id, owner_id=owner_id,
            workspace_id=workspace_id, worker_id=worker_id, lease_epoch=lease_epoch,
            skill_request=skill_request, skill_call_id=call_id, skill_artifact_ids=artifact_ids,
        )

    def _review_skill_result(
        self,
        *,
        request: dict[str, Any], unit_id: str, run_id: str, owner_id: str, workspace_id: str,
        worker_id: str, lease_epoch: int, skill_request: dict[str, Any], skill_call_id: str,
        skill_artifact_ids: list[str],
    ) -> dict[str, Any]:
        review_request = {
            **request,
            "phase": "review_skill_result",
            "skillResult": {
                "skillId": skill_request["skillId"], "skillCallId": skill_call_id,
                "artifactIds": list(skill_artifact_ids), "reason": skill_request["reason"],
            },
            "inputArtifactRefs": list(dict.fromkeys([*request["inputArtifactRefs"], *skill_artifact_ids])),
        }
        idempotency_key = _fingerprint({"runId": run_id, "workUnitId": unit_id, "skillCallId": skill_call_id, "kind": "same_agent_review"})
        fingerprint = _fingerprint(review_request)
        review = self._collaboration.create_capability_invocation(
            run_id=run_id, owner_id=owner_id, workspace_id=workspace_id,
            worker_id=worker_id, fence_epoch=lease_epoch, work_unit_id=unit_id,
            requesting_agent_id=request["agentId"], invocation_kind="review",
            idempotency_key=idempotency_key, request_fingerprint=fingerprint,
            input_memory_refs=skill_request["inputMemoryRefs"], input_artifact_refs=review_request["inputArtifactRefs"],
        )
        review_id = _required_text(review.get("call_id"), "persisted Skill review invocation id")
        review_state = str(review.get("state") or "")
        if review_state == "unknown":
            return _outcome([], terminal=True, state="external_result_unknown")
        if review_state in {"failed", "blocked", "cancelled"}:
            return _outcome([], terminal=True, state="failed")
        if review_state == "succeeded":
            output_ids = _refs(review.get("output_artifact_refs"))
            return self._audit_skill_outputs(
                request=request, unit_id=unit_id, run_id=run_id, owner_id=owner_id,
                workspace_id=workspace_id, worker_id=worker_id, lease_epoch=lease_epoch,
                review_call=review, artifact_ids=[*skill_artifact_ids, *output_ids],
            )
        persisted_review_artifacts = _refs(review.get("output_artifact_refs"))
        if review.get("external_action_started") and persisted_review_artifacts and review.get("result_summary"):
            finished = self._collaboration.finish_capability_invocation(
                call_id=review_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch, state="succeeded",
                summary=_required_text(review.get("result_summary"), "persisted Skill review summary"),
                output_artifact_refs=persisted_review_artifacts,
            )
            return self._audit_skill_outputs(
                request=request, unit_id=unit_id, run_id=run_id, owner_id=owner_id,
                workspace_id=workspace_id, worker_id=worker_id, lease_epoch=lease_epoch,
                review_call=finished, artifact_ids=[*skill_artifact_ids, *persisted_review_artifacts],
            )
        if self._preflight_agent_unit is not None:
            preflight_failure = self._preflight_agent_unit(review_request)
            if preflight_failure is not None:
                summary = _required_text(
                    preflight_failure.get("summary") if isinstance(preflight_failure, dict) else None,
                    "provider preflight summary",
                )
                self._collaboration.finish_capability_invocation(
                    call_id=review_id, owner_id=owner_id, workspace_id=workspace_id,
                    worker_id=worker_id, fence_epoch=lease_epoch, state="failed", summary=summary,
                    output_artifact_refs=[],
                )
                self._collaboration.transition_work_unit(
                    work_unit_id=unit_id, owner_id=owner_id, workspace_id=workspace_id,
                    worker_id=worker_id, fence_epoch=lease_epoch, state="failed", summary=summary,
                )
                return _outcome([], terminal=True, state="failed")
        begun, already_started = self._collaboration.begin_capability_invocation(
            call_id=review_id, owner_id=owner_id, workspace_id=workspace_id,
            worker_id=worker_id, fence_epoch=lease_epoch,
            idempotency_key=idempotency_key, request_fingerprint=fingerprint,
        )
        if already_started:
            self._collaboration.mark_capability_result_unknown(
                call_id=review_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch,
            )
            return _outcome([], terminal=True, state="external_result_unknown")
        review_request["providerEvidence"] = {
            "executionDecisionId": run_id,
            "workUnitId": unit_id,
            "capabilityCallId": review_id,
            "dispatchId": f"dispatch:{review_id}",
            "attemptNumber": max(1, int(begun.get("attempt_number") or 1)),
            "canonicalPayloadFingerprint": fingerprint,
        }
        try:
            reviewed = self._execute_agent_unit(review_request)
        except ExternalResultUnknown:
            self._collaboration.mark_capability_result_unknown(
                call_id=review_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch,
            )
            return _outcome([], terminal=True, state="external_result_unknown")
        if not isinstance(reviewed, dict):
            raise ValueError("Skill review callback must return a mapping")
        state = str(reviewed.get("state") or "succeeded")
        summary = _required_text(reviewed.get("summary") or "The requesting Agent did not review the Skill result.", "Skill review summary")
        if state == "unknown" or reviewed.get("externalResultUnknown") is True:
            self._collaboration.mark_capability_result_unknown(
                call_id=review_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch,
            )
            return _outcome([], terminal=True, state="external_result_unknown")
        if state != "succeeded" or reviewed.get("skillRequest") is not None:
            self._collaboration.finish_capability_invocation(
                call_id=review_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch, state="failed", summary=summary,
                output_artifact_refs=[],
            )
            self._collaboration.transition_work_unit(
                work_unit_id=unit_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch, state="failed", summary=summary,
            )
            return _outcome([], terminal=True, state="failed")
        artifact_id = _required_text(self._persist_artifact(review_id, reviewed), "persisted reviewed research artifact id")
        finished = self._collaboration.finish_capability_invocation(
            call_id=review_id, owner_id=owner_id, workspace_id=workspace_id,
            worker_id=worker_id, fence_epoch=lease_epoch, state="succeeded", summary=summary,
            output_artifact_refs=[artifact_id],
        )
        return self._audit_skill_outputs(
            request=request, unit_id=unit_id, run_id=run_id, owner_id=owner_id,
            workspace_id=workspace_id, worker_id=worker_id, lease_epoch=lease_epoch,
            review_call=finished, artifact_ids=[*skill_artifact_ids, artifact_id],
        )

    def _audit_skill_outputs(
        self,
        *,
        request: dict[str, Any],
        unit_id: str,
        run_id: str,
        owner_id: str,
        workspace_id: str,
        worker_id: str,
        lease_epoch: int,
        review_call: dict[str, Any],
        artifact_ids: list[str],
    ) -> dict[str, Any]:
        artifact_ids = list(dict.fromkeys(_refs(artifact_ids)))
        if not artifact_ids:
            raise DurableExecutionError(409, "skill_review_requires_output_artifact")
        source_unit = next(
            row for row in self._collaboration.list_work_units(
                run_id=run_id, owner_id=owner_id, workspace_id=workspace_id,
            )
            if row.get("work_unit_id") == unit_id
        )
        source_spec = {
            "unitKey": request["unitKey"],
            "agentId": request["agentId"],
            "inputMemoryRefs": list(request["inputMemoryRefs"]),
            "stageKey": request.get("stageKey"),
            "substageKey": request.get("substageKey"),
            "artifactContract": request["artifactContract"],
            "artifactContractSchemaVersion": request["artifactContractSchemaVersion"],
            "artifactRequiredFields": request["artifactRequiredFields"],
            "artifactSemanticVersion": request["artifactSemanticVersion"],
            "artifactSchemaDigest": request["artifactSchemaDigest"],
            "artifactSchema": request["artifactSchema"],
            "artifactSchemaRef": request["artifactSchemaRef"],
            "mandatoryReviewContract": request["mandatoryReviewContract"],
        }
        return self._review_specialist_artifacts(
            source_request=request, source_spec=source_spec, source_unit=source_unit,
            source_summary=_required_text(review_call.get("result_summary"), "Skill review summary"),
            artifact_ids=artifact_ids, run_id=run_id, owner_id=owner_id,
            workspace_id=workspace_id, worker_id=worker_id, lease_epoch=lease_epoch,
        )

    def _recover_requested_skill(
        self,
        *,
        request: dict[str, Any],
        unit_id: str,
        run_id: str,
        owner_id: str,
        workspace_id: str,
        worker_id: str,
        lease_epoch: int,
        skill_call: dict[str, Any],
    ) -> dict[str, Any]:
        skill_call_id = _required_text(skill_call.get("call_id"), "persisted Skill invocation id")
        state = str(skill_call.get("state") or "")
        if state == "unknown":
            return _outcome([], terminal=True, state="external_result_unknown")
        if state == "failed":
            self._collaboration.transition_work_unit(
                work_unit_id=unit_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch, state="failed",
                summary=_required_text(
                    skill_call.get("result_summary") or "The requested Skill could not be executed.",
                    "persisted Skill failure summary",
                ),
            )
            return _outcome([], terminal=True, state="failed")
        if state in {"blocked", "cancelled"}:
            return _outcome([], terminal=True, state="failed")
        artifact_ids = _refs(skill_call.get("output_artifact_refs"))
        try:
            skill_request = _persisted_skill_request(
                skill_call.get("result_summary"),
                frozen_request=request,
                require_frozen_authority=not artifact_ids,
            )
        except DurableExecutionError:
            summary = "The persisted Skill request no longer contains executable frozen authority."
            return self._fail_unstarted_requested_skill(
                call_id=skill_call_id, unit_id=unit_id, owner_id=owner_id,
                workspace_id=workspace_id, worker_id=worker_id,
                lease_epoch=lease_epoch, summary=summary,
            )
        if not artifact_ids:
            try:
                return self._execute_requested_skill(
                    request=request, unit_id=unit_id, run_id=run_id, owner_id=owner_id,
                    workspace_id=workspace_id, worker_id=worker_id, lease_epoch=lease_epoch,
                    skill_request=skill_request, persisted_call=skill_call,
                )
            except DurableExecutionError as error:
                if error.detail != "persisted_skill_authority_mismatch":
                    raise
                summary = "The persisted Skill request does not match the frozen route authority."
                return self._fail_unstarted_requested_skill(
                    call_id=skill_call_id, unit_id=unit_id, owner_id=owner_id,
                    workspace_id=workspace_id, worker_id=worker_id,
                    lease_epoch=lease_epoch, summary=summary,
                )
        summary = _required_text(skill_call.get("result_summary"), "persisted Skill result summary")
        if state != "succeeded":
            completed = self._collaboration.finish_capability_invocation(
                call_id=skill_call_id, owner_id=owner_id, workspace_id=workspace_id,
                worker_id=worker_id, fence_epoch=lease_epoch, state="succeeded", summary=summary,
                output_artifact_refs=artifact_ids,
            )
            artifact_ids = _refs(completed.get("output_artifact_refs"))
        return self._review_skill_result(
            request=request, unit_id=unit_id, run_id=run_id, owner_id=owner_id,
            workspace_id=workspace_id, worker_id=worker_id, lease_epoch=lease_epoch,
            skill_request=skill_request,
            skill_call_id=skill_call_id,
            skill_artifact_ids=artifact_ids,
        )

    def _fail_unstarted_requested_skill(
        self,
        *,
        call_id: str,
        unit_id: str,
        owner_id: str,
        workspace_id: str,
        worker_id: str,
        lease_epoch: int,
        summary: str,
    ) -> dict[str, Any]:
        self._collaboration.fail_unstarted_capability_invocation(
            call_id=call_id, owner_id=owner_id, workspace_id=workspace_id,
            worker_id=worker_id, fence_epoch=lease_epoch, summary=summary,
            failure_classification="skill_runtime_unavailable",
        )
        self._collaboration.transition_work_unit(
            work_unit_id=unit_id, owner_id=owner_id, workspace_id=workspace_id,
            worker_id=worker_id, fence_epoch=lease_epoch, state="failed", summary=summary,
        )
        return _outcome([], terminal=True, state="failed")

    def _ensure_handoff(self, *, source: dict[str, Any], target: dict[str, Any], run_id: str, owner_id: str, workspace_id: str, worker_id: str, lease_epoch: int, artifact_ids: list[str]) -> None:
        source_id = _required_text(source.get("work_unit_id"), "source work unit id")
        target_id = _required_text(target.get("work_unit_id"), "target work unit id")
        handoffs = self._collaboration.list_handoffs(run_id=run_id, owner_id=owner_id, workspace_id=workspace_id)
        if any(handoff.get("from_work_unit_id") == source_id and handoff.get("to_work_unit_id") == target_id for handoff in handoffs):
            return
        self._collaboration.handoff(
            from_work_unit_id=source_id, to_work_unit_id=target_id,
            owner_id=owner_id, workspace_id=workspace_id, worker_id=worker_id, fence_epoch=lease_epoch,
            summary="已将本项研究产物交接给后续专业智能体。",
            input_artifact_refs=artifact_ids, output_artifact_refs=artifact_ids,
        )

    def _request_spec(self, *, spec: dict[str, Any], unit: dict[str, Any], frozen_run: dict[str, Any], persisted_plan: dict[str, Any], run_id: str, owner_id: str, workspace_id: str, controller_inputs: list[str]) -> dict[str, Any]:
        handoffs = self._collaboration.list_handoffs(run_id=run_id, owner_id=owner_id, workspace_id=workspace_id)
        parent_handoffs = [
            handoff for handoff in handoffs
            if handoff.get("to_work_unit_id") == unit.get("work_unit_id")
        ]
        prior_artifacts = _refs(*(handoff.get("output_artifact_refs") for handoff in parent_handoffs))
        context_envelope = build_specialist_context_envelope(frozen_run=frozen_run, work_unit=unit)
        return {
            "runId": run_id,
            "taskId": _required_text(frozen_run.get("task_id"), "run.task_id"),
            "ownerId": owner_id,
            "workspaceId": workspace_id,
            "unitKey": spec["unitKey"],
            "agentId": spec["agentId"],
            "kind": spec["kind"],
            "objective": spec["objective"],
            "stageKey": spec.get("stageKey"),
            "substageKey": spec.get("substageKey"),
            "selectedSkillIds": list(spec["selectedSkillIds"]),
            "selectedSkillDefinitions": deepcopy(spec.get("selectedSkillDefinitions") or []),
            "selectedSkillBindings": deepcopy(spec.get("selectedSkillBindings") or []),
            "modelMode": spec.get("modelMode"),
            "artifactContract": spec["artifactContract"],
            "artifactContractSchemaVersion": spec["artifactContractSchemaVersion"],
            "artifactRequiredFields": list(spec["artifactRequiredFields"]),
            "artifactSemanticVersion": spec["artifactSemanticVersion"],
            "artifactSchemaDigest": spec["artifactSchemaDigest"],
            "artifactSchema": spec["artifactSchema"],
            "artifactSchemaRef": spec["artifactSchemaRef"],
            "mandatoryReviewContract": spec["mandatoryReviewContract"],
            # This is copied from the persisted plan, not selected by a
            # browser or a Skill. The live Skill adapter uses it to resolve
            # the same logical provider and frozen model profile as its work
            # unit without consulting a fallback chain.
            "logicalProviderId": _required_text(persisted_plan.get("selectedProvider"), "plan.selectedProvider"),
            "modelProfile": _required_text(persisted_plan.get("modelProfile"), "plan.modelProfile"),
            "frozenGoal": _frozen_goal(frozen_run),
            "frozenConstraints": _frozen_constraints(frozen_run),
            "controllerInputContext": [
                *(
                    _refs(_required_mapping(frozen_run.get("input_snapshot"), "run.input_snapshot").get("controllerInputContext"))
                    if _is_first_executable_work_unit(spec, persisted_plan) else []
                ),
                *controller_inputs,
            ],
            "inputMemoryRefs": _refs(spec["inputMemoryRefs"]),
            "contextEnvelope": context_envelope,
            "inputArtifactRefs": _refs(unit.get("input_artifact_refs")),
            "handoffSummaries": [_required_text(handoff.get("summary"), "durable handoff summary") for handoff in parent_handoffs],
            "planContext": dict(_required_mapping(persisted_plan.get("context"), "plan.context")),
        }


def _is_first_executable_work_unit(spec: dict[str, Any], persisted_plan: dict[str, Any]) -> bool:
    if spec.get("kind") not in {"specialist", "skill_route"}:
        return False
    work_units = _work_specs(persisted_plan.get("workUnits"))
    return bool(work_units) and spec.get("unitKey") == work_units[0].get("unitKey")


def _work_specs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("persisted plan workUnits must be a list")
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict) or raw.get("state") != "planned":
            raise ValueError("workUnits must be planned mappings")
        unit_key = _required_text(raw.get("unitKey"), "workUnit.unitKey")
        if unit_key in seen:
            raise ValueError("workUnits must have unique unitKey values")
        seen.add(unit_key)
        agent_id = _required_text(raw.get("agentId"), "workUnit.agentId")
        kind = _text(raw.get("kind"))
        if kind not in {"specialist", "skill_route"}:
            raise ValueError("workUnits must contain authorized specialist or Skill-route work")
        contract = _frozen_contract_spec(raw)
        review_contract = raw.get("mandatoryReviewContract")
        if not isinstance(review_contract, dict):
            raise ValueError("workUnit mandatory review contract is unavailable")
        _frozen_contract_spec(review_contract)
        reviewer_id = _required_text(review_contract.get("reviewerAgentId"), "mandatory review agent")
        selected_skill_ids = _refs(raw.get("selectedSkillIds"))
        selected_skill_definitions, selected_skill_bindings = _frozen_skill_authority(
            raw, selected_skill_ids
        )
        model_mode = _text(raw.get("modelMode"))
        if model_mode not in {"terra", "sol"}:
            raise ValueError("workUnit modelMode must be terra or sol")
        if kind == "skill_route":
            if not selected_skill_ids:
                raise ValueError("Skill-route work requires at least one durable executable Skill")
        specs.append({
            "unitKey": unit_key, "parentUnitKey": _text(raw.get("parentUnitKey")) or None,
            "stageKey": _text(raw.get("stageKey")) or None, "substageKey": _text(raw.get("substageKey")) or None,
            "agentId": agent_id,
            "kind": kind, "state": "planned",
            "objective": _required_text(raw.get("objective"), "workUnit.objective"),
            "inputMemoryRefs": _refs(raw.get("inputMemoryRefs")),
            "inputArtifactRefs": _refs(raw.get("inputArtifactRefs")),
            "selectedSkillIds": selected_skill_ids,
            "selectedSkillDefinitions": selected_skill_definitions,
            "selectedSkillBindings": selected_skill_bindings,
            "modelMode": model_mode,
            **contract,
            "mandatoryReviewContract": {**review_contract, "reviewerAgentId": reviewer_id},
        })
    return specs


def _frozen_contract_spec(raw: dict[str, Any]) -> dict[str, Any]:
    try:
        frozen = frozen_artifact_contract_from_snapshot(raw)
    except ValueError as exc:
        raise ValueError("workUnit frozen artifact contract is invalid") from exc
    fields = _refs(raw.get("artifactRequiredFields"))
    required = frozen.schema.get("required") if hasattr(frozen.schema, "get") else None
    if not isinstance(required, tuple) or fields != list(required):
        raise ValueError("workUnit required fields must match the frozen schema")
    if type(raw.get("artifactContractSchemaVersion")) is not int or raw["artifactContractSchemaVersion"] < 1:
        raise ValueError("workUnit artifact contract schema version is invalid")
    return {
        "artifactContract": frozen.contract_id,
        "artifactContractSchemaVersion": raw["artifactContractSchemaVersion"],
        "artifactRequiredFields": fields,
        **frozen.snapshot(),
    }


def _frozen_goal(run: dict[str, Any]) -> str:
    snapshot = _required_mapping(run.get("input_snapshot"), "run.input_snapshot")
    plan = snapshot.get("creationPlan")
    return _first_text((plan or {}).get("goalSummary") if isinstance(plan, dict) else None, snapshot.get("goalSummary"))


def _frozen_constraints(run: dict[str, Any]) -> list[str]:
    snapshot = _required_mapping(run.get("input_snapshot"), "run.input_snapshot")
    plan = snapshot.get("creationPlan")
    if not isinstance(plan, dict):
        return []
    return _refs(plan.get("clarifications"), ((plan.get("understanding") or {}).get("evidenceBoundaries") if isinstance(plan.get("understanding"), dict) else None))


def _outcome(artifact_ids: list[str], *, terminal: bool, state: str) -> dict[str, Any]:
    return {"artifactIds": list(dict.fromkeys(artifact_ids)), "terminal": terminal, "state": state}


def _failure_classification(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    classification = value.get("failureClassification")
    if classification in {
        "provider_configuration",
        "provider_rejected_request",
        "provider_response_contract_invalid",
        "skill_finalization_runtime_failed",
    }:
        return classification
    return None


def _strict_review_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DurableExecutionError(422, "invalid_artifact_review_result")
    wrapped = value.get("reviewVerdict")
    if wrapped is not None:
        if set(value) != {"reviewVerdict"} or not isinstance(wrapped, dict):
            raise DurableExecutionError(422, "invalid_artifact_review_result")
        value = wrapped
    verdict = str(value.get("verdict") or "").strip()
    if verdict == "rejected_or_needs_revision":
        verdict = "needs_revision"
    if verdict not in {"usable", "needs_revision", "needs_input", "insufficient_materials", "human_decision_required", "external_result_unknown"}:
        raise DurableExecutionError(422, "invalid_artifact_review_verdict")
    basis = _required_text(value.get("basis") or value.get("summary"), "artifact review basis")
    issues = value.get("issues") or []
    if not isinstance(issues, list):
        raise DurableExecutionError(422, "invalid_artifact_review_issues")
    normalized_issues: list[dict[str, str]] = []
    for issue in issues:
        if not isinstance(issue, dict):
            raise DurableExecutionError(422, "invalid_artifact_review_issues")
        normalized_issues.append({
            "code": _required_text(issue.get("code"), "artifact review issue code"),
            "severity": _required_text(issue.get("severity"), "artifact review issue severity"),
            "message": _required_text(issue.get("message"), "artifact review issue message"),
        })
    missing_inputs = _refs(value.get("missingInputs"))
    return {"verdict": verdict, "basis": basis, "issues": normalized_issues, "missingInputs": missing_inputs}


def _question_refinement_stage_sufficiency(
    review_materials: list[dict[str, Any]],
) -> bool | None:
    """Evaluate only whether question refinement is sufficient for the next stage.

    Missing downstream experiment/verification details are not blockers here.
    An explicit user decision remains blocking when it changes the core question
    candidate or current scope. ``None`` preserves compatibility when old review
    material cannot be parsed.
    """
    parsed_any = False
    for material in review_materials:
        content = material.get("content") if isinstance(material, dict) else None
        if not isinstance(content, str) or not content.strip():
            continue
        try:
            body = json.loads(content)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(body, dict) and isinstance(body.get("body"), dict):
            body = body["body"]
        if not isinstance(body, dict):
            continue
        parsed_any = True

        candidates = body.get("question_candidates")
        if not isinstance(candidates, list) or not candidates:
            return False
        has_core_question = any(
            isinstance(candidate, dict)
            and isinstance(candidate.get("research_question"), str)
            and bool(candidate["research_question"].strip())
            and isinstance(candidate.get("research_object"), str)
            and bool(candidate["research_object"].strip())
            and isinstance(candidate.get("outcome_or_decision"), str)
            and bool(candidate["outcome_or_decision"].strip())
            for candidate in candidates
        )
        if not has_core_question:
            return False

        scope = body.get("scope_boundary")
        included = scope.get("included_boundaries") if isinstance(scope, dict) else None
        if not isinstance(included, list) or not any(
            isinstance(boundary, dict)
            and isinstance(boundary.get("statement"), str)
            and bool(boundary["statement"].strip())
            for boundary in included
        ):
            return False

        evidence_needs = body.get("next_evidence_needs")
        if isinstance(evidence_needs, list):
            for need in evidence_needs:
                if not isinstance(need, dict):
                    continue
                if (
                    need.get("need_kind") == "user_clarification"
                    and need.get("blocks_readiness") is True
                    and need.get("resolution_effect") in {
                        "narrow_candidate",
                        "choose_candidate",
                        "revise_scope",
                    }
                ):
                    return False

    return True if parsed_any else None


def _is_advisory_question_refinement_review(
    source_spec: dict[str, Any],
    review_result: dict[str, Any],
    artifact_ids: list[str],
    review_materials: list[dict[str, Any]] | None = None,
) -> bool:
    """Advance a stage-sufficient question result despite future-detail requests."""
    if (
        source_spec.get("artifactContract") != "research_question_refinement_artifact_v1"
        or review_result["verdict"] != "needs_input"
        or not artifact_ids
    ):
        return False

    stage_sufficient = _question_refinement_stage_sufficiency(review_materials or [])
    if stage_sufficient is not None:
        return stage_sufficient

    # Compatibility fallback for legacy/unreadable review materials.
    return all(
        str(issue.get("severity") or "").strip().lower()
        not in {"blocker", "blocking", "critical", "error"}
        for issue in review_result["issues"]
    )


def _validated_review_materials(value: Any, artifact_ids: list[str]) -> list[dict[str, Any]]:
    """Require the reviewer to receive verified text for every gated artifact."""
    if not isinstance(value, list) or len(value) != len(artifact_ids):
        raise DurableExecutionError(422, "invalid_review_materials")
    expected_ids = list(artifact_ids)
    materials: list[dict[str, Any]] = []
    for artifact_id, material in zip(expected_ids, value, strict=True):
        if not isinstance(material, dict) or material.get("artifactId") != artifact_id:
            raise DurableExecutionError(422, "invalid_review_materials")
        content = material.get("content")
        if not isinstance(content, str) or not content.strip():
            raise DurableExecutionError(422, "invalid_review_materials")
        title = _required_text(material.get("title"), "review material title")
        mime_type = _required_text(material.get("mimeType"), "review material MIME type")
        content_sha256 = _required_text(material.get("contentSha256"), "review material content hash")
        truncated = material.get("truncated")
        if not isinstance(truncated, bool):
            raise DurableExecutionError(422, "invalid_review_materials")
        materials.append({
            "artifactId": artifact_id,
            "title": title,
            "mimeType": mime_type,
            "content": content,
            "truncated": truncated,
            "contentSha256": content_sha256,
        })
    return materials


def _authorized_skill_request(result: dict[str, Any], request: dict[str, Any]) -> dict[str, Any] | None:
    """Validate a model-proposed Skill request before any Skill call exists.

    This deliberately accepts no free-form routing decision: the Skill must be
    both present in the frozen work-unit allowlist and in the server registry,
    and every reference must already be in the specialist's bounded context.
    """
    raw = result.get("skillRequest")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise DurableExecutionError(422, "invalid_agent_skill_request")
    if result.get("artifactPayload") is not None:
        raise DurableExecutionError(422, "agent_skill_request_cannot_include_final_artifact")
    skill_id = _required_text(raw.get("skillId"), "skill request skillId")
    allowed = {_normal_skill_id(value) for value in request.get("selectedSkillIds") or []}
    if _normal_skill_id(skill_id) not in allowed:
        raise DurableExecutionError(403, "agent_skill_request_not_authorized_by_frozen_plan")
    definition, binding = _selected_frozen_skill_authority(request, skill_id)
    memory_refs = _refs(raw.get("inputMemoryRefs"))
    artifact_refs = _refs(raw.get("inputArtifactRefs"))
    if not set(memory_refs).issubset(set(_refs(request.get("inputMemoryRefs")))):
        raise DurableExecutionError(403, "agent_skill_request_memory_reference_not_authorized")
    if not set(artifact_refs).issubset(set(_refs(request.get("inputArtifactRefs")))):
        raise DurableExecutionError(403, "agent_skill_request_artifact_reference_not_authorized")
    parameters = raw.get("parameters") or {}
    if not isinstance(parameters, dict) or len(parameters) > 40:
        raise DurableExecutionError(422, "invalid_agent_skill_request_parameters")
    try:
        encoded = json.dumps(parameters, ensure_ascii=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise DurableExecutionError(422, "invalid_agent_skill_request_parameters") from exc
    if len(encoded) > 20_000:
        raise DurableExecutionError(422, "invalid_agent_skill_request_parameters")
    return {
        "skillId": definition["skillId"],
        "definitionVersion": definition["definitionVersion"],
        "snapshotDefinition": definition,
        "sourceBindingRefs": [_binding_ref(binding)],
        "selectedSkillBindings": [binding],
        "inputMemoryRefs": memory_refs,
        "inputArtifactRefs": artifact_refs,
        "parameters": parameters,
        "reason": _skill_request_text(raw.get("reason"), "skill request reason"),
        "idempotencyKey": _skill_request_text(raw.get("idempotencyKey"), "skill request idempotencyKey"),
    }


def _requested_skill_dispatch(
    *,
    request: dict[str, Any],
    run_id: str,
    unit_id: str,
    owner_id: str,
    workspace_id: str,
    skill_request: dict[str, Any],
) -> tuple[str, dict[str, Any], str]:
    skill_id = _required_text(skill_request.get("skillId"), "skill request skillId")
    definition, binding = _selected_frozen_skill_authority(request, skill_id)
    expected_authority = {
        "definitionVersion": definition["definitionVersion"],
        "snapshotDefinition": definition,
        "sourceBindingRefs": [_binding_ref(binding)],
        "selectedSkillBindings": [binding],
    }
    if any(skill_request.get(key) != value for key, value in expected_authority.items()):
        raise DurableExecutionError(409, "persisted_skill_authority_mismatch")
    idempotency_key = _fingerprint({
        "runId": run_id,
        "workUnitId": unit_id,
        "agentId": request["agentId"],
        "skillId": skill_id,
        "agentKey": _required_text(skill_request.get("idempotencyKey"), "skill request idempotencyKey"),
        "kind": "skill",
    })
    payload = {
        **skill_request,
        "logicalProviderId": request["logicalProviderId"],
        "modelProfile": request["modelMode"],
        "frozenGoal": request["frozenGoal"],
        "frozenConstraints": list(request["frozenConstraints"]),
        "controllerInputContext": list(request["controllerInputContext"]),
        "runId": run_id,
        "taskId": request["taskId"],
        "ownerId": owner_id,
        "workspaceId": workspace_id,
        "workUnitId": unit_id,
        "requestingAgentId": request["agentId"],
    }
    return idempotency_key, payload, _fingerprint(payload)


def _normalize_persisted_skill_request(
    request: Any, *, require_frozen_authority: bool
) -> dict[str, Any]:
    common = {
        "skillId", "inputMemoryRefs", "inputArtifactRefs", "parameters", "reason", "idempotencyKey",
    }
    authority = {
        "definitionVersion", "snapshotDefinition", "sourceBindingRefs", "selectedSkillBindings",
    }
    required = common | authority if require_frozen_authority else common
    if not isinstance(request, dict) or set(request) != required:
        raise DurableExecutionError(409, "persisted_skill_request_invalid")
    try:
        skill_id = _required_text(request.get("skillId"), "persisted Skill id")
        memory_refs = _canonical_persisted_refs(request.get("inputMemoryRefs"))
        artifact_refs = _canonical_persisted_refs(request.get("inputArtifactRefs"))
        reason = _required_text(request.get("reason"), "persisted Skill reason")
        idempotency_key = _required_text(request.get("idempotencyKey"), "persisted Skill idempotency key")
    except ValueError as exc:
        raise DurableExecutionError(409, "persisted_skill_request_invalid") from exc
    parameters = request.get("parameters")
    if not isinstance(parameters, dict) or len(parameters) > 40:
        raise DurableExecutionError(409, "persisted_skill_request_invalid")
    normalized = {
        "skillId": skill_id,
        "inputMemoryRefs": memory_refs,
        "inputArtifactRefs": artifact_refs,
        "parameters": deepcopy(parameters),
        "reason": reason,
        "idempotencyKey": idempotency_key,
    }
    if require_frozen_authority:
        definition_version = request.get("definitionVersion")
        snapshot_definition = request.get("snapshotDefinition")
        selected_bindings = request.get("selectedSkillBindings")
        if (
            not isinstance(definition_version, int)
            or isinstance(definition_version, bool)
            or definition_version < 1
            or not isinstance(snapshot_definition, dict)
            or not isinstance(selected_bindings, list)
        ):
            raise DurableExecutionError(409, "persisted_skill_request_invalid")
        try:
            definitions, bindings = _frozen_skill_authority(
                {
                    "selectedSkillDefinitions": [snapshot_definition],
                    "selectedSkillBindings": selected_bindings,
                },
                [skill_id],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DurableExecutionError(409, "persisted_skill_request_invalid") from exc
        definition = definitions[0]
        binding = bindings[0]
        if (
            definition_version != definition["definitionVersion"]
            or request.get("sourceBindingRefs") != [_binding_ref(binding)]
        ):
            raise DurableExecutionError(409, "persisted_skill_request_invalid")
        normalized.update({
            "definitionVersion": definition_version,
            "snapshotDefinition": definition,
            "sourceBindingRefs": [_binding_ref(binding)],
            "selectedSkillBindings": bindings,
        })
    try:
        encoded = json.dumps(
            normalized, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DurableExecutionError(409, "persisted_skill_request_invalid") from exc
    if json.loads(encoded) != normalized:
        raise DurableExecutionError(409, "persisted_skill_request_invalid")
    return normalized


def _canonical_persisted_refs(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > 100:
        raise DurableExecutionError(409, "persisted_skill_request_invalid")
    refs = [_required_text(item, "persisted Skill reference") for item in value]
    if len(set(refs)) != len(refs):
        raise DurableExecutionError(409, "persisted_skill_request_invalid")
    return refs


def _persisted_skill_request(
    value: Any,
    *,
    frozen_request: dict[str, Any] | None = None,
    require_frozen_authority: bool = True,
) -> dict[str, Any]:
    text = str(value or "")
    if text.startswith("skill-request.v2:"):
        prefix = "skill-request.v2:"
        persisted_has_authority = True
    elif text.startswith("skill-request.v1:"):
        prefix = "skill-request.v1:"
        persisted_has_authority = False
    else:
        raise DurableExecutionError(409, "persisted_skill_request_unavailable")
    try:
        request = json.loads(text[len(prefix):])
    except (TypeError, json.JSONDecodeError) as exc:
        raise DurableExecutionError(409, "persisted_skill_request_invalid") from exc
    normalized = _normalize_persisted_skill_request(
        request, require_frozen_authority=persisted_has_authority,
    )
    if persisted_has_authority or not require_frozen_authority:
        return normalized
    if frozen_request is None:
        raise DurableExecutionError(409, "persisted_skill_authority_unavailable")
    try:
        definition, binding = _selected_frozen_skill_authority(
            frozen_request, normalized["skillId"],
        )
    except (DurableExecutionError, KeyError, TypeError, ValueError) as exc:
        raise DurableExecutionError(409, "persisted_skill_authority_unavailable") from exc
    return {
        **normalized,
        "definitionVersion": definition["definitionVersion"],
        "snapshotDefinition": definition,
        "sourceBindingRefs": [_binding_ref(binding)],
        "selectedSkillBindings": [binding],
    }


def _frozen_skill_authority(
    source: dict[str, Any], selected_skill_ids: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate and canonical-copy the frozen Definition/Binding route."""
    if not selected_skill_ids:
        return [], []
    raw_definitions = source.get("selectedSkillDefinitions")
    raw_bindings = source.get("selectedSkillBindings")
    if not isinstance(raw_definitions, list) or not isinstance(raw_bindings, list):
        raise ValueError("selected Skill authority is unavailable")
    if not all(isinstance(row, dict) for row in [*raw_definitions, *raw_bindings]):
        raise ValueError("selected Skill authority must contain mappings")

    definitions: dict[tuple[str, int], dict[str, Any]] = {}
    for raw in raw_definitions:
        skill_id = _required_text(raw.get("skillId"), "selected Skill definition skillId")
        version = raw.get("definitionVersion")
        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or version < 1
            or raw.get("enabled") is not True
            or raw.get("status") != "ready"
        ):
            raise ValueError("selected Skill definition identity is invalid")
        identity = (_normal_skill_id(skill_id), version)
        if identity in definitions:
            raise ValueError("selected Skill definitions must be unique")
        definitions[identity] = deepcopy(raw)

    bindings: dict[tuple[str, int], dict[str, Any]] = {}
    for raw in raw_bindings:
        skill_id = _required_text(raw.get("skillId"), "selected Skill binding skillId")
        skill_version = raw.get("skillDefinitionVersion")
        binding_version = raw.get("bindingVersion")
        definition_id = _required_text(raw.get("definitionId"), "selected Skill binding definitionId")
        definition_version = raw.get("definitionVersion")
        if (
            not isinstance(skill_version, int)
            or isinstance(skill_version, bool)
            or skill_version < 1
            or not isinstance(binding_version, int)
            or isinstance(binding_version, bool)
            or binding_version < 1
            or not isinstance(definition_version, int)
            or isinstance(definition_version, bool)
            or definition_version < 1
            or raw.get("bindingId") != f"bind:{definition_id}:skill:{skill_id}"
            or raw.get("enabled") is not True
            or raw.get("sourceAuthority") != "skill_binding_source"
        ):
            raise ValueError("selected Skill binding identity is invalid")
        identity = (_normal_skill_id(skill_id), skill_version)
        if identity in bindings:
            raise ValueError("selected Skill bindings must be unique")
        bindings[identity] = deepcopy(raw)

    ordered_definitions: list[dict[str, Any]] = []
    ordered_bindings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for selected_skill_id in selected_skill_ids:
        normal_id = _normal_skill_id(selected_skill_id)
        if normal_id in seen_ids:
            raise ValueError("selected Skill ids must be unique")
        seen_ids.add(normal_id)
        matches = [identity for identity in definitions if identity[0] == normal_id]
        if len(matches) != 1 or matches[0] not in bindings:
            raise ValueError("each selected Skill requires one frozen Definition and Binding")
        definition = definitions[matches[0]]
        binding = bindings[matches[0]]
        if definition["skillId"] != selected_skill_id or binding["skillId"] != selected_skill_id:
            raise ValueError("selected Skill identity does not match frozen authority")
        ordered_definitions.append(definition)
        ordered_bindings.append(binding)
    if len(ordered_definitions) != len(definitions) or len(ordered_bindings) != len(bindings):
        raise ValueError("frozen Skill authority contains an unselected route")
    return ordered_definitions, ordered_bindings


def _selected_frozen_skill_authority(
    request: dict[str, Any], skill_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    definitions, bindings = _frozen_skill_authority(
        request, _refs(request.get("selectedSkillIds"))
    )
    matches = [
        (definition, binding)
        for definition, binding in zip(definitions, bindings, strict=True)
        if _normal_skill_id(definition["skillId"]) == _normal_skill_id(skill_id)
    ]
    if len(matches) != 1:
        raise DurableExecutionError(403, "agent_skill_request_not_authorized_by_frozen_plan")
    return deepcopy(matches[0][0]), deepcopy(matches[0][1])


def _binding_ref(binding: dict[str, Any]) -> dict[str, Any]:
    return {
        "bindingId": binding["bindingId"],
        "bindingVersion": binding["bindingVersion"],
    }


def _normal_skill_id(value: Any) -> str:
    return _required_text(value, "skill identifier").replace("-", "_").lower()


def _skill_request_text(value: Any, field: str) -> str:
    try:
        return _required_text(value, field)
    except ValueError as exc:
        raise DurableExecutionError(422, "invalid_agent_skill_request") from exc


def _fingerprint(value: dict[str, Any]) -> str:
    return sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _title(spec: dict[str, Any]) -> str:
    return f"{spec['agentId']} research task"


def _refs(*values: Any) -> list[str]:
    refs: list[str] = []
    for value in values:
        if isinstance(value, (list, tuple)):
            refs.extend(_text(item) for item in value)
    return list(dict.fromkeys(ref for ref in refs if ref))


def _required_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a mapping")
    return value


def _required_text(value: Any, field: str) -> str:
    text = _text(value)
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _first_text(*values: Any) -> str:
    return next((text for value in values if (text := _text(value))), "")


def _text(value: Any) -> str:
    return str(value or "").strip()
