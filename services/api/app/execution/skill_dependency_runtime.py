"""Invoke Skills under frozen Binding or AgentSkill authority.

A Definition→Skill Binding is the sole handoff authority. This helper follows
only explicit artifact source Binding refs and delegates payload assembly/schema
validation to the shared input contract gate. Skill-global handoff metadata is
never read and no semantic projection is performed here. Agent-internal calls
instead use their frozen AgentSkill grant and the same production Skill gate.
"""

from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from typing import Any, Callable, Mapping

from app.execution.durable_skill_runtime import (
    execute_production_durable_skill,
    resolve_registered_durable_skill_runtime,
)
from app.execution.research_dispatch_provider import DispatchSubmissionRequest
from app.execution.research_input_contract_gate import (
    InputContractGateFailure,
    SkillInputRequest,
    resolve_registered_skill_input,
)
from app.models.research_orchestration import ExecutionStrategy


class SkillDependencyContractError(RuntimeError):
    """A frozen Skill Binding prerequisite graph cannot execute truthfully."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _skill_definition(
    snapshot: Mapping[str, Any],
    skill_id: str,
    definition_version: int,
) -> Mapping[str, Any]:
    rows = [
        row
        for row in snapshot.get("definitions", {}).get("skills", [])
        if isinstance(row, Mapping)
        and row.get("skillId") == skill_id
        and row.get("definitionVersion") == definition_version
    ]
    if len(rows) != 1:
        raise SkillDependencyContractError("skill_dependency_definition_missing")
    return rows[0]


def _skill_binding(
    snapshot: Mapping[str, Any],
    binding_ref: tuple[str, int],
) -> Mapping[str, Any]:
    binding_id, binding_version = binding_ref
    rows = [
        row
        for row in snapshot.get("bindings", {}).get("skills", [])
        if isinstance(row, Mapping)
        and row.get("bindingId") == binding_id
        and row.get("bindingVersion") == binding_version
        and row.get("enabled") is True
    ]
    if len(rows) != 1:
        raise SkillDependencyContractError("skill_dependency_binding_missing")
    return rows[0]


def _artifact_source_binding_refs(binding: Mapping[str, Any]) -> tuple[tuple[str, int], ...]:
    input_sources = binding.get("inputSources")
    if input_sources is None:
        return ()
    if not isinstance(input_sources, Mapping) or input_sources.get("contractVersion") != 2:
        raise SkillDependencyContractError("skill_dependency_input_sources_missing")
    fields = input_sources.get("fields")
    if not isinstance(fields, list):
        raise SkillDependencyContractError("skill_dependency_input_sources_invalid")

    refs: set[tuple[str, int]] = set()
    for entry in fields:
        source = entry.get("source") if isinstance(entry, Mapping) else None
        if not isinstance(source, Mapping):
            raise SkillDependencyContractError("skill_dependency_input_sources_invalid")
        if source.get("kind") != "artifact":
            continue
        presence = entry.get("sourcePresence")
        if presence not in {"required", "optional"}:
            raise SkillDependencyContractError("skill_dependency_input_sources_invalid")
        if presence == "optional":
            # Optional upstream Artifacts are historical/conditional inputs. The
            # dependency runtime must not manufacture them by executing a producer.
            continue
        binding_id = source.get("bindingId")
        binding_version = source.get("bindingVersion")
        if (
            not isinstance(binding_id, str)
            or not binding_id
            or not isinstance(binding_version, int)
            or isinstance(binding_version, bool)
            or binding_version < 1
        ):
            raise SkillDependencyContractError("skill_dependency_input_sources_invalid")
        refs.add((binding_id, binding_version))
    return tuple(sorted(refs))


def _resolve_frozen_binding_graph(
    snapshot: Mapping[str, Any],
    *,
    target_binding_ref: tuple[str, int],
) -> tuple[Mapping[str, Any], ...]:
    ordered: list[Mapping[str, Any]] = []
    visiting: set[tuple[str, int]] = set()
    visited: set[tuple[str, int]] = set()

    def visit(binding_ref: tuple[str, int]) -> None:
        if binding_ref in visited:
            return
        if binding_ref in visiting:
            raise SkillDependencyContractError("skill_dependency_cycle")
        visiting.add(binding_ref)
        binding = _skill_binding(snapshot, binding_ref)
        for source_ref in _artifact_source_binding_refs(binding):
            visit(source_ref)
        visiting.remove(binding_ref)
        visited.add(binding_ref)
        ordered.append(binding)

    visit(target_binding_ref)
    return tuple(ordered)


def resolve_frozen_skill_dependency_chain(
    snapshot: Mapping[str, Any],
    *,
    target_binding_ref: tuple[str, int],
) -> tuple[tuple[str, int], ...]:
    """Return the dependencies-first Skill identities frozen by one Binding graph."""
    return tuple(
        (str(binding["skillId"]), int(binding["skillDefinitionVersion"]))
        for binding in _resolve_frozen_binding_graph(
            snapshot, target_binding_ref=target_binding_ref
        )
    )


def execute_frozen_skill_dependency_chain(
    *,
    snapshot: Mapping[str, Any],
    target_binding_ref: tuple[str, int],
    canonical_user_query: str,
    scientific_state: Mapping[str, Any] | None = None,
    request: DispatchSubmissionRequest,
    requesting_actor_id: str,
    execute: Callable[[dict[str, Any]], dict[str, Any]] = execute_production_durable_skill,
    runtime_resolver: Callable[..., dict[str, Any] | None] = (
        resolve_registered_durable_skill_runtime
    ),
) -> dict[str, Any]:
    """Execute the artifact prerequisites frozen by a concrete target Binding."""
    query = str(canonical_user_query or "").strip()
    if not query:
        raise SkillDependencyContractError("skill_dependency_canonical_query_missing")

    bindings = _resolve_frozen_binding_graph(
        snapshot, target_binding_ref=target_binding_ref
    )
    artifacts: list[dict[str, Any]] = []
    result: dict[str, Any] = {}

    for index, binding in enumerate(bindings):
        skill_id = str(binding["skillId"])
        definition_version = int(binding["skillDefinitionVersion"])
        definition = _skill_definition(snapshot, skill_id, definition_version)
        runtime = runtime_resolver(
            skill_id,
            definition_version=definition_version,
            snapshot_definition=definition,
        )
        if runtime is None:
            raise SkillDependencyContractError("skill_dependency_runtime_unavailable")

        is_target = index == len(bindings) - 1
        if not is_target and bool(runtime.get("requiresExternalAction")):
            raise SkillDependencyContractError(
                "skill_dependency_external_action_requires_dispatch"
            )

        try:
            parameters = resolve_registered_skill_input(
                snapshot=snapshot,
                request=SkillInputRequest(
                    task_id=request.task_id,
                    strategy=ExecutionStrategy.SPECIALIST_AGENT.value,
                    skill_id=skill_id,
                    definition_version=definition_version,
                ),
                input_context={
                    "executionIdentity": {"task_id": request.task_id},
                    "invocationContext": {"canonical_user_query": query},
                    "scientificState": dict(scientific_state or {}),
                    "scientificStatePresent": scientific_state is not None,
                    "artifacts": list(artifacts),
                },
                skill_binding=binding,
            )
        except InputContractGateFailure as exc:
            raise SkillDependencyContractError(exc.code) from exc

        execution_request = {
            "skillId": skill_id,
            "taskId": request.task_id,
            "ownerId": request.owner_id,
            "workspaceId": request.workspace_id,
            "workUnitId": request.work_unit_id,
            "callId": request.idempotency_key,
            "capabilityCallId": request.capability_call_id,
            "attemptNumber": request.attempt_number,
            "requestingActorType": "agent",
            "requestingActorId": requesting_actor_id,
            "frozenGoal": query,
            "frozenConstraints": [],
            "controllerInputContext": ["user_goal"],
            "parameters": parameters,
            "logicalProviderId": runtime.get("logicalProviderId"),
            "modelProfile": runtime.get("modelProfile"),
            **_provider_evidence(runtime, request, skill_id),
            **_binding_input_evidence(binding),
        }
        result = execute(execution_request)
        if str(result.get("state") or "") != "succeeded":
            return _annotate_dependency_failure(result, skill_id)
        if not is_target:
            artifacts.append(
                _dependency_artifact(
                    skill_id=skill_id,
                    definition_version=definition_version,
                    source_binding_ref=(str(binding["bindingId"]), int(binding["bindingVersion"])),
                    content=_decode_succeeded_json_artifact(result),
                )
            )

    return result


def execute_frozen_agent_skill(
    *,
    snapshot: Mapping[str, Any],
    target_skill_ref: tuple[str, int],
    parameters: Mapping[str, Any],
    canonical_user_query: str,
    request: DispatchSubmissionRequest,
    requesting_actor_id: str,
    execute: Callable[..., dict[str, Any]] = execute_production_durable_skill,
    runtime_resolver: Callable[..., dict[str, Any] | None] = (
        resolve_registered_durable_skill_runtime
    ),
) -> dict[str, Any]:
    """Invoke an Agent-internal Skill under its frozen AgentSkill grant.

    This is not a Definition Binding dependency graph. The existing production
    entrypoint still owns input Schema checks and Binding evidence requirements.
    """
    skill_id, definition_version = target_skill_ref
    grants = [
        row for row in snapshot.get("bindings", {}).get("agentSkills", [])
        if row.get("agentId") == requesting_actor_id
        and row.get("skillId") == skill_id
        and row.get("skillDefinitionVersion") == definition_version
        and row.get("enabled") is True
    ]
    if len(grants) != 1:
        raise SkillDependencyContractError("agent_skill_frozen_grant_missing")
    definition = _skill_definition(snapshot, skill_id, definition_version)
    runtime = runtime_resolver(
        skill_id, definition_version=definition_version, snapshot_definition=definition,
    )
    if runtime is None:
        raise SkillDependencyContractError("skill_dependency_runtime_unavailable")
    execution_request = {
        "skillId": skill_id,
        "taskId": request.task_id,
        "ownerId": request.owner_id,
        "workspaceId": request.workspace_id,
        "workUnitId": request.work_unit_id,
        "callId": request.idempotency_key,
        "capabilityCallId": request.capability_call_id,
        "executionDecisionId": request.execution_decision_id,
        "attemptNumber": request.attempt_number,
        "requestingActorType": "agent",
        "requestingActorId": requesting_actor_id,
        "frozenGoal": canonical_user_query,
        "frozenConstraints": [],
        "controllerInputContext": ["user_goal"],
        "parameters": deepcopy(dict(parameters)),
        "logicalProviderId": runtime.get("logicalProviderId"),
        "modelProfile": runtime.get("modelProfile"),
        **_provider_evidence(runtime, request, skill_id),
    }
    if execute is execute_production_durable_skill:
        return execute(execution_request, prepared_runtime=runtime)
    return execute(execution_request)


def _dependency_artifact(
    *,
    skill_id: str,
    definition_version: int,
    source_binding_ref: tuple[str, int],
    content: bytes,
) -> dict[str, Any]:
    return {
        "sourceCapabilityType": "skill",
        "sourceCapabilityId": skill_id,
        "sourceCapabilityDefinitionVersion": definition_version,
        "sourceBindingRefs": [{
            "bindingId": source_binding_ref[0],
            "bindingVersion": source_binding_ref[1],
        }],
        "mimeType": "application/json",
        "content": content,
    }


def _provider_evidence(
    runtime: Mapping[str, Any],
    request: DispatchSubmissionRequest,
    skill_id: str,
) -> dict[str, Any]:
    if runtime.get("runtimeKind") != "llm_reasoning":
        return {}
    return {
        "providerEvidence": {
            "capabilityCallId": request.capability_call_id,
            "attemptNumber": request.attempt_number,
            "evidenceKind": "sub_call",
            "adapterName": f"skill_dependency:{skill_id}",
        }
    }


def _binding_input_evidence(binding: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "bindingInputEvidence": {
            "bindingId": binding["bindingId"],
            "bindingVersion": binding["bindingVersion"],
            "bindingContractVersion": 2,
            "inputSources": binding.get("inputSources"),
            "runtimeInputs": binding.get("runtimeInputs"),
        }
    }

def _decode_succeeded_json_artifact(result: Mapping[str, Any]) -> bytes:
    payload = result.get("artifactPayload")
    presentation = payload.get("presentation") if isinstance(payload, Mapping) else None
    if (
        result.get("state") != "succeeded"
        or not isinstance(payload, Mapping)
        or payload.get("contentType") != "application/json"
        or not isinstance(presentation, Mapping)
    ):
        raise SkillDependencyContractError("skill_dependency_output_invalid")
    try:
        raw = base64.b64decode(str(payload.get("contentBase64") or ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise SkillDependencyContractError("skill_dependency_output_invalid") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if not raw or str(presentation.get("contentSha256") or "").strip().lower() != digest:
        raise SkillDependencyContractError("skill_dependency_output_invalid")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillDependencyContractError("skill_dependency_output_invalid") from exc
    if not isinstance(parsed, dict):
        raise SkillDependencyContractError("skill_dependency_output_invalid")
    return raw


def _annotate_dependency_failure(
    result: Mapping[str, Any],
    skill_id: str,
) -> dict[str, Any]:
    annotated = dict(result)
    annotated.setdefault("failedStage", f"dependency:{skill_id}")
    annotated.setdefault("firstMismatch", f"declared_dependency_failed:{skill_id}")
    return annotated


__all__ = [
    "SkillDependencyContractError",
    "execute_frozen_agent_skill",
    "execute_frozen_skill_dependency_chain",
    "resolve_frozen_skill_dependency_chain",
]
