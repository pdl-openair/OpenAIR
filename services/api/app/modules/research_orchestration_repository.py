"""PostgreSQL persistence skeleton for research orchestration v1.2.

This module owns no runtime/provider/worker behavior.  It only performs scoped
reads and a Task CAS with optional pristine pending outbox inserts in one local
database transaction.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, ContextManager, Mapping, Protocol

import psycopg
from psycopg import errors
from psycopg.rows import dict_row
from pydantic import ValidationError

from app.config import settings
from app.modules.canonical_execution_lineage import validate_canonical_execution_lineage
from app.modules.controller_command_repository import ControllerCommandRepository
from app.models.research_orchestration import (
    ActorRef,
    ArtifactState,
    AssignmentRole,
    CapabilityType,
    DispatchRecord,
    DispatchStatus,
    ExecutorRef,
    ExecutionDecisionOutcome,
    ExecutionDecisionStatus,
    LegacySubstageProjection,
    LegacyTaskProjection,
    LegacyWorkUnitProjection,
    ResearchDesignRef,
    ResearchDesignStatus,
    ResearchTaskV12,
    TaskExecutionMode,
    TaskStatus,
    WorkUnitSourceMode,
    WorkUnitStatus,
)
from app.models.research_orchestration import READ_ONLY_LEGACY_RESEARCH_DESIGN
from app.models.result_protocol import (
    artifact_quality_for_protocol,
    merge_result_protocols,
    normalize_result_protocol,
)
from app.execution.research_dispatch_provider import (
    DispatchSubmissionCompleted,
)
from app.modules.artifact_field_projection import merge_artifact_field_projection
from app.modules.artifact_repository import ensure_initial_version_truth
from app.modules.artifact_write import ArtifactStorageError, artifact_write_service
from app.modules.registry_snapshot_repository import snapshot_from_envelope
from app.modules.research_blocker_semantics import needs_user_input, runtime_blocker_status
from app.modules.continuation_responsibility import (
    NEEDS_INPUT_RESUME_PATH,
    NEEDS_INPUT_USER_ACTION,
    ContinuationOwnerKind,
    ContinuationOwnerRef,
    ContinuationResponsibilityKind,
    ContinuationResponsibilityProof,
    user_responsibility_contract,
)
from app.modules.research_progress_projection import project_task_progress
from app.modules.research_task_lifecycle import (
    ResearchDesignLifecycleFact,
    reconcile_task_lifecycle,
)
from app.modules.research_task_lifecycle_repository import (
    TaskLifecycleWriteConflictError,
    commit_reconciled_task_projection_locked,
)
from app.modules.research_scientific_state_authority import (
    latest_scientific_state_for_design,
    scientific_state_at_version_for_design,
)
from app.modules.task_message_repository import TaskMessageRepository

if TYPE_CHECKING:
    from app.modules.research_execution_decision_service import (
        ExecutionMaterialization,
        PreparedSelection,
        SelectionCommitResult,
    )
from app.models.research_external_result import (
    EvidenceCaptureRequest,
    EvidenceKind,
    EvidenceLineage,
    ExplicitRetryCommand,
    ExternalExecutionState,
    ExternalQueryObservation,
    FailureClass,
    FrozenExternalQuery,
    ReconcileExternalResultCommand,
    SafeValidatorDiagnostic,
)
from app.modules.research_external_evidence_repository import (
    ExternalEvidenceRecorder,
    ResearchExternalEvidenceRepository,
)
from app.modules.research_external_recovery_persistence import (
    enqueue_dispatch_continuation as _enqueue_dispatch_continuation,
    ensure_unknown_recovery_obligation as _ensure_unknown_recovery_obligation,
    no_external_action_retry_proven as _no_external_action_retry_proven,
    prepare_reconcile_query as _prepare_reconcile_query,
    prepare_system_explicit_retry_command as _prepare_system_explicit_retry_command,
    prepare_system_reconcile_command as _prepare_system_reconcile_command,
    system_external_recovery_state as _system_external_recovery_state,
    validate_reconcile_row as _validate_reconcile_row,
    validate_known_failed_safe_retry_authorization as _validate_known_failed_safe_retry_authorization,
)

logger = logging.getLogger(__name__)


class ResearchOrchestrationPersistenceError(RuntimeError):
    pass


class FeatureDisabledError(ResearchOrchestrationPersistenceError):
    pass


class PersistenceBackendUnsupportedError(ResearchOrchestrationPersistenceError):
    pass


class TaskRevisionConflictError(ResearchOrchestrationPersistenceError):
    pass


class DispatchIdentityConflictError(ResearchOrchestrationPersistenceError):
    pass


def _project_optional_enhancement_failure_from_raw_payload(
    task: ResearchTaskV12,
    *,
    execution_decision_id: str,
    occurred_at: datetime,
    raw_payload: Mapping[str, Any] | None,
) -> ResearchTaskV12:
    """Use verified frozen Workflow lineage to project an authorized optional skip."""
    if raw_payload is None:
        return task
    workflow_admission = raw_payload.get("workflowAdmission")
    if workflow_admission is None:
        return task
    if not isinstance(workflow_admission, Mapping):
        raise ResearchOrchestrationPersistenceError("workflow_admission_invalid")
    try:
        lineage = validate_canonical_execution_lineage(
            task=task,
            workflow_admission=workflow_admission,
            persisted_skill_pack=raw_payload.get("skillPack"),
        )
    except ValueError as exc:
        raise ResearchOrchestrationPersistenceError(
            "canonical_workflow_lineage_mismatch"
        ) from exc
    snapshot = workflow_admission.get("workflowDefinitionSnapshot")
    definition = snapshot.get("definition") if isinstance(snapshot, Mapping) else None
    if not isinstance(definition, Mapping):
        raise ResearchOrchestrationPersistenceError("canonical_workflow_lineage_mismatch")
    from app.modules.research_execution_decision_service import (
        project_system_owned_optional_enhancement_failure,
    )

    return project_system_owned_optional_enhancement_failure(
        task,
        execution_decision_id=execution_decision_id,
        occurred_at=occurred_at,
        workflow_lineage=lineage.as_lineage_facts(),
        workflow_definition=definition,
    )


def _normalized_sha256(value: object) -> str | None:
    """Accept the canonical sha256 URI form and the stored bare digest form."""
    digest = str(value or "")
    if digest.startswith("sha256:"):
        digest = digest[7:]
    return digest if re.fullmatch(r"[0-9a-f]{64}", digest) else None


def _digest_matches(value: object, expected_hex: object) -> bool:
    actual = _normalized_sha256(value)
    expected = _normalized_sha256(expected_hex)
    return actual is not None and actual == expected


def _selection_blocked_outcome(
    *,
    reason_code: str,
    selection_command_hash: str,
    substage_instance_id: str,
    facts: Mapping[str, Any],
) -> str:
    return "blocked:" + json.dumps(
        {
            "facts": dict(facts),
            "reasonCode": reason_code,
            "selectionCommandHash": selection_command_hash,
            "substageInstanceId": substage_instance_id,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _parse_selection_blocked_outcome(
    outcome: object,
) -> tuple[str, str, str, Mapping[str, Any]] | None:
    text = str(outcome or "").strip()
    if not text.startswith("blocked:"):
        return None
    raw = text[len("blocked:") :]
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    reason_code = str(payload.get("reasonCode") or "").strip()
    command_hash = str(payload.get("selectionCommandHash") or "").strip()
    instance_id = str(payload.get("substageInstanceId") or "").strip()
    facts = payload.get("facts")
    if (
        not reason_code
        or _normalized_sha256(command_hash) is None
        or not instance_id
        or not isinstance(facts, dict)
    ):
        return None
    return reason_code, command_hash, instance_id, facts


def _blocked_outcome_expired(blocker_facts: Mapping[str, Any], current_revision: int) -> bool:
    """A persisted blocked verdict only applies while the task hasn't advanced past it.

    Blocked outcomes record the task revision at block time (``facts.taskRevision``,
    written as ``expected_task_revision + 1``). If the task has since moved beyond that
    revision (design revision, re-confirmation, any CAS write), the conditions that
    produced the block no longer hold and the verdict must not be replayed — otherwise
    a single transient block (e.g. a workflow-lineage check fired before admission was
    written) permanently pins the task. Facts lacking ``taskRevision`` (legacy blocks
    written before this field) are left to their original always-replay behavior.
    """
    blocked_revision = blocker_facts.get("taskRevision")
    return isinstance(blocked_revision, int) and current_revision > blocked_revision


class PayloadRevisionMismatchError(ResearchOrchestrationPersistenceError):
    pass


class ResearchTaskNotFoundError(ResearchOrchestrationPersistenceError):
    pass


class DispatchAuthorizationError(ResearchOrchestrationPersistenceError):
    pass


class SelectionRequestPayloadConflictError(ResearchOrchestrationPersistenceError):
    pass


class ExternalRecoveryConflictError(ResearchOrchestrationPersistenceError):
    pass


_DIRECT_SKILL_ARTIFACT_FORMAT_BY_MIME: dict[str, str] = {
    "application/json": "json",
    "text/csv": "csv",
    "text/markdown": "markdown",
    "text/plain": "text",
    "text/yaml": "yaml",
}


def _canonical_artifact_format(
    *,
    assignment: Mapping[str, Any],
    result: DispatchSubmissionCompleted,
) -> str:
    """Resolve public Artifact format from the producer contract that owns it.

    Agent outputs are validated against a frozen structured Artifact schema, so
    their canonical public format remains JSON even when provider transport uses
    ``text/plain``. Direct Skills freeze and validate ``artifactMimeType`` before
    this repository seam, so their validated MIME may authoritatively select an
    existing public Artifact format.
    """
    executor = assignment.get("executorRef")
    executor_type = executor.get("executorType") if isinstance(executor, Mapping) else None
    if executor_type == "agent":
        return "json"
    if executor_type != "skill":
        raise ResearchOrchestrationPersistenceError("producer_artifact_format_authority_missing")
    artifact_format = _DIRECT_SKILL_ARTIFACT_FORMAT_BY_MIME.get(result.mime_type)
    if artifact_format is None:
        raise ResearchOrchestrationPersistenceError("producer_artifact_mime_type_unsupported")
    return artifact_format


def _dispatch_agent_message_id(fence: "AuthorizedDispatch") -> str:
    """Derive a replay-safe message identity without including provider content."""
    identity = f"{fence.task_id}:{fence.dispatch_id}:{fence.work_unit_id}".encode("utf-8")
    return f"msg_dispatch_{hashlib.sha256(identity).hexdigest()[:24]}"


def _producer_artifact_id(dispatch_id: str, artifact_type: str | None = None) -> str:
    """Stable Artifact identity for one dispatch-owned typed output."""
    if artifact_type:
        return f"artifact:{dispatch_id}:{artifact_type}"
    return f"artifact:{dispatch_id}"


def _assert_multi_artifact_schema_authority(
    expected_types: list[str],
    output_schema: Mapping[str, Any] | None,
) -> None:
    """Admit multi-Artifact split only when formal output schema authorizes it.

    Narrow shared rule (not a mapping DSL):
    - output schema must be a JSON object schema;
    - ``expectedArtifactTypes`` must exactly equal the schema's required
      top-level fields (set equality; no extras, no omissions);
    - each expected type must be declared under ``properties``.

    Nested paths, aliases, and type≠field-name shapes stay fail-closed.
    """
    if not isinstance(output_schema, Mapping):
        raise ResearchOrchestrationPersistenceError(
            "producer_multi_artifact_schema_missing"
        )
    if output_schema.get("type") != "object":
        raise ResearchOrchestrationPersistenceError(
            "producer_multi_artifact_schema_unauthorized"
        )
    required = output_schema.get("required")
    properties = output_schema.get("properties")
    if not isinstance(required, list) or not isinstance(properties, Mapping):
        raise ResearchOrchestrationPersistenceError(
            "producer_multi_artifact_schema_unauthorized"
        )
    required_types = [str(item) for item in required if str(item).strip()]
    if (
        not expected_types
        or len(expected_types) != len(set(expected_types))
        or set(expected_types) != set(required_types)
    ):
        raise ResearchOrchestrationPersistenceError(
            "producer_multi_artifact_schema_unauthorized"
        )
    for artifact_type in expected_types:
        if artifact_type not in properties:
            raise ResearchOrchestrationPersistenceError(
                "producer_multi_artifact_schema_unauthorized"
            )


def _producer_frozen_output_schema(
    snapshot: Mapping[str, Any] | None,
    assignment: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Resolve the producer Skill's frozen output schema from a registry snapshot."""
    executor = assignment.get("executorRef")
    if not isinstance(executor, Mapping):
        return None
    if str(executor.get("executorType") or "").strip() != "skill":
        return None
    skill_id = str(executor.get("executorId") or "").strip()
    if not skill_id:
        return None
    version_raw = executor.get("definitionVersion")
    version: int | None
    try:
        version = int(version_raw) if version_raw is not None else None
    except (TypeError, ValueError):
        version = None
    skills = ((snapshot or {}).get("definitions") or {}).get("skills") or []
    for row in skills:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("skillId") or "").strip() != skill_id:
            continue
        if version is not None:
            try:
                if int(row.get("definitionVersion")) != version:
                    continue
            except (TypeError, ValueError):
                continue
        frozen = (
            ((row.get("sourceContract") or {}).get("frozenSchemas") or {}).get("output")
            or {}
        )
        schema = frozen.get("schema") if isinstance(frozen, Mapping) else None
        if isinstance(schema, Mapping):
            return dict(schema)
    return None


def _split_provider_result_by_expected_types(
    content: str,
    expected_types: list[str],
    *,
    output_schema: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Map each expected Artifact type to its formal JSON payload.

    Authority comes from the producer output schema (required top-level fields).
    Each admitted type must then exist as an exact top-level key on the provider
    result; the platform never copies the whole document into every Artifact and
    never invents fields or nested-path mappings.
    """
    _assert_multi_artifact_schema_authority(expected_types, output_schema)
    try:
        document = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ResearchOrchestrationPersistenceError(
            "producer_multi_artifact_payload_invalid"
        ) from exc
    if not isinstance(document, dict):
        raise ResearchOrchestrationPersistenceError(
            "producer_multi_artifact_payload_invalid"
        )
    payloads: dict[str, str] = {}
    for artifact_type in expected_types:
        if artifact_type not in document:
            raise ResearchOrchestrationPersistenceError(
                "producer_multi_artifact_field_missing",
            )
        payloads[artifact_type] = json.dumps(
            document[artifact_type],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return payloads


def _provider_result_message_text(content: str) -> str | None:
    """Use only contract-shaped, user-readable result fields, never the raw body."""
    try:
        body = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    if body.get("verdict") == "external_result_unknown" or body.get("status") in {
        "unknown",
        "failed",
    }:
        return None

    # These are the existing user-readable fields used by the current research
    # artifact contracts.  Values outside this small allowlist remain in the
    # Artifact and are not projected into collaboration text.
    paths = (
        ("reasoning_summary",),
        ("summary",),
        ("answer",),
        ("result",),
        ("search_boundary", "target_question"),
        ("question_candidates", 0, "research_question"),
        ("handoff", "instruction"),
        ("basis",),
    )
    for path in paths:
        value: Any = body
        for part in path:
            if isinstance(part, int):
                if not isinstance(value, list) or len(value) <= part:
                    break
            elif not isinstance(value, dict) or part not in value:
                break
            value = value[part]
        else:
            if isinstance(value, str):
                text = " ".join(value.split())
                if text and len(text) <= 2_000 and not any(
                    marker in text.lower()
                    for marker in ("traceback", "provider_id", "providerid", "adapter")
                ):
                    return text
    return None


def _compact_collaboration_detail(value: Any, *, limit: int = 800) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or len(text) > limit:
        return None
    lowered = text.lower()
    if any(marker in lowered for marker in (
        "traceback", "provider", "adapter", "runtime", "postgres", "sqlalchemy", "stack trace",
    )):
        return None
    return text


def _dispatch_message_narrative(
    *,
    assignment: dict[str, Any],
    result: DispatchSubmissionCompleted,
    content: str,
    capability_call_id: str,
    is_reviewer: bool,
) -> dict[str, Any]:
    """Persist only research-useful, contract-shaped detail for the folded UI."""
    details: list[str] = []
    objective = _compact_collaboration_detail(assignment.get("objective"))
    if objective:
        details.append(f"本次工作：{objective}")

    try:
        body = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        body = None
    if isinstance(body, dict):
        for label, value in (
            ("研究依据", body.get("basis")),
            ("研究范围", body.get("search_boundary", {}).get("target_question") if isinstance(body.get("search_boundary"), dict) else None),
            ("交接结论", body.get("handoff", {}).get("instruction") if isinstance(body.get("handoff"), dict) else None),
            ("假设与限制", body.get("limitations") or body.get("assumptions")),
        ):
            if isinstance(value, list):
                value = "；".join(item for item in value if isinstance(item, str))
            detail = _compact_collaboration_detail(value)
            if detail:
                details.append(f"{label}：{detail}")
        if is_reviewer:
            reviewer_conclusions = {
                "usable": "复核结论：当前结果满足既定要求。",
                "needs_revision": "复核结论：结果需要修订后再继续。",
                "insufficient_materials": "复核结论：现有证据不足，需要补充材料。",
                "human_decision_required": "复核结论：需要你确认后才能继续。",
            }
            conclusion = reviewer_conclusions.get(body.get("verdict"))
            if conclusion:
                details.append(conclusion)
    elif not is_reviewer:
        title = _compact_collaboration_detail(result.title, limit=200)
        if title:
            details.append(f"本次产物：{title}")

    return {
        "summary": _provider_result_message_text(content) or "本次研究结果已形成。",
        "detailText": "\n".join(details) or None,
        "detailMarkdown": None,
        "detailCollapsedByDefault": True,
        "sourceType": "capability_call",
        "sourceId": capability_call_id,
        "severity": "success",
        "suggestedActions": [],
    }


class ExternalRecoveryStateError(ResearchOrchestrationPersistenceError):
    pass


@dataclass(frozen=True)
class DispatchFence:
    dispatch_id: str
    task_id: str
    execution_decision_id: str
    work_unit_id: str
    idempotency_key: str
    lease_owner: str
    attempt_count: int


@dataclass(frozen=True)
class ExternalAttemptIdentity:
    capability_call_id: str
    attempt_number: int


@dataclass(frozen=True)
class AuthorizedDispatch(DispatchFence):
    authorization_task_revision: int
    work_unit_payload: dict[str, Any]
    external_attempt: ExternalAttemptIdentity
    submission_mode: str = "durable_idempotent"
    external_action_started_at: datetime | None = None
    task_revision_after_action_started: int | None = None
    result_storage_type: str | None = None
    result_storage_scope: str | None = None
    result_storage_ref: str | None = None
    result_recovery_from_unknown: bool = False


@dataclass(frozen=True)
class DispatchContinuationFence:
    dispatch_id: str
    task_id: str
    execution_decision_id: str
    work_unit_id: str
    owner_id: str
    workspace_id: str
    lease_owner: str
    attempt_count: int


def dispatch_capability_call_id(fence: DispatchFence) -> str:
    """Return the stable physical-attempt identity for one dispatch."""
    binding = ":".join((
        fence.task_id,
        fence.work_unit_id,
        fence.execution_decision_id,
        fence.dispatch_id,
    ))
    return f"call:dispatch:{hashlib.sha256(binding.encode('utf-8')).hexdigest()[:24]}"


def initial_external_attempt_identity(fence: DispatchFence) -> ExternalAttemptIdentity:
    return ExternalAttemptIdentity(
        capability_call_id=dispatch_capability_call_id(fence),
        attempt_number=1,
    )


def dispatch_result_id(fence: DispatchFence) -> str:
    """Return the stable Result identity for one completed Dispatch."""

    return f"result:{fence.dispatch_id}"


def _materialize_checked_result_projection(
    payload: dict[str, Any],
    *,
    result_id: str,
    occurred_at: datetime,
) -> dict[str, Any]:
    result_row = next(
        (row for row in payload.get("results", []) if row.get("resultId") == result_id),
        None,
    )
    if result_row is None or result_row.get("checkStatus") != "passed":
        return payload
    from app.modules.target_output_reuse_materialization import (
        materialize_accepted_reuse_judgments,
        record_result_scientific_state_versions,
    )

    payload = materialize_accepted_reuse_judgments(
        payload, result_id=result_id, occurred_at=occurred_at
    )
    check_result = result_row.get("checkResult")
    projection = (
        check_result.get("scientificStateProjection")
        if isinstance(check_result, Mapping)
        else None
    )
    if not isinstance(projection, Mapping):
        return payload
    declaration = projection.get("declaration")
    facts = projection.get("facts")
    artifact_refs = result_row.get("artifactRefs") or []
    if not isinstance(declaration, Mapping) or not isinstance(facts, Mapping) or not artifact_refs:
        raise ResearchOrchestrationPersistenceError(
            "result_scientific_state_projection_invalid"
        )
    work = next(
        row for row in payload["workUnits"]
        if row["workUnitId"] == result_row["workUnitId"]
    )
    before_versions = {
        int(row["scientificStateVersion"])
        for row in payload.get("scientificStates", [])
    }
    from app.modules.scientific_state_fact_projection import (
        materialize_scientific_state_projection,
    )

    materialized = materialize_scientific_state_projection(
        task_payload=payload,
        declaration=declaration,
        projected_facts=facts,
        source_artifact_id=str(artifact_refs[0]),
        research_design_version=int(work["researchDesignVersion"]),
        occurred_at=occurred_at,
    )
    record_result_scientific_state_versions(materialized, result_id, before_versions)
    return materialized


@dataclass(frozen=True)
class ExecutionContinuationDirective:
    action: str
    expected_task_revision: int
    next_instance_id: str | None
    selection_request_id: str | None
    reason_code: str | None


class SettingsLike(Protocol):
    persistence_backend: str
    research_orchestration_v1: bool
    postgres_host: str
    postgres_port: int
    postgres_user: str
    postgres_password: str
    postgres_database: str


ConnectionFactory = Callable[[], ContextManager[psycopg.Connection[Any]]]


def _confirmed_automatic_research(task: ResearchTaskV12) -> bool:
    """Return True only inside an active confirmed automatic-research Design."""
    active_ref = task.active_research_design_ref
    if active_ref is None or task.execution_mode is not TaskExecutionMode.AUTOMATIC_RESEARCH:
        return False
    return any(
        design.research_design_id == active_ref.research_design_id
        and design.version == active_ref.version
        and design.status == ResearchDesignStatus.CONFIRMED
        for design in task.research_design_versions
    )


class ResearchOrchestrationUnitOfWork:
    """One PostgreSQL connection and transaction for Task CAS plus outbox.

    Production transactions stamp the execution namespace into a transaction-local
    PostgreSQL setting. The dispatch ownership trigger uses that setting as the
    database-level fence, so legacy/foreign workers cannot mutate namespaced rows
    even when they share the same credentials and physical database.
    """

    def __init__(
        self,
        connection_factory: ConnectionFactory,
        *,
        execution_namespace: str | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._execution_namespace = (execution_namespace or "").strip()

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection[Any]]:
        with self._connection_factory() as connection:
            if self._execution_namespace:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('openair.execution_namespace', %s, true)",
                        (self._execution_namespace,),
                    )
            yield connection


class ResearchOrchestrationRepository:
    def __init__(
        self,
        *,
        connection_factory: ConnectionFactory | None = None,
        runtime_settings: SettingsLike = settings,
    ) -> None:
        self._settings = runtime_settings
        self._connection_factory = connection_factory or self._connect
        self._uow = ResearchOrchestrationUnitOfWork(
            self._connection_factory,
            execution_namespace=getattr(runtime_settings, "execution_namespace", None),
        )

    @staticmethod
    def _execution_stop_active(raw_payload: Mapping[str, Any]) -> bool:
        receipt = raw_payload.get("stopReceipt")
        return (
            isinstance(receipt, Mapping)
            and receipt.get("status") == "stopped"
            and receipt.get("active") is True
        )

    @staticmethod
    def _latest_lifecycle_control_generation(raw_payload: Mapping[str, Any]) -> int:
        generations = [
            receipt.get("controlGeneration")
            for key in ("stopReceipt", "resumeReceipt")
            if isinstance((receipt := raw_payload.get(key)), Mapping)
            and type(receipt.get("controlGeneration")) is int
        ]
        return max(generations, default=0)

    @staticmethod
    def _execution_policy_token(policy: Mapping[str, Any]) -> str:
        return (
            f"execution-policy.v{int(policy['policy_version'])}."
            f"{str(policy['current_mode'])}"
        )

    def _locked_execution_policy(
        self,
        cursor: psycopg.Cursor[Any],
        *,
        task: ResearchTaskV12,
        owner_id: str,
        workspace_id: str,
        changed_by: str,
    ) -> dict[str, Any]:
        cursor.execute(
            "SELECT * FROM openair_task_execution_policies WHERE task_id = %s FOR UPDATE",
            (task.task_id,),
        )
        policy = cursor.fetchone()
        if policy is None:
            cursor.execute(
                """INSERT INTO openair_task_execution_policies
                       (task_id, owner_id, workspace_id, current_mode, policy_version, changed_by)
                   VALUES (%s, %s, %s, %s, 1, %s)
                   RETURNING *""",
                (
                    task.task_id,
                    owner_id,
                    workspace_id,
                    task.execution_mode.value,
                    changed_by,
                ),
            )
            policy = cursor.fetchone()
        if (
            policy is None
            or policy["owner_id"] != owner_id
            or policy["workspace_id"] != workspace_id
        ):
            raise ResearchTaskNotFoundError("research task not found")
        return dict(policy)

    def _apply_pending_execution_policy_at_boundary(
        self,
        cursor: psycopg.Cursor[Any],
        *,
        task: ResearchTaskV12,
        owner_id: str,
        workspace_id: str,
        changed_by: str,
    ) -> tuple[ResearchTaskV12, dict[str, Any]]:
        policy = self._locked_execution_policy(
            cursor,
            task=task,
            owner_id=owner_id,
            workspace_id=workspace_id,
            changed_by=changed_by,
        )
        if policy.get("pending_mode") is not None:
            cursor.execute(
                """UPDATE openair_task_execution_policies
                      SET current_mode = pending_mode,
                          policy_version = pending_policy_version,
                          pending_mode = NULL,
                          pending_policy_version = NULL,
                          applied_at = NOW()
                    WHERE task_id = %s
                    RETURNING *""",
                (task.task_id,),
            )
            policy = dict(cursor.fetchone())
        payload = task.model_dump(mode="json", by_alias=True)
        payload.update(
            {
                "executionMode": policy["current_mode"],
                "pendingExecutionMode": policy.get("pending_mode"),
                "executionPolicyVersion": self._execution_policy_token(policy),
            }
        )
        return ResearchTaskV12.model_validate(payload), policy

    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection[Any]]:
        conninfo = (
            f"host={self._settings.postgres_host} port={self._settings.postgres_port} "
            f"dbname={self._settings.postgres_database} user={self._settings.postgres_user} "
            "connect_timeout=2"
        )
        connection = psycopg.connect(
            conninfo,
            password=self._settings.postgres_password,
            row_factory=dict_row,
        )
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def load(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> ResearchTaskV12:
        return self._load_snapshot(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            validation_context=None,
        )

    def load_read_only(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> ResearchTaskV12:
        """Load one historical ResearchDesign snapshot without command authority."""
        return self._load_snapshot(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            validation_context={READ_ONLY_LEGACY_RESEARCH_DESIGN: True},
        )

    def execution_stop_active(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> bool:
        """Read the durable stop receipt without changing Task or Dispatch state."""
        self._require_postgres()
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                _task, raw_payload = self._load_locked_task(
                    cursor,
                    task_id=task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                return self._execution_stop_active(raw_payload)

    def finalize_stopped_persisted_dispatch_result(
        self,
        fence: AuthorizedDispatch,
        *,
        owner_id: str,
        workspace_id: str,
        content_sha256: str,
    ) -> bool:
        """Close a stopped dispatch from its already-canonical persisted result.

        This is deliberately narrower than normal result recording: it is only
        for a user-stopped task whose deterministic result and Artifact were
        already persisted before an unknown-outcome projection.  It never
        validates new provider output, releases dependencies, or resumes work.
        """
        self._require_write_enabled()
        self._require_postgres()
        self._require_authorized_fence(fence)
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                task, raw_payload = self._load_locked_task(
                    cursor,
                    task_id=fence.task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                if not self._execution_stop_active(raw_payload):
                    return False
                result_id = dispatch_result_id(fence)
                call_id = fence.external_attempt.capability_call_id
                artifact_id = f"artifact:{fence.dispatch_id}"
                result = next((row for row in task.results if row.result_id == result_id), None)
                call = next(
                    (row for row in task.capability_calls if row.capability_call_id == call_id),
                    None,
                )
                work = next(
                    (row for row in task.work_units if row.work_unit_id == fence.work_unit_id),
                    None,
                )
                artifact = next((row for row in task.artifacts if row.artifact_id == artifact_id), None)
                if (
                    result is None
                    or call is None
                    or work is None
                    or artifact is None
                    or result.work_unit_id != fence.work_unit_id
                    or result.capability_call_id != call_id
                    or result.storage_type != fence.result_storage_type
                    or result.storage_scope != fence.result_storage_scope
                    or result.storage_ref != fence.result_storage_ref
                    or result.content_sha256 != content_sha256
                    or artifact_id not in result.artifact_refs
                ):
                    return False
                cursor.execute(
                    """SELECT EXISTS (
                           SELECT 1 FROM openair_artifacts
                            WHERE artifact_id = %s
                              AND owner_id = %s
                              AND workspace_id = %s
                       ) AS artifact_exists,
                       EXISTS (
                           SELECT 1 FROM openair_artifact_lineage
                            WHERE task_id = %s AND artifact_id = %s
                       ) AS lineage_exists""",
                    (artifact_id, owner_id, workspace_id, fence.task_id, artifact_id),
                )
                canonical = cursor.fetchone()
                if not canonical["artifact_exists"] or not canonical["lineage_exists"]:
                    return False
                if call.status.value not in {"external_result_unknown", "succeeded"}:
                    return False
                if work.status.value not in {"external_result_unknown", WorkUnitStatus.COMPLETED.value}:
                    return False
                if call.status.value == "external_result_unknown" or work.status.value == "external_result_unknown":
                    payload = task.model_dump(mode="json", by_alias=True)
                    persisted_call = next(
                        row for row in payload["capabilityCalls"]
                        if row["capabilityCallId"] == call_id
                    )
                    persisted_work = next(
                        row for row in payload["workUnits"]
                        if row["workUnitId"] == fence.work_unit_id
                    )
                    now = datetime.now(timezone.utc).isoformat()
                    persisted_call.update({
                        "status": "succeeded",
                        "outputArtifactRefs": list(result.artifact_refs),
                        "finishedAt": now,
                    })
                    persisted_work.update({
                        "status": WorkUnitStatus.COMPLETED.value,
                        "terminalReasonCode": None,
                        "finishedAt": now,
                    })
                    self._write_locked_task(
                        cursor,
                        raw_payload,
                        ResearchTaskV12.model_validate(payload),
                    )
                cursor.execute(
                    """UPDATE openair_dispatch
                          SET status = 'submitted', updated_at = clock_timestamp()
                        WHERE dispatch_id = %(dispatch_id)s
                          AND task_id = %(task_id)s
                          AND execution_decision_id = %(execution_decision_id)s
                          AND work_unit_id = %(work_unit_id)s
                          AND idempotency_key = %(idempotency_key)s
                          AND status = 'submission_authorized'
                          AND lease_owner = %(lease_owner)s
                          AND attempt_count = %(attempt_count)s
                          AND authorization_task_revision = %(authorization_task_revision)s
                          AND result_storage_ref IS NOT NULL""",
                    fence.__dict__,
                )
                if cursor.rowcount != 1:
                    return False
                cursor.execute(
                    """UPDATE openair_external_execution x
                          SET external_state = 'completed',
                              receipt = d.result_storage_ref,
                              remote_run_id = d.result_storage_ref,
                              failure_code = NULL,
                              updated_at = clock_timestamp()
                         FROM openair_dispatch d
                        WHERE x.dispatch_id = d.dispatch_id
                          AND d.dispatch_id = %(dispatch_id)s
                          AND d.task_id = %(task_id)s
                          AND x.task_id = d.task_id
                          AND x.execution_decision_id = %(execution_decision_id)s
                          AND x.work_unit_id = %(work_unit_id)s
                          AND x.capability_call_id = %(capability_call_id)s
                          AND d.result_storage_ref IS NOT NULL""",
                    {**fence.__dict__, "capability_call_id": call_id},
                )
                if cursor.rowcount != 1:
                    raise ResearchOrchestrationPersistenceError(
                        "external_execution_completion_missing"
                    )
                return True

    def _load_snapshot(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        validation_context: Mapping[str, object] | None,
    ) -> ResearchTaskV12:
        self._require_postgres()
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT payload, revision
                       FROM openair_tasks
                       WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                         AND status <> 'deleted'""",
                    (task_id, owner_id, workspace_id),
                )
                row = cursor.fetchone()
        if row is None:
            raise ResearchTaskNotFoundError("research_task_not_found")
        raw_payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
        database_revision = int(row["revision"])
        if raw_payload.get("revision") != database_revision:
            raise PayloadRevisionMismatchError("task_payload_revision_mismatch")
        try:
            return ResearchTaskV12.model_validate(
                _extract_contract_payload(raw_payload),
                context=validation_context,
            )
        except (KeyError, ValidationError) as exc:
            raise ResearchOrchestrationPersistenceError("invalid_research_orchestration_payload") from exc

    def read_execution_input_context(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        research_design_version: int,
        input_artifact_refs: list[str] | tuple[str, ...],
        input_artifact_snapshots: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
        invocation_context: Mapping[str, Any] | None = None,
        max_total_bytes: int = 1_000_000,
    ) -> dict[str, Any]:
        """Read accepted immutable inputs for an ordinary not-yet-frozen WorkUnit."""
        return self._read_execution_input_context(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            research_design_version=research_design_version,
            input_artifact_refs=input_artifact_refs,
            input_artifact_snapshots=input_artifact_snapshots,
            invocation_context=invocation_context,
            max_total_bytes=max_total_bytes,
            allowed_artifact_states=frozenset({ArtifactState.USABLE}),
        )

    def read_reviewer_execution_input_context(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        research_design_version: int,
        input_artifact_refs: list[str] | tuple[str, ...],
        input_artifact_snapshots: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
        invocation_context: Mapping[str, Any] | None = None,
        max_total_bytes: int = 1_000_000,
    ) -> dict[str, Any]:
        """Read verified producer inputs that are awaiting the review being prepared."""
        return self._read_execution_input_context(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            research_design_version=research_design_version,
            input_artifact_refs=input_artifact_refs,
            input_artifact_snapshots=input_artifact_snapshots,
            invocation_context=invocation_context,
            max_total_bytes=max_total_bytes,
            allowed_artifact_states=frozenset(
                {ArtifactState.NEEDS_REVIEW, ArtifactState.USABLE}
            ),
        )

    def _read_execution_input_context(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        research_design_version: int,
        input_artifact_refs: list[str] | tuple[str, ...],
        input_artifact_snapshots: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
        invocation_context: Mapping[str, Any] | None,
        max_total_bytes: int,
        allowed_artifact_states: frozenset[ArtifactState],
    ) -> dict[str, Any]:
        """Resolve confirmed Design context and verify exact upstream Artifact bytes.

        Artifact-state policy is supplied by the caller role.  Ordinary execution
        remains restricted to accepted ``usable`` artifacts; Reviewer preparation
        may additionally inspect ``needs_review`` producer output because review is
        the authority that decides whether that output becomes accepted.
        """
        self._require_postgres()
        if max_total_bytes < 1:
            raise ResearchOrchestrationPersistenceError(
                "execution_input_material_budget_invalid"
            )

        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                task, _raw_payload = self._load_task_for_execution_input(
                    cursor,
                    task_id=task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                design = next(
                    (
                        row
                        for row in task.research_design_versions
                        if row.version == research_design_version
                        and row.status is ResearchDesignStatus.CONFIRMED
                    ),
                    None,
                )
                if design is None:
                    raise ResearchOrchestrationPersistenceError(
                        "execution_input_research_design_not_confirmed"
                    )
                refs = self._validate_execution_input_refs(
                    input_artifact_refs,
                    input_artifact_snapshots,
                )
                materials = self._read_verified_execution_artifacts(
                    cursor,
                    task=task,
                    task_id=task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    refs=refs,
                    input_artifact_snapshots=input_artifact_snapshots,
                    max_total_bytes=max_total_bytes,
                    allowed_artifact_states=allowed_artifact_states,
                )
        latest_state = latest_scientific_state_for_design(
            task.scientific_states,
            research_design_version=research_design_version,
        )
        return {
            "canonicalUserQuery": design.goal,
            "executionIdentity": {"task_id": task_id},
            "invocationContext": dict(invocation_context or {}),
            "scientificState": (
                {} if latest_state is None else dict(latest_state.scientific_facts)
            ),
            "scientificStatePresent": latest_state is not None,
            "scientificStateVersion": (
                None if latest_state is None else latest_state.scientific_state_version
            ),
            "inputArtifactRefs": sorted(refs),
            "artifacts": materials,
            "inputMaterialWarnings": (
                ["execution_input_material_budget_exceeded"]
                if sum(len(row["content"]) for row in materials) > max_total_bytes else []
            ),
        }

    def read_direct_skill_input_context_for_dispatch(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        execution_decision_id: str,
        work_unit_id: str,
        max_total_bytes: int = 1_000_000,
        _expected_executor_type: str = "skill",
    ) -> dict[str, Any]:
        """Read the exact frozen input context for one persisted live WorkUnit."""
        self._require_postgres()
        if max_total_bytes < 1:
            raise ResearchOrchestrationPersistenceError("skill_input_material_budget_invalid")

        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                task, _raw_payload = self._load_task_for_execution_input(
                    cursor,
                    task_id=task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                decision = next(
                    (
                        row
                        for row in task.execution_decisions
                        if row.execution_decision_id == execution_decision_id
                    ),
                    None,
                )
                work = next(
                    (row for row in task.work_units if row.work_unit_id == work_unit_id),
                    None,
                )
                if (
                    decision is None
                    or work is None
                    or work.task_id != task_id
                    or work.execution_decision_id != execution_decision_id
                    or work.source_mode is not WorkUnitSourceMode.LIVE
                    or not work.dispatchable
                    or work.executor_ref.executor_type != _expected_executor_type
                    or work.registry_snapshot_ref != decision.registry_snapshot_ref
                ):
                    raise ResearchOrchestrationPersistenceError(
                        "skill_input_dispatch_scope_invalid"
                    )
                design = next(
                    (
                        row
                        for row in task.research_design_versions
                        if row.version == work.research_design_version
                        and row.status is ResearchDesignStatus.CONFIRMED
                    ),
                    None,
                )
                if design is None:
                    raise ResearchOrchestrationPersistenceError(
                        "skill_input_research_design_not_confirmed"
                    )
                snapshots = [
                    row.model_dump(mode="json", by_alias=True)
                    for row in work.input_artifact_snapshots
                ]
                refs = self._validate_execution_input_refs(
                    work.input_artifact_refs,
                    snapshots,
                )
                materials = self._read_verified_execution_artifacts(
                    cursor,
                    task=task,
                    task_id=task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    refs=refs,
                    input_artifact_snapshots=snapshots,
                    max_total_bytes=max_total_bytes,
                )
        if work.input_scientific_state_version is None:
            input_state = latest_scientific_state_for_design(
                task.scientific_states,
                research_design_version=work.research_design_version,
            )
        else:
            input_state = scientific_state_at_version_for_design(
                task.scientific_states,
                research_design_version=work.research_design_version,
                scientific_state_version_value=work.input_scientific_state_version,
            )
            if input_state is None:
                raise ResearchOrchestrationPersistenceError(
                    "skill_input_scientific_state_snapshot_missing"
                )
        return {
            "canonicalUserQuery": design.goal,
            "executionIdentity": {"task_id": task_id},
            "invocationContext": dict(work.invocation_context),
            "scientificState": (
                {} if input_state is None else dict(input_state.scientific_facts)
            ),
            "scientificStatePresent": input_state is not None,
            "scientificStateVersion": (
                None if input_state is None else input_state.scientific_state_version
            ),
            "inputArtifactRefs": sorted(refs),
            "artifacts": materials,
            "inputMaterialWarnings": (
                ["execution_input_material_budget_exceeded"]
                if sum(len(row["content"]) for row in materials) > max_total_bytes else []
            ),
        }

    def _load_task_for_execution_input(
        self,
        cursor: Any,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> tuple[ResearchTaskV12, dict[str, Any]]:
        cursor.execute(
            """SELECT payload, revision
               FROM openair_tasks
               WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                 AND status <> 'deleted'""",
            (task_id, owner_id, workspace_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise ResearchTaskNotFoundError("research_task_not_found")
        return self._task_from_database_payload(row["payload"], int(row["revision"]))

    @staticmethod
    def _validate_execution_input_refs(
        input_artifact_refs: list[str] | tuple[str, ...],
        input_artifact_snapshots: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    ) -> list[str]:
        refs = [str(ref) for ref in input_artifact_refs]
        if len(refs) != len(set(refs)):
            raise ResearchOrchestrationPersistenceError("execution_input_artifact_refs_invalid")
        snapshots = [dict(row) for row in input_artifact_snapshots]
        snapshot_ids = [str(row.get("artifactId") or "") for row in snapshots]
        if len(snapshot_ids) != len(set(snapshot_ids)) or set(snapshot_ids) != set(refs):
            raise ResearchOrchestrationPersistenceError("execution_input_artifact_snapshots_invalid")
        return refs

    def _read_verified_execution_artifacts(
        self,
        cursor: Any,
        *,
        task: ResearchTaskV12,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        refs: list[str],
        input_artifact_snapshots: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
        max_total_bytes: int,
        allowed_artifact_states: frozenset[ArtifactState] = frozenset(
            {ArtifactState.USABLE}
        ),
    ) -> list[dict[str, Any]]:
        artifacts = {row.artifact_id: row for row in task.artifacts if row.artifact_id in refs}
        if set(artifacts) != set(refs):
            raise ResearchOrchestrationPersistenceError("execution_input_artifact_reference_not_found")
        snapshots = {str(row.get("artifactId") or ""): dict(row) for row in input_artifact_snapshots}
        calls = {row.capability_call_id: row for row in task.capability_calls}
        work_units = {row.work_unit_id: row for row in task.work_units}
        source_calls: dict[str, Any] = {}
        for artifact in artifacts.values():
            producer = work_units.get(artifact.work_unit_id)
            snapshot = snapshots.get(artifact.artifact_id) or {}
            if (
                artifact.artifact_state not in allowed_artifact_states
                or producer is None
                or producer.source_mode is not WorkUnitSourceMode.LIVE
                or producer.status is not WorkUnitStatus.COMPLETED
                or snapshot.get("artifactType") != artifact.artifact_type
                or snapshot.get("version") != artifact.version
            ):
                raise ResearchOrchestrationPersistenceError(
                    "execution_input_artifact_domain_scope_invalid"
                )
            validator = artifact.validator_result
            validator_status = validator.get("status")
            revision_approved = (
                validator_status == "revision_human_approved"
                and validator.get("revisionSource") in {"user_edit", "restore"}
            )
            validator_digest = _normalized_sha256(validator.get("contentSha256"))
            snapshot_digest = _normalized_sha256(snapshot.get("contentSha256"))
            if (
                (validator_status != "passed" and not revision_approved)
                or not isinstance(validator.get("artifactContract"), str)
                or not validator["artifactContract"].strip()
                or _normalized_sha256(validator.get("artifactSchemaDigest")) is None
                or validator_digest is None
                or snapshot_digest != validator_digest
            ):
                raise ResearchOrchestrationPersistenceError(
                    "execution_input_artifact_validator_invalid"
                )
            source_call_id = artifact.source_capability_call_id
            if source_call_id is not None:
                call = calls.get(source_call_id)
                if (
                    call is None
                    or call.work_unit_id != artifact.work_unit_id
                    or call.substage_instance_id != artifact.substage_instance_id
                    or call.status.value != "succeeded"
                    or artifact.artifact_id not in call.output_artifact_refs
                    or (
                        call.capability_type is CapabilityType.SKILL
                        and (
                            producer.executor_ref.executor_type != "skill"
                            or producer.executor_ref.executor_id != call.capability_id
                        )
                    )
                ):
                    raise ResearchOrchestrationPersistenceError(
                        "execution_input_artifact_source_call_invalid"
                    )
                source_calls[artifact.artifact_id] = call

        if refs:
            cursor.execute(
                """SELECT a.artifact_id, a.mime_type, a.storage_type, a.storage_scope,
                          a.storage_ref, a.content_size, a.content_sha256,
                          a.source_capability_call_id, a.source_mode, a.metadata,
                          lineage.work_unit_id AS lineage_work_unit_id,
                          lineage.execution_decision_id AS lineage_execution_decision_id,
                          lineage.substage_instance_id AS lineage_substage_instance_id,
                          lineage.source_capability_call_id AS lineage_source_call_id,
                          lineage.direct_work_unit_output,
                          unit.source_mode AS lineage_work_unit_source_mode,
                          unit.dispatchable AS lineage_work_unit_dispatchable,
                          call.capability_call_id AS lineage_call_id
                   FROM openair_artifacts AS a
                   JOIN openair_artifact_lineage AS lineage
                     ON lineage.task_id = a.source_task_id
                    AND lineage.artifact_id = a.artifact_id
                   JOIN openair_work_unit_lineage AS unit
                     ON unit.task_id = lineage.task_id
                    AND unit.work_unit_id = lineage.work_unit_id
                    AND unit.execution_decision_id = lineage.execution_decision_id
                    AND unit.substage_instance_id = lineage.substage_instance_id
                    AND unit.source_mode = lineage.source_mode
                   LEFT JOIN openair_capability_call_lineage AS call
                     ON call.task_id = lineage.task_id
                    AND call.capability_call_id = lineage.source_capability_call_id
                    AND call.work_unit_id = lineage.work_unit_id
                    AND call.substage_instance_id = lineage.substage_instance_id
                    AND call.source_mode = lineage.source_mode
                   WHERE a.artifact_id = ANY(%s)
                     AND a.owner_id = %s AND a.workspace_id = %s
                     AND a.source_task_id = %s""",
                (refs, owner_id, workspace_id, task_id),
            )
            rows = {row["artifact_id"]: row for row in cursor.fetchall()}
        else:
            rows = {}
        if set(rows) != set(refs):
            raise ResearchOrchestrationPersistenceError(
                "execution_input_artifact_catalog_scope_invalid"
            )

        materials: list[dict[str, Any]] = []
        for artifact_id in sorted(refs):
            artifact = artifacts[artifact_id]
            producer = work_units[artifact.work_unit_id]
            row = rows[artifact_id]
            validator = artifact.validator_result
            metadata = row["metadata"] if isinstance(row["metadata"], dict) else {}
            source_call_id = artifact.source_capability_call_id
            call = source_calls.get(artifact_id)
            if (
                row["source_mode"] != "live"
                or row["storage_type"] not in {"local", "external"}
                or not isinstance(row["storage_ref"], str)
                or not row["storage_ref"].strip()
                or row["lineage_work_unit_source_mode"] != "live"
                or row["lineage_work_unit_dispatchable"] is not True
                or row["lineage_work_unit_id"] != artifact.work_unit_id
                or row["lineage_execution_decision_id"] != producer.execution_decision_id
                or row["lineage_substage_instance_id"] != artifact.substage_instance_id
                or row["source_capability_call_id"] != source_call_id
                or row["lineage_source_call_id"] != source_call_id
                or row["direct_work_unit_output"] != (source_call_id is None)
                or row["lineage_call_id"] != source_call_id
                or metadata.get("taskId") != task_id
                or metadata.get("workUnitId") != artifact.work_unit_id
                or metadata.get("artifactState") != artifact.artifact_state.value
                or metadata.get("artifactType") != artifact.artifact_type
                or metadata.get("validatorResult") != validator
                or (
                    validator.get("status") == "revision_human_approved"
                    and (
                        metadata.get("reviewSource") != "user"
                        or not str(metadata.get("reviewedAt") or "").strip()
                    )
                )
                or not _digest_matches(row["content_sha256"], validator["contentSha256"])
            ):
                raise ResearchOrchestrationPersistenceError(
                    "execution_input_artifact_catalog_verification_failed"
                )
            try:
                content = (
                    artifact_write_service.read_durable_bytes(
                        task_id,
                        row["storage_type"],
                        row["storage_ref"],
                        row.get("storage_scope"),
                    )
                    if row["storage_type"] == "external"
                    else artifact_write_service.read_durable_bytes(
                        task_id,
                        row["storage_type"],
                        row["storage_ref"],
                    )
                )
            except ArtifactStorageError as exc:
                raise ResearchOrchestrationPersistenceError(
                    "execution_input_artifact_storage_unavailable"
                ) from exc
            if (
                not isinstance(content, bytes)
                or not isinstance(row["content_size"], int)
                or row["content_size"] < 1
                or len(content) != row["content_size"]
                or not _digest_matches(hashlib.sha256(content).hexdigest(), row["content_sha256"])
            ):
                raise ResearchOrchestrationPersistenceError(
                    "execution_input_artifact_content_verification_failed"
                )
            materials.append(
                {
                    "artifactId": artifact_id,
                    "artifactType": artifact.artifact_type,
                    "mimeType": str(row["mime_type"] or "application/octet-stream"),
                    "content": content,
                    "contentSha256": row["content_sha256"],
                    "currentVersionId": metadata.get("currentVersionId"),
                    "artifactContract": validator["artifactContract"],
                    "sourceCapabilityType": None if call is None else call.capability_type.value,
                    "sourceCapabilityId": None if call is None else call.capability_id,
                    "sourceCapabilityDefinitionVersion": (
                        producer.executor_ref.definition_version
                        if call is not None
                        and call.capability_type is CapabilityType.SKILL
                        and producer.executor_ref.executor_type == "skill"
                        and producer.executor_ref.executor_id == call.capability_id
                        else None
                    ),
                    "sourceCapabilityCallId": source_call_id,
                    "sourceBindingRefs": [
                        ref.model_dump(mode="json", by_alias=True)
                        for decision in task.execution_decisions
                        if decision.execution_decision_id == producer.execution_decision_id
                        for assignment in decision.assignments
                        if assignment.assignment_id == producer.assignment_id
                        for ref in assignment.source_binding_refs
                    ],
                    "sourceRefs": list(artifact.source_refs),
                }
            )
        return materials

    def read_agent_input_materials_for_dispatch(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        execution_decision_id: str,
        work_unit_id: str,
        max_total_chars: int = 120_000,
    ) -> list[dict[str, Any]]:
        """Materialize the current canonical Artifact bodies for one primary Agent dispatch."""
        if max_total_chars < 1:
            raise ResearchOrchestrationPersistenceError("agent_input_material_budget_invalid")
        context = self.read_direct_skill_input_context_for_dispatch(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            execution_decision_id=execution_decision_id,
            work_unit_id=work_unit_id,
            max_total_bytes=max_total_chars * 4,
            _expected_executor_type="agent",
        )
        remaining = max_total_chars
        materials: list[dict[str, Any]] = []
        for row in context.get("artifacts") or []:
            content = row.get("content") if isinstance(row, dict) else None
            if not isinstance(content, bytes):
                raise ResearchOrchestrationPersistenceError("agent_input_artifact_content_missing")
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ResearchOrchestrationPersistenceError("agent_input_artifact_not_text") from exc
            if not text or len(text) > remaining:
                raise ResearchOrchestrationPersistenceError("agent_input_material_budget_exceeded")
            materials.append({
                "artifactId": row.get("artifactId"),
                "artifactType": row.get("artifactType"),
                "mimeType": row.get("mimeType"),
                "content": text,
                "contentSha256": row.get("contentSha256"),
                "currentVersionId": row.get("currentVersionId"),
            })
            remaining -= len(text)
        return materials

    def read_review_materials_for_dispatch(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        execution_decision_id: str,
        reviewer_work_unit_id: str,
        max_total_chars: int = 60_000,
    ) -> list[dict[str, Any]]:
        """Read verified producer artifacts for one persisted reviewer WorkUnit."""
        self._require_postgres()
        if max_total_chars < 1:
            raise ResearchOrchestrationPersistenceError("review_material_budget_invalid")

        task = self.load(task_id=task_id, owner_id=owner_id, workspace_id=workspace_id)
        decision = next(
            (
                row
                for row in task.execution_decisions
                if row.execution_decision_id == execution_decision_id
            ),
            None,
        )
        reviewer = next(
            (row for row in task.work_units if row.work_unit_id == reviewer_work_unit_id),
            None,
        )
        assignment = None if decision is None or reviewer is None else next(
            (row for row in decision.assignments if row.assignment_id == reviewer.assignment_id),
            None,
        )
        if (
            decision is None
            or reviewer is None
            or assignment is None
            or assignment.role is not AssignmentRole.REVIEWER
            or reviewer.task_id != task_id
            or reviewer.execution_decision_id != execution_decision_id
            or reviewer.substage_instance_id != decision.substage_instance_id
            or reviewer.source_mode is not WorkUnitSourceMode.LIVE
            or not reviewer.dispatchable
            or reviewer.executor_ref != assignment.executor_ref
        ):
            raise ResearchOrchestrationPersistenceError("review_dispatch_scope_invalid")

        refs = list(reviewer.input_artifact_refs)
        if not refs or len(refs) != len(set(refs)):
            raise ResearchOrchestrationPersistenceError("review_artifact_refs_invalid")
        dependency_assignments = set(assignment.dependency_assignment_ids)
        dependency_work_units = {
            row.work_unit_id
            for row in task.work_units
            if row.assignment_id in dependency_assignments
            and row.execution_decision_id == execution_decision_id
            and row.substage_instance_id == reviewer.substage_instance_id
            and row.source_mode is WorkUnitSourceMode.LIVE
        }
        if not dependency_assignments or not dependency_work_units:
            raise ResearchOrchestrationPersistenceError("review_dependency_scope_invalid")
        if set(reviewer.dependency_work_unit_ids) != dependency_work_units:
            raise ResearchOrchestrationPersistenceError("review_dependency_scope_invalid")

        artifacts = {row.artifact_id: row for row in task.artifacts if row.artifact_id in refs}
        if set(artifacts) != set(refs):
            raise ResearchOrchestrationPersistenceError("review_artifact_reference_not_found")
        calls = {row.capability_call_id: row for row in task.capability_calls}
        work_units = {row.work_unit_id: row for row in task.work_units}
        for artifact in artifacts.values():
            producer = work_units.get(artifact.work_unit_id)
            # Reviewer materials are readable while the producer artifact is still
            # awaiting durable review (needs_review). Approval later promotes it to
            # usable; already-usable refs remain readable for re-entry.
            if (
                artifact.artifact_state
                not in {ArtifactState.NEEDS_REVIEW, ArtifactState.USABLE}
                or artifact.work_unit_id not in dependency_work_units
                or artifact.substage_instance_id != reviewer.substage_instance_id
                or producer is None
                or producer.execution_decision_id != execution_decision_id
                or producer.source_mode is not WorkUnitSourceMode.LIVE
                or producer.status is not WorkUnitStatus.COMPLETED
            ):
                raise ResearchOrchestrationPersistenceError("review_artifact_domain_scope_invalid")
            validator = artifact.validator_result
            if (
                validator.get("status") != "passed"
                or not isinstance(validator.get("artifactContract"), str)
                or not validator["artifactContract"].strip()
                or _normalized_sha256(validator.get("artifactSchemaDigest")) is None
                or _normalized_sha256(validator.get("contentSha256")) is None
            ):
                raise ResearchOrchestrationPersistenceError("review_artifact_validator_invalid")
            if artifact.source_capability_call_id is not None:
                call = calls.get(artifact.source_capability_call_id)
                if (
                    call is None
                    or call.work_unit_id != artifact.work_unit_id
                    or call.substage_instance_id != artifact.substage_instance_id
                    or call.status.value != "succeeded"
                    or artifact.artifact_id not in call.output_artifact_refs
                ):
                    raise ResearchOrchestrationPersistenceError("review_artifact_source_call_invalid")

        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT a.artifact_id, a.title, a.mime_type, a.storage_type,
                              a.storage_scope, a.storage_ref, a.content_size, a.content_sha256,
                              a.source_capability_call_id, a.source_mode, a.metadata,
                              lineage.work_unit_id AS lineage_work_unit_id,
                              lineage.execution_decision_id AS lineage_execution_decision_id,
                              lineage.substage_instance_id AS lineage_substage_instance_id,
                              lineage.source_capability_call_id AS lineage_source_call_id,
                              lineage.direct_work_unit_output,
                              unit.source_mode AS lineage_work_unit_source_mode,
                              unit.dispatchable AS lineage_work_unit_dispatchable,
                              call.capability_call_id AS lineage_call_id
                       FROM openair_artifacts AS a
                       JOIN openair_artifact_lineage AS lineage
                         ON lineage.task_id = a.source_task_id
                        AND lineage.artifact_id = a.artifact_id
                       JOIN openair_work_unit_lineage AS unit
                         ON unit.task_id = lineage.task_id
                        AND unit.work_unit_id = lineage.work_unit_id
                        AND unit.execution_decision_id = lineage.execution_decision_id
                        AND unit.substage_instance_id = lineage.substage_instance_id
                        AND unit.source_mode = lineage.source_mode
                       LEFT JOIN openair_capability_call_lineage AS call
                         ON call.task_id = lineage.task_id
                        AND call.capability_call_id = lineage.source_capability_call_id
                        AND call.work_unit_id = lineage.work_unit_id
                        AND call.substage_instance_id = lineage.substage_instance_id
                        AND call.source_mode = lineage.source_mode
                       WHERE a.artifact_id = ANY(%s)
                         AND a.owner_id = %s AND a.workspace_id = %s
                         AND a.source_task_id = %s""",
                    (refs, owner_id, workspace_id, task_id),
                )
                rows = {row["artifact_id"]: row for row in cursor.fetchall()}
        if set(rows) != set(refs):
            raise ResearchOrchestrationPersistenceError("review_artifact_catalog_scope_invalid")

        remaining = max_total_chars
        materials: list[dict[str, Any]] = []
        for artifact_id in refs:
            artifact = artifacts[artifact_id]
            row = rows[artifact_id]
            validator = artifact.validator_result
            metadata = row["metadata"] if isinstance(row["metadata"], dict) else {}
            catalog_validator = metadata.get("validatorResult")
            source_call_id = artifact.source_capability_call_id
            if (
                row["source_mode"] != "live"
                or row["storage_type"] not in {"local", "external"}
                or not isinstance(row["storage_ref"], str)
                or not row["storage_ref"].strip()
                or row["lineage_work_unit_source_mode"] != "live"
                or row["lineage_work_unit_dispatchable"] is not True
                or row["lineage_work_unit_id"] != artifact.work_unit_id
                or row["lineage_execution_decision_id"] != execution_decision_id
                or row["lineage_substage_instance_id"] != artifact.substage_instance_id
                or row["source_capability_call_id"] != source_call_id
                or row["lineage_source_call_id"] != source_call_id
                or row["direct_work_unit_output"] != (source_call_id is None)
                or row["lineage_call_id"] != source_call_id
                or metadata.get("taskId") != task_id
                or metadata.get("workUnitId") != artifact.work_unit_id
                or metadata.get("artifactState")
                not in {
                    ArtifactState.NEEDS_REVIEW.value,
                    ArtifactState.USABLE.value,
                }
                or metadata.get("artifactType") != artifact.artifact_type
                or catalog_validator != validator
                or not _digest_matches(row["content_sha256"], validator["contentSha256"])
            ):
                raise ResearchOrchestrationPersistenceError("review_artifact_catalog_verification_failed")
            try:
                content = (
                    artifact_write_service.read_durable_bytes(
                        task_id, row["storage_type"], row["storage_ref"], row.get("storage_scope")
                    )
                    if row["storage_type"] == "external"
                    else artifact_write_service.read_durable_bytes(
                        task_id, row["storage_type"], row["storage_ref"]
                    )
                )
            except ArtifactStorageError as exc:
                raise ResearchOrchestrationPersistenceError(
                    "review_artifact_storage_unavailable"
                ) from exc
            if (
                not isinstance(content, bytes)
                or not isinstance(row["content_size"], int)
                or row["content_size"] < 1
                or len(content) != row["content_size"]
                or not _digest_matches(hashlib.sha256(content).hexdigest(), row["content_sha256"])
            ):
                raise ResearchOrchestrationPersistenceError(
                    "review_artifact_content_verification_failed"
                )
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ResearchOrchestrationPersistenceError("review_artifact_json_invalid") from exc
            mime_type = str(row["mime_type"] or "text/plain").lower()
            is_json = mime_type in ("application/json", "application/json; charset=utf-8", "text/json")
            # Canonical JSON artifacts are held to a stricter, tamper-evident contract
            # (must be a dict whose canonical re-serialization equals the stored bytes).
            # Skill runtime legitimately produces markdown / yaml / text artifacts
            # (e.g. sdr intent_understanding's research-config); those are passed to the
            # reviewer agent verbatim — the storage-digest and validator checks above
            # already guarantee integrity, and the reviewer is an LLM that reads free text.
            if is_json:
                try:
                    document = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ResearchOrchestrationPersistenceError("review_artifact_json_invalid") from exc
                canonical = json.dumps(
                    document,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                if not isinstance(document, dict) or canonical != text:
                    raise ResearchOrchestrationPersistenceError("review_artifact_content_not_canonical")
            if len(text) > remaining:
                raise ResearchOrchestrationPersistenceError("review_artifact_content_not_canonical")
            materials.append(
                {
                    "artifactId": artifact_id,
                    "title": str(row["title"] or "Research artifact"),
                    "mimeType": str(row["mime_type"] or "text/plain"),
                    "content": text,
                    "truncated": False,
                    "contentSha256": row["content_sha256"],
                }
            )
            remaining -= len(text)
        return materials

    def lookup_selection_replay(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        selection_request_id: str,
        selection_command_hash: str,
    ) -> "SelectionCommitResult | BlockedSelectionReplay | None":
        """Return committed or durably blocked selection truth before mutable-state preflight.

        The scoped Task lock precedes the ledger lookup so a request outside the
        caller scope is indistinguishable from a missing request.
        """
        from app.modules.research_execution_decision_service import (
            BlockedSelectionReplay,
            SelectionCommitResult,
        )

        self._require_postgres()
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                task, _raw_payload = self._load_locked_task(
                    cursor,
                    task_id=task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                cursor.execute(
                    """SELECT command_hash, execution_decision_id
                       FROM openair_execution_selection_request
                       WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                         AND selection_request_id = %s
                       FOR UPDATE""",
                    (task_id, owner_id, workspace_id, selection_request_id),
                )
                replay = cursor.fetchone()
                if replay is None:
                    cursor.execute(
                        """SELECT outcome
                           FROM openair_execution_start_attempts
                           WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                             AND operation = 'research_execution_selection'
                             AND idempotency_key_sha256 = %s
                             AND outcome LIKE 'blocked:%%'
                           ORDER BY occurred_at DESC, attempt_id DESC
                           LIMIT 1
                           FOR UPDATE""",
                        (
                            task_id,
                            owner_id,
                            workspace_id,
                            hashlib.sha256(selection_request_id.encode("utf-8")).hexdigest(),
                        ),
                    )
                    blocked = cursor.fetchone()
                    if blocked is None:
                        return None
                    blocked_evidence = _parse_selection_blocked_outcome(blocked["outcome"])
                    if blocked_evidence is None:
                        return None
                    reason_code, blocked_command_hash, _instance_id, blocker_facts = blocked_evidence
                    if _blocked_outcome_expired(blocker_facts, task.revision):
                        # Task has advanced past the revision that was blocked; the stale
                        # verdict no longer applies, so let the caller re-evaluate freshly.
                        return None
                    if blocked_command_hash != selection_command_hash:
                        raise SelectionRequestPayloadConflictError(
                            "selection_request_command_conflict"
                        )
                    return BlockedSelectionReplay(
                        reason_code=reason_code,
                        facts=blocker_facts,
                    )
                if replay["command_hash"] != selection_command_hash:
                    raise SelectionRequestPayloadConflictError(
                        "selection_request_command_conflict"
                    )
                decision = next(
                    (
                        row
                        for row in task.execution_decisions
                        if row.execution_decision_id == replay["execution_decision_id"]
                    ),
                    None,
                )
                if decision is None:
                    raise ResearchOrchestrationPersistenceError(
                        "selection_request_decision_missing"
                    )
                return SelectionCommitResult(task=task, decision=decision, replayed=True)

    def lookup_selection_replay_evidence(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        selection_request_id: str,
    ) -> "SelectionReplayEvidence | None":
        """Read immutable request evidence without resolving current ready state."""
        from app.modules.research_execution_decision_service import (
            BlockedSelectionReplay,
            SelectionCommitResult,
            SelectionReplayEvidence,
        )

        self._require_postgres()
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT command_hash, execution_decision_id
                       FROM openair_execution_selection_request
                       WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                         AND selection_request_id = %s
                       FOR UPDATE""",
                    (task_id, owner_id, workspace_id, selection_request_id),
                )
                committed = cursor.fetchone()
                if committed is not None:
                    task, _raw_payload = self._load_locked_task(
                        cursor,
                        task_id=task_id,
                        owner_id=owner_id,
                        workspace_id=workspace_id,
                    )
                    decision = next(
                        (
                            row
                            for row in task.execution_decisions
                            if row.execution_decision_id == committed["execution_decision_id"]
                        ),
                        None,
                    )
                    if decision is None:
                        raise ResearchOrchestrationPersistenceError(
                            "selection_request_decision_missing"
                        )
                    return SelectionReplayEvidence(
                        instance_id=decision.substage_instance_id,
                        selection_command_hash=committed["command_hash"],
                        replay=SelectionCommitResult(
                            task=task,
                            decision=decision,
                            replayed=True,
                        ),
                    )

                cursor.execute(
                    """SELECT outcome
                       FROM openair_execution_start_attempts
                       WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                         AND operation = 'research_execution_selection'
                         AND idempotency_key_sha256 = %s
                         AND outcome LIKE 'blocked:%%'
                       ORDER BY occurred_at DESC, attempt_id DESC
                       LIMIT 1
                       FOR UPDATE""",
                    (
                        task_id,
                        owner_id,
                        workspace_id,
                        hashlib.sha256(selection_request_id.encode("utf-8")).hexdigest(),
                    ),
                )
                blocked = cursor.fetchone()
                if blocked is None:
                    return None
                parsed = _parse_selection_blocked_outcome(blocked["outcome"])
                if parsed is None:
                    return None
                reason_code, command_hash, instance_id, blocker_facts = parsed
                task, _raw_payload = self._load_locked_task(
                    cursor,
                    task_id=task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                if _blocked_outcome_expired(blocker_facts, task.revision):
                    return None
                return SelectionReplayEvidence(
                    instance_id=instance_id,
                    selection_command_hash=command_hash,
                    replay=BlockedSelectionReplay(
                        reason_code=reason_code,
                        facts=blocker_facts,
                    ),
                )

    def load_registry_snapshot(self, content_hash: str) -> Mapping[str, Any]:
        """Load and content-verify one archived Registry snapshot."""
        self._require_postgres()
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT schema_version, content_hash, payload
                       FROM openair_registry_snapshots
                       WHERE content_hash = %s""",
                    (content_hash,),
                )
                row = cursor.fetchone()
        if row is None:
            raise ResearchOrchestrationPersistenceError("registry_snapshot_not_found")
        envelope = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
        snapshot = snapshot_from_envelope(envelope)
        if snapshot.content_hash != row["content_hash"] or snapshot.schema_version != row["schema_version"]:
            raise ResearchOrchestrationPersistenceError("registry_snapshot_integrity_error")
        return envelope

    def _preserve_live_dispatch_work_units(
        self,
        cursor: psycopg.Cursor[Any],
        *,
        before: ResearchTaskV12,
        after: ResearchTaskV12,
    ) -> ResearchTaskV12:
        """Keep queued retry work units dispatchable after lifecycle reconcile."""
        cursor.execute(
            """SELECT DISTINCT work_unit_id
                 FROM openair_dispatch
                WHERE task_id = %s
                  AND status IN (
                      'pending', 'claimed', 'submission_authorized', 'submission_unknown'
                  )""",
            (before.task_id,),
        )
        protected_ids = {str(row["work_unit_id"]) for row in cursor.fetchall()}
        if not protected_ids:
            return after
        before_payload = before.model_dump(mode="json", by_alias=True)
        after_payload = after.model_dump(mode="json", by_alias=True)
        before_by_id = {
            str(row["workUnitId"]): row for row in before_payload["workUnits"]
        }
        changed = False
        for row in after_payload["workUnits"]:
            work_unit_id = str(row["workUnitId"])
            if work_unit_id not in protected_ids:
                continue
            prior = before_by_id.get(work_unit_id)
            if prior is None or prior.get("status") != "queued":
                continue
            if row.get("status") == "queued":
                continue
            row["status"] = "queued"
            row["terminalReasonCode"] = prior.get("terminalReasonCode")
            row["finishedAt"] = prior.get("finishedAt")
            changed = True
        if not changed:
            return after
        return ResearchTaskV12.model_validate(after_payload)

    def _reconcile_task_lifecycle_with_frozen_snapshot(
        self,
        task: ResearchTaskV12,
        *,
        occurred_at: datetime,
        lifecycle_control_fact: Mapping[str, Any] | None = None,
    ) -> ResearchTaskV12:
        """Reconcile lifecycle against the Task-frozen Registry snapshot."""
        from app.modules.research_execution_decision_service import (
            reconcile_task_lifecycle_from_runtime,
        )

        snapshot = (
            None
            if lifecycle_control_fact is not None
            and lifecycle_control_fact.get("controlAction") == "stop"
            else self.load_registry_snapshot(task.registry_snapshot_ref.content_hash)
        )
        return reconcile_task_lifecycle_from_runtime(
            task,
            occurred_at=occurred_at,
            snapshot=snapshot,
            lifecycle_control_fact=lifecycle_control_fact,
        )

    def prepare_execution_registry_snapshot(
        self,
        *,
        planning_snapshot_ref: Mapping[str, Any],
        required_definition_refs: tuple[tuple[str, int], ...],
    ) -> Mapping[str, Any]:
        """Archive execution capabilities only after a confirmed research decision."""
        from app.modules.registry_snapshot_repository import RegistrySnapshotRepository
        from app.modules.research_orchestration_resolver import build_execution_registry_snapshot

        planning = self.load_registry_snapshot(str(planning_snapshot_ref["contentHash"]))
        if planning.get("schemaVersion") != planning_snapshot_ref["schemaVersion"]:
            raise ResearchOrchestrationPersistenceError("registry_snapshot_integrity_error")
        execution = build_execution_registry_snapshot(
            planning, required_definition_refs=required_definition_refs,
        )
        return RegistrySnapshotRepository(self._connect).put(execution).envelope

    def load_execution_workflow_lineage(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> Mapping[str, Any] | None:
        """Validate and return Workflow lineage for the confirmed ResearchDesign."""
        self._require_postgres()
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT payload, revision
                       FROM openair_tasks
                       WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                         AND status <> 'deleted'""",
                    (task_id, owner_id, workspace_id),
                )
                row = cursor.fetchone()
        if row is None:
            raise ResearchTaskNotFoundError("research_task_not_found")
        payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
        if payload.get("revision") != int(row["revision"]):
            raise PayloadRevisionMismatchError("task_payload_revision_mismatch")
        admission = payload.get("workflowAdmission")
        if admission is None:
            return None
        if not isinstance(admission, Mapping):
            raise ResearchOrchestrationPersistenceError("workflow_admission_invalid")
        try:
            task = ResearchTaskV12.model_validate(_extract_contract_payload(payload))
        except (KeyError, ValidationError) as exc:
            raise ResearchOrchestrationPersistenceError("invalid_research_orchestration_payload") from exc
        return validate_canonical_execution_lineage(
            task=task,
            workflow_admission=admission,
            persisted_skill_pack=payload.get("skillPack"),
        ).as_lineage_facts()

    def load_planning_workflow_admission(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> Mapping[str, Any] | None:
        """Read the immutable Task admission used to constrain design planning."""
        self._require_postgres()
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT payload FROM openair_tasks
                       WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                         AND status <> 'deleted'""",
                    (task_id, owner_id, workspace_id),
                )
                row = cursor.fetchone()
        if row is None:
            raise ResearchTaskNotFoundError("research_task_not_found")
        payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
        admission = payload.get("workflowAdmission")
        if admission is None:
            return None
        if not isinstance(admission, Mapping):
            raise ResearchOrchestrationPersistenceError("workflow_admission_invalid")
        from app.models.workflow_definition import parse_frozen_workflow_definition

        workflow_id = str(admission.get("workflowId") or "").strip()
        skill_pack = str(admission.get("skillPack") or "").strip()
        workflow_version = admission.get("workflowVersion")
        workflow_content_hash = str(admission.get("workflowContentHash") or "").strip().lower()
        snapshot_payload = admission.get("workflowDefinitionSnapshot")
        try:
            frozen = parse_frozen_workflow_definition(snapshot_payload)
        except (ValidationError, TypeError, ValueError) as exc:
            raise ResearchOrchestrationPersistenceError("workflow_admission_lineage_mismatch") from exc

        task_registry_ref = payload.get("registrySnapshotRef")
        frozen_registry_ref = frozen.registry_snapshot_ref.model_dump(mode="json", by_alias=True)
        if (
            not workflow_id
            or not skill_pack
            or isinstance(workflow_version, bool)
            or not isinstance(workflow_version, int)
            or workflow_version < 1
            or payload.get("skillPack") != skill_pack
            or workflow_id != frozen.workflow_id
            or workflow_version != frozen.workflow_version
            or workflow_content_hash != frozen.content_hash
            or skill_pack != frozen.definition.skill_pack
            or task_registry_ref != frozen_registry_ref
        ):
            raise ResearchOrchestrationPersistenceError("workflow_admission_lineage_mismatch")
        return dict(admission)

    def attach_pending_workflow_admission(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        workflow_admission: Mapping[str, Any],
    ) -> ResearchTaskV12:
        """Atomically bind an explicitly selected workflow to an untouched reserved task.

        Research Start reserves the task identity before planning begins.
        The explicit selection, not the reservation, owns the workflow identity.
        This method is intentionally limited to a pristine draft so a later
        planning retry can never rewrite a task that has begun planning or work.
        """
        self._require_write_enabled()
        self._require_postgres()
        workflow_id = str(workflow_admission.get("workflowId") or "").strip()
        skill_pack = str(workflow_admission.get("skillPack") or "").strip()
        if not workflow_id or not skill_pack:
            raise ResearchOrchestrationPersistenceError("workflow_admission_invalid")
        normalized_admission = dict(workflow_admission)
        normalized_admission["workflowId"] = workflow_id
        normalized_admission["skillPack"] = skill_pack

        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                task, raw_payload = self._load_locked_task(
                    cursor,
                    task_id=task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                existing = raw_payload.get("workflowAdmission")
                if existing is not None:
                    if not isinstance(existing, Mapping):
                        raise ResearchOrchestrationPersistenceError("workflow_admission_invalid")
                    existing_identity = (
                        str(existing.get("workflowId") or "").strip(),
                        str(existing.get("skillPack") or "").strip(),
                    )
                    admission_identity = (
                        workflow_id,
                        skill_pack,
                    )
                    if existing_identity != admission_identity:
                        raise ResearchOrchestrationPersistenceError(
                            "workflow_admission_lineage_conflict"
                        )
                    if raw_payload.get("skillPack") != skill_pack:
                        raise ResearchOrchestrationPersistenceError(
                            "workflow_admission_lineage_mismatch"
                        )
                    return task
                if (
                    task.status != TaskStatus.DRAFT
                    or task.planning_attempts
                    or task.research_design_versions
                    or task.execution_decisions
                    or task.work_units
                    or task.capability_calls
                ):
                    raise ResearchOrchestrationPersistenceError(
                        "workflow_admission_after_planning_forbidden"
                    )
                next_revision = task.revision + 1
                candidate = project_task_progress(task, revision=next_revision)
                self._write_locked_task(
                    cursor,
                    raw_payload,
                    candidate,
                    payload_patch={
                        "skillPack": skill_pack,
                        "workflowAdmission": normalized_admission,
                    },
                )
        return candidate


    def load_catalog_execution_binding(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> Mapping[str, str] | None:
        """Return the Resource Plaza execution pin that reserved this Task.

        The Research Start session remains the durable authority.  This read is
        owner/workspace scoped and fails closed if duplicate or partial catalog
        bindings somehow exist for the same reserved Task identity.
        """
        from app.modules.catalog_execution_binding import binding_from_session

        self._require_postgres()
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT catalog_intent_id, selected_resource_id,
                              selected_resource_version, selected_capability_id
                       FROM research_start_sessions
                       WHERE reserved_task_id = %s AND owner_id = %s AND workspace_id = %s
                         AND catalog_intent_id IS NOT NULL
                       ORDER BY created_at, session_id
                       LIMIT 2""",
                    (task_id, owner_id, workspace_id),
                )
                rows = list(cursor.fetchall())
        if not rows:
            return None
        if len(rows) != 1:
            raise ResearchOrchestrationPersistenceError("catalog_execution_binding_ambiguous")
        binding = binding_from_session(rows[0])
        if binding is None:
            raise ResearchOrchestrationPersistenceError("catalog_execution_binding_missing")
        return binding.to_public()

    def record_needs_input(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int,
        substage_instance_id: str,
        reason_code: str,
        selection_request_id: str,
        selection_command_hash: str,
        blocker_facts: Mapping[str, Any] | None = None,
    ) -> ResearchTaskV12 | "SelectionCommitResult" | "BlockedSelectionReplay":
        """Atomically record or replay one deterministic preflight blocker."""
        from app.modules.research_execution_decision_service import (
            BlockedSelectionReplay,
            SelectionCommitResult,
        )

        self._require_write_enabled()
        self._require_postgres()
        self._require_expected_revision(expected_task_revision)
        if not reason_code.strip():
            raise ValueError("reason_code must be non-empty")
        if not needs_user_input(reason_code):
            raise ResearchOrchestrationPersistenceError(
                "system_blocker_cannot_enter_needs_input"
            )
        if not selection_request_id.strip():
            raise ValueError("selection_request_id must be non-empty")
        if _normalized_sha256(selection_command_hash) is None:
            raise ValueError("selection_command_hash must be a sha256 digest")
        request_digest = hashlib.sha256(selection_request_id.encode("utf-8")).hexdigest()
        attempt_digest = hashlib.sha256(
            f"{task_id}\x1f{owner_id}\x1f{workspace_id}\x1f{selection_request_id}".encode("utf-8")
        ).hexdigest()
        attempt_id = f"selection_block_{attempt_digest}"

        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                task, raw_payload = self._load_locked_task(
                    cursor,
                    task_id=task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )

                # The final Task lock is the idempotency authority boundary.  Re-check
                # committed/blocked truth before revision CAS so a concurrent loser
                # replays the winner instead of observing task_revision_conflict.
                cursor.execute(
                    """SELECT command_hash, execution_decision_id
                       FROM openair_execution_selection_request
                       WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                         AND selection_request_id = %s
                       FOR UPDATE""",
                    (task_id, owner_id, workspace_id, selection_request_id),
                )
                committed = cursor.fetchone()
                if committed is not None:
                    if committed["command_hash"] != selection_command_hash:
                        raise SelectionRequestPayloadConflictError(
                            "selection_request_command_conflict"
                        )
                    decision = next(
                        (
                            row
                            for row in task.execution_decisions
                            if row.execution_decision_id == committed["execution_decision_id"]
                        ),
                        None,
                    )
                    if decision is None:
                        raise ResearchOrchestrationPersistenceError(
                            "selection_request_decision_missing"
                        )
                    return SelectionCommitResult(task=task, decision=decision, replayed=True)

                cursor.execute(
                    """SELECT outcome
                       FROM openair_execution_start_attempts
                       WHERE attempt_id = %s
                         AND task_id = %s AND owner_id = %s AND workspace_id = %s
                         AND operation = 'research_execution_selection'
                         AND idempotency_key_sha256 = %s
                       FOR UPDATE""",
                    (attempt_id, task_id, owner_id, workspace_id, request_digest),
                )
                existing_blocked = cursor.fetchone()
                if existing_blocked is not None:
                    parsed = _parse_selection_blocked_outcome(existing_blocked["outcome"])
                    if parsed is None:
                        raise ResearchOrchestrationPersistenceError(
                            "selection_blocked_evidence_invalid"
                        )
                    stored_reason, stored_hash, stored_instance_id, stored_facts = parsed
                    if _blocked_outcome_expired(stored_facts, task.revision):
                        # Stale verdict: treat as no prior block and let the caller
                        # re-evaluate (which may write a fresh block at this revision).
                        return None
                    if stored_hash != selection_command_hash:
                        raise SelectionRequestPayloadConflictError(
                            "selection_request_command_conflict"
                        )
                    if stored_instance_id != substage_instance_id:
                        raise ResearchOrchestrationPersistenceError(
                            "selection_blocked_instance_identity_mismatch"
                        )
                    return BlockedSelectionReplay(
                        reason_code=stored_reason,
                        facts=stored_facts,
                    )

                if task.revision != expected_task_revision:
                    raise TaskRevisionConflictError("task_revision_conflict")
                payload = task.model_dump(mode="json", by_alias=True)
                runtime = next(
                    (
                        row
                        for row in payload["substageRuntimeStates"]
                        if row["substageInstanceId"] == substage_instance_id
                    ),
                    None,
                )
                if runtime is None or runtime["status"] != "ready":
                    raise TaskRevisionConflictError("instance_not_ready")
                occurred_at = datetime.now(timezone.utc)
                runtime.update(
                    {
                        "status": "needs_input",
                        "activeExecutionDecisionId": None,
                        "workUnitIds": [],
                        "statusReasonCode": reason_code,
                        "updatedAt": occurred_at.isoformat(),
                    }
                )
                updated = self._reconcile_task_lifecycle_with_frozen_snapshot(
                    ResearchTaskV12.model_validate(payload),
                    occurred_at=occurred_at,
                )
                self._write_locked_task(cursor, raw_payload, updated)
                durable_facts = {
                    "taskRevision": expected_task_revision + 1,
                    **dict(blocker_facts or {}),
                }
                cursor.execute(
                    """INSERT INTO openair_execution_start_attempts (
                           attempt_id, owner_id, workspace_id, task_id, operation,
                           idempotency_key_sha256, outcome, canonical_run_id
                       ) VALUES (%s, %s, %s, %s, 'research_execution_selection', %s, %s, NULL)
                       ON CONFLICT (attempt_id) DO NOTHING""",
                    (
                        attempt_id,
                        owner_id,
                        workspace_id,
                        task_id,
                        request_digest,
                        _selection_blocked_outcome(
                            reason_code=reason_code,
                            selection_command_hash=selection_command_hash,
                            substage_instance_id=substage_instance_id,
                            facts=durable_facts,
                        ),
                    ),
                )
        result = updated.model_dump(mode="json", by_alias=True)
        result["revision"] = expected_task_revision + 1
        return ResearchTaskV12.model_validate(result)

    def commit_prepared_selection(
        self,
        prepared: "PreparedSelection",
        freeze_selection: Callable[[ResearchTaskV12, str], "ExecutionMaterialization"],
    ) -> "SelectionCommitResult":
        """Commit replay/CAS/fairness/Decision/WorkUnit/outbox as one UoW.

        Lock order is scoped Task, request replay row, fairness cursor, then new
        Dispatch inserts.  No provider/runtime/network operation is permitted.
        """
        from app.modules.research_execution_decision_service import SelectionCommitResult

        self._require_write_enabled()
        self._require_postgres()
        self._require_expected_revision(prepared.expected_task_revision)
        try:
            with self._uow.transaction() as connection:
                with connection.cursor() as cursor:
                    task, raw_payload = self._load_locked_task(
                        cursor,
                        task_id=prepared.task.task_id,
                        owner_id=prepared.owner_id,
                        workspace_id=prepared.workspace_id,
                    )
                    cursor.execute(
                        """SELECT command_hash, payload_hash, execution_decision_id
                           FROM openair_execution_selection_request
                           WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                             AND selection_request_id = %s
                           FOR UPDATE""",
                        (
                            prepared.task.task_id,
                            prepared.owner_id,
                            prepared.workspace_id,
                            prepared.selection_request_id,
                        ),
                    )
                    replay = cursor.fetchone()
                    if replay is not None:
                        if replay["command_hash"] != prepared.selection_command_hash:
                            raise SelectionRequestPayloadConflictError(
                                "selection_request_command_conflict"
                            )
                        decision = next(
                            (
                                row
                                for row in task.execution_decisions
                                if row.execution_decision_id == replay["execution_decision_id"]
                            ),
                            None,
                        )
                        if decision is None:
                            raise ResearchOrchestrationPersistenceError(
                                "selection_request_decision_missing"
                            )
                        return SelectionCommitResult(task=task, decision=decision, replayed=True)
                    if task.revision != prepared.expected_task_revision:
                        raise TaskRevisionConflictError("task_revision_conflict")
                    if (
                        task.registry_snapshot_ref != prepared.task.registry_snapshot_ref
                        or task.active_research_design_ref != prepared.task.active_research_design_ref
                    ):
                        raise TaskRevisionConflictError("selection_authority_changed")
                    task, _execution_policy = self._apply_pending_execution_policy_at_boundary(
                        cursor,
                        task=task,
                        owner_id=prepared.owner_id,
                        workspace_id=prepared.workspace_id,
                        changed_by=prepared.owner_id,
                    )

                    cursor.execute(
                        """SELECT command_payload
                             FROM openair_model_jobs
                            WHERE resource_kind = 'task'
                              AND resource_id = %s
                              AND owner_id = %s
                              AND workspace_id = %s
                              AND job_id = %s
                              AND job_kind = 'execution_selection'
                            FOR UPDATE""",
                        (
                            task.task_id,
                            prepared.owner_id,
                            prepared.workspace_id,
                            prepared.selection_request_id,
                        ),
                    )
                    start_job = cursor.fetchone()
                    command_payload = start_job.get("command_payload") if start_job else None
                    if isinstance(command_payload, str):
                        try:
                            command_payload = json.loads(command_payload)
                        except json.JSONDecodeError:
                            command_payload = None
                    request_kind = (
                        str(command_payload.get("requestKind") or "").strip()
                        if isinstance(command_payload, Mapping)
                        else ""
                    )
                    if request_kind == "continue_research":
                        from app.modules.workbench_user_actions import project_continue_research_action

                        action = project_continue_research_action({
                            **raw_payload, **task.model_dump(mode="json", by_alias=True),
                        })
                        if action["executionStopped"] or action["allowed"] is not True:
                            raise TaskRevisionConflictError("continue_research_not_allowed")
                    if request_kind in {"explicit_start", "confirmed_design"}:
                        # The shared Start Authority is evaluated again at the
                        # final execution-selection write while this Task row is
                        # locked. The initiating queued/leased job is ignored,
                        # but any competing current-revision Start remains visible.
                        from app.modules.research_execution_start_status import (
                            ResearchExecutionInitialStartDenied,
                            evaluate_current_execution_start_status,
                        )

                        start_status = evaluate_current_execution_start_status(
                            cursor,
                            task_id=task.task_id,
                            owner_id=prepared.owner_id,
                            workspace_id=prepared.workspace_id,
                            lock_task=True,
                            initiating_selection_request_id=prepared.selection_request_id,
                        )
                        if not start_status.startAllowed:
                            raise ResearchExecutionInitialStartDenied(start_status)

                    winner = prepared.final_tie_candidate_ids[0]
                    cursor_slot: int | None = None
                    fairness = prepared.fairness
                    if fairness is not None:
                        cursor.execute(
                            """INSERT INTO openair_equivalent_candidate_fairness (
                                   policy_version, owner_id, workspace_id,
                                   registry_snapshot_content_hash, substage_definition_ref,
                                   equivalence_set_hash, next_slot
                               ) VALUES (%s, %s, %s, %s, %s, %s, 0)
                               ON CONFLICT DO NOTHING""",
                            (
                                fairness.policy_version,
                                fairness.owner_id,
                                fairness.workspace_id,
                                fairness.registry_snapshot_content_hash,
                                fairness.substage_definition_ref,
                                fairness.equivalence_set_hash,
                            ),
                        )
                        cursor.execute(
                            """SELECT next_slot
                               FROM openair_equivalent_candidate_fairness
                               WHERE policy_version = %s AND owner_id = %s AND workspace_id = %s
                                 AND registry_snapshot_content_hash = %s
                                 AND substage_definition_ref = %s AND equivalence_set_hash = %s
                               FOR UPDATE""",
                            (
                                fairness.policy_version,
                                fairness.owner_id,
                                fairness.workspace_id,
                                fairness.registry_snapshot_content_hash,
                                fairness.substage_definition_ref,
                                fairness.equivalence_set_hash,
                            ),
                        )
                        row = cursor.fetchone()
                        if row is None:
                            raise ResearchOrchestrationPersistenceError("fairness_cursor_missing")
                        cursor_slot = int(row["next_slot"])
                        winner = fairness.candidate_identities[
                            cursor_slot % len(fairness.candidate_identities)
                        ]

                    materialized = freeze_selection(task, winner)
                    next_revision = prepared.expected_task_revision + 1
                    candidate = project_task_progress(materialized.task, revision=next_revision)
                    candidate_payload = ControllerCommandRepository.apply_canonical_at_materialization(
                        cursor,
                        task_payload=candidate.model_dump(mode="json", by_alias=True),
                        owner_id=prepared.owner_id,
                        workspace_id=prepared.workspace_id,
                    )
                    candidate = ResearchTaskV12.model_validate(candidate_payload)
                    dispatches = list(materialized.dispatches)
                    self._validate_pending_dispatches(candidate, dispatches)
                    self._write_locked_task(cursor, raw_payload, candidate)
                    for record in dispatches:
                        record.assert_pending_insert()
                        cursor.execute(
                            """INSERT INTO openair_dispatch (
                                   dispatch_id, task_id, execution_decision_id, work_unit_id,
                                   idempotency_key, execution_namespace, status, attempt_count, lease_owner,
                                   lease_expires_at, authorization_task_revision, remote_run_id,
                                   next_attempt_at, last_error_code, created_at, updated_at
                               ) VALUES (
                                   %(dispatch_id)s, %(task_id)s, %(execution_decision_id)s,
                                   %(work_unit_id)s, %(idempotency_key)s, %(execution_namespace)s, %(status)s,
                                   %(attempt_count)s, %(lease_owner)s, %(lease_expires_at)s,
                                   %(authorization_task_revision)s, %(remote_run_id)s,
                                   %(next_attempt_at)s, %(last_error_code)s,
                                   %(created_at)s, %(updated_at)s
                               )""",
                            record.model_dump(mode="python"),
                        )
                    cursor.execute(
                        """INSERT INTO openair_execution_selection_request (
                               task_id, owner_id, workspace_id, selection_request_id,
                               command_hash, payload_hash, execution_decision_id
                           ) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (
                            candidate.task_id,
                            prepared.owner_id,
                            prepared.workspace_id,
                            prepared.selection_request_id,
                            prepared.selection_command_hash,
                            prepared.selection_payload_hash,
                            materialized.decision.execution_decision_id,
                        ),
                    )
                    if fairness is not None and cursor_slot is not None:
                        cursor.execute(
                            """UPDATE openair_equivalent_candidate_fairness
                               SET next_slot = %s, updated_at = clock_timestamp()
                               WHERE policy_version = %s AND owner_id = %s AND workspace_id = %s
                                 AND registry_snapshot_content_hash = %s
                                 AND substage_definition_ref = %s AND equivalence_set_hash = %s""",
                            (
                                cursor_slot + 1,
                                fairness.policy_version,
                                fairness.owner_id,
                                fairness.workspace_id,
                                fairness.registry_snapshot_content_hash,
                                fairness.substage_definition_ref,
                                fairness.equivalence_set_hash,
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise ResearchOrchestrationPersistenceError(
                                "fairness_cursor_advance_failed"
                            )
            return SelectionCommitResult(
                task=candidate,
                decision=materialized.decision,
                replayed=False,
            )
        except errors.UniqueViolation as exc:
            raise DispatchIdentityConflictError("selection_or_dispatch_identity_conflict") from exc

    def save(
        self,
        task: ResearchTaskV12,
        *,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int,
        pending_dispatches: list[DispatchRecord] | None = None,
        automatic_dispatch: bool = False,
        lifecycle_fact: ResearchDesignLifecycleFact | None = None,
    ) -> ResearchTaskV12:
        self._require_write_enabled()
        self._require_postgres()
        dispatches = list(pending_dispatches or [])
        if automatic_dispatch and not dispatches:
            raise ValueError("automatic dispatch requires at least one pending DispatchRecord")
        if task.revision != expected_task_revision:
            raise TaskRevisionConflictError("task_revision_conflict")

        candidate = task
        self._validate_pending_dispatches(candidate, dispatches)

        try:
            with self._uow.transaction() as connection:
                with connection.cursor() as cursor:
                    persisted_task, raw_payload = self._load_locked_task(
                        cursor,
                        task_id=task.task_id,
                        owner_id=owner_id,
                        workspace_id=workspace_id,
                    )
                    if persisted_task.revision != expected_task_revision:
                        raise TaskRevisionConflictError("task_revision_conflict")
                    if lifecycle_fact is not None:
                        transition = reconcile_task_lifecycle(
                            persisted_task.model_dump(mode="json", by_alias=True),
                            fact=lifecycle_fact,
                        )
                        if transition is None:
                            raise TaskRevisionConflictError("task_lifecycle_transition_rejected")
                        candidate_payload = task.model_dump(mode="json", by_alias=True)
                        candidate_payload["status"] = transition.task_status
                        candidate = ResearchTaskV12.model_validate(candidate_payload)

                    next_revision = expected_task_revision + 1
                    candidate = project_task_progress(candidate, revision=next_revision)
                    candidate_payload = ControllerCommandRepository.apply_canonical_at_materialization(
                        cursor,
                        task_payload=candidate.model_dump(mode="json", by_alias=True),
                        owner_id=owner_id,
                        workspace_id=workspace_id,
                    )
                    candidate = ResearchTaskV12.model_validate(candidate_payload)
                    self._validate_pending_dispatches(candidate, dispatches)
                    self._write_locked_task(cursor, raw_payload, candidate)
                    for record in dispatches:
                        cursor.execute(
                            """INSERT INTO openair_dispatch (
                                   dispatch_id, task_id, execution_decision_id, work_unit_id,
                                   idempotency_key, execution_namespace, status, attempt_count, lease_owner,
                                   lease_expires_at, authorization_task_revision, remote_run_id,
                                   next_attempt_at, last_error_code, created_at, updated_at
                               ) VALUES (
                                   %(dispatch_id)s, %(task_id)s, %(execution_decision_id)s,
                                   %(work_unit_id)s, %(idempotency_key)s, %(execution_namespace)s, %(status)s,
                                   %(attempt_count)s, %(lease_owner)s, %(lease_expires_at)s,
                                   %(authorization_task_revision)s, %(remote_run_id)s,
                                   %(next_attempt_at)s, %(last_error_code)s,
                                   %(created_at)s, %(updated_at)s
                               )""",
                            record.model_dump(mode="python"),
                        )
        except errors.UniqueViolation as exc:
            raise DispatchIdentityConflictError("dispatch_identity_conflict") from exc
        return candidate

    def list_pending_method_continuations(self, *, min_pending_age_seconds: int = 0) -> list[dict[str, Any]]:
        """Recover a committed known failure whose callback did not finish.

        The Task is the durable trigger, scoped to this execution namespace.
        No failed dispatch is reclaimed and no external action is retried.
        CAS with the observed Task revision arbitrates stop/design/concurrency.
        """
        self._require_postgres()
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT t.task_id, t.owner_id, t.workspace_id, t.revision,
                              decision->>'executionDecisionId' AS execution_decision_id
                         FROM openair_tasks t
                         CROSS JOIN LATERAL jsonb_array_elements(
                             COALESCE(t.payload->'executionDecisions', '[]'::jsonb)
                         ) AS decision
                        WHERE t.status = 'running'
                          AND COALESCE(t.payload->'stopReceipt'->>'active', 'false') <> 'true'
                          AND decision->>'strategy' = 'execution_method'
                          AND decision->>'status' = 'dispatched'
                          AND decision->>'researchDesignVersion' = t.payload->'activeResearchDesignRef'->>'version'
                          AND t.updated_at <= clock_timestamp() - make_interval(secs => %(min_age)s)
                          AND EXISTS (
                              SELECT 1 FROM openair_dispatch d
                               WHERE d.task_id = t.task_id
                                 AND d.execution_decision_id = decision->>'executionDecisionId'
                                 AND d.execution_namespace = %(namespace)s
                          )
                          AND NOT EXISTS (
                              SELECT 1 FROM openair_dispatch d
                               WHERE d.task_id = t.task_id
                                 AND d.execution_decision_id = decision->>'executionDecisionId'
                                 AND d.status IN ('pending', 'claimed', 'submission_authorized', 'submission_unknown')
                          )
                          AND NOT EXISTS (
                              SELECT 1 FROM jsonb_array_elements(COALESCE(t.payload->'workUnits', '[]'::jsonb)) w
                               WHERE w->>'executionDecisionId' = decision->>'executionDecisionId'
                                 AND w->>'status' NOT IN ('completed', 'failed', 'blocked')
                          )
                          AND (
                              jsonb_array_length(COALESCE(decision->'assignmentPreflightOutcomes', '[]'::jsonb)) > 0
                              OR EXISTS (
                                  SELECT 1 FROM jsonb_array_elements(COALESCE(t.payload->'workUnits', '[]'::jsonb)) w
                                   WHERE w->>'executionDecisionId' = decision->>'executionDecisionId'
                                     AND w->>'status' IN ('completed', 'failed', 'blocked')
                              )
                          )
                        ORDER BY t.updated_at, t.task_id
                        LIMIT 16""",
                    {"namespace": self._settings.execution_namespace, "min_age": max(0, min_pending_age_seconds)},
                )
                return [dict(row) for row in cursor.fetchall()]

    def claim_dispatches(
        self,
        *,
        lease_owner: str,
        lease_seconds: int,
        batch_size: int = 1,
        dispatch_id: str | None = None,
        task_id: str | None = None,
        owner_id: str | None = None,
        workspace_id: str | None = None,
        min_pending_age_seconds: int = 0,
        execution_namespace: str | None = None,
        max_inflight_per_task: int | None = None,
    ) -> list[DispatchFence]:
        """Recover un-authorized expired claims, then atomically fence due work.

        ``min_pending_age_seconds`` is enforced in the same transaction as the
        claim.  It is used by API fallback executors so a worker/fallback race
        still has one database-owned execution winner.
        """
        self._require_write_enabled()
        self._require_postgres()
        if (
            not lease_owner.strip()
            or lease_seconds < 1
            or not 1 <= batch_size <= 100
            or type(min_pending_age_seconds) is not int
            or min_pending_age_seconds < 0
            or (
                max_inflight_per_task is not None
                and (type(max_inflight_per_task) is not int or max_inflight_per_task < 1)
            )
        ):
            raise ValueError("invalid dispatch claim parameters")
        if (owner_id is None) != (workspace_id is None):
            raise ValueError("owner_id and workspace_id must be provided together")
        namespace = (execution_namespace or getattr(self._settings, "execution_namespace", "")).strip()
        if not namespace:
            raise ValueError("execution_namespace is required for dispatch claims")
        scope = {
            "dispatch_id": dispatch_id,
            "task_id": task_id,
            "owner_id": owner_id,
            "workspace_id": workspace_id,
            "lease_owner": lease_owner,
            "lease_seconds": lease_seconds,
            "batch_size": batch_size,
            "min_pending_age_seconds": min_pending_age_seconds,
            'execution_namespace': namespace,
            'max_inflight_per_task': max_inflight_per_task,
        }
        scope_sql = """
            AND (%(dispatch_id)s::text IS NULL OR d.dispatch_id = %(dispatch_id)s::text)
            AND (%(task_id)s::text IS NULL OR d.task_id = %(task_id)s::text)
            AND (%(owner_id)s::text IS NULL OR t.owner_id = %(owner_id)s::text)
            AND (%(workspace_id)s::text IS NULL OR t.workspace_id = %(workspace_id)s::text)
            AND d.execution_namespace = %(execution_namespace)s::text
        """
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('openair.execution_namespace', %s, true)",
                    (namespace,),
                )
                cursor.execute(
                    f"""UPDATE openair_dispatch AS d
                           SET status = 'pending', lease_owner = NULL, lease_expires_at = NULL,
                               updated_at = clock_timestamp()
                          FROM openair_tasks AS t
                         WHERE t.task_id = d.task_id
                           AND t.status <> 'deleted'
                           AND d.status = 'claimed'
                           AND d.lease_expires_at <= clock_timestamp()
                           {scope_sql}""",
                    scope,
                )
                cursor.execute(
                    f"""WITH candidates AS (
                           SELECT d.dispatch_id
                             FROM openair_dispatch AS d
                             JOIN openair_tasks AS t ON t.task_id = d.task_id
                             WHERE d.status = 'pending'
                               AND t.status <> 'deleted'
                               AND (t.payload->'stopReceipt'->>'active') IS DISTINCT FROM 'true'
                               AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= clock_timestamp())
                               AND (
                                   %(max_inflight_per_task)s::integer IS NULL
                                   OR (
                                       SELECT count(*) FROM openair_dispatch AS active
                                        WHERE active.task_id = d.task_id
                                          AND active.execution_namespace = d.execution_namespace
                                          AND active.status IN ('claimed', 'submission_authorized')
                                          AND active.lease_expires_at > clock_timestamp()
                                   ) < %(max_inflight_per_task)s::integer
                               )
                               AND d.created_at <= clock_timestamp()
                                   - make_interval(secs => %(min_pending_age_seconds)s)
                              {scope_sql}
                            ORDER BY d.next_attempt_at ASC NULLS FIRST, d.created_at, d.dispatch_id
                            FOR UPDATE OF d, t SKIP LOCKED
                            LIMIT %(batch_size)s
                       )
                       UPDATE openair_dispatch AS d
                          SET status = 'claimed',
                              attempt_count = d.attempt_count + 1,
                              lease_owner = %(lease_owner)s,
                              lease_expires_at = clock_timestamp() + make_interval(secs => %(lease_seconds)s),
                              updated_at = clock_timestamp()
                         FROM candidates
                        WHERE d.dispatch_id = candidates.dispatch_id
                    RETURNING d.dispatch_id, d.task_id, d.execution_decision_id, d.work_unit_id,
                              d.idempotency_key, d.lease_owner, d.attempt_count""",
                    scope,
                )
                rows = cursor.fetchall()
        return [DispatchFence(**dict(row)) for row in rows]

    def renew_dispatch(
        self,
        fence: DispatchFence,
        *,
        lease_seconds: int,
    ) -> bool:
        self._require_write_enabled()
        self._require_postgres()
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """UPDATE openair_dispatch
                          SET lease_expires_at = clock_timestamp() + make_interval(secs => %(lease_seconds)s),
                              updated_at = clock_timestamp()
                        WHERE dispatch_id = %(dispatch_id)s
                          AND task_id = %(task_id)s
                          AND execution_decision_id = %(execution_decision_id)s
                          AND work_unit_id = %(work_unit_id)s
                          AND idempotency_key = %(idempotency_key)s
                          AND status = 'claimed'
                          AND lease_owner = %(lease_owner)s
                          AND attempt_count = %(attempt_count)s
                          AND lease_expires_at > clock_timestamp()""",
                    {**fence.__dict__, "lease_seconds": lease_seconds},
                )
                return cursor.rowcount == 1

    def claim_authorized_recovery(
        self,
        *,
        lease_owner: str,
        lease_seconds: int,
        batch_size: int = 1,
        dispatch_id: str | None = None,
        task_id: str | None = None,
        owner_id: str | None = None,
        workspace_id: str | None = None,
        execution_namespace: str | None = None,
        max_inflight_per_task: int | None = None,
    ) -> list[AuthorizedDispatch]:
        """Take over expired authorization leases without changing submission identity."""
        self._require_write_enabled()
        self._require_postgres()
        if (
            not lease_owner.strip()
            or lease_seconds < 1
            or not 1 <= batch_size <= 100
            or (
                max_inflight_per_task is not None
                and (type(max_inflight_per_task) is not int or max_inflight_per_task < 1)
            )
        ):
            raise ValueError("invalid authorized recovery claim parameters")
        if (owner_id is None) != (workspace_id is None):
            raise ValueError("owner_id and workspace_id must be provided together")
        namespace = (execution_namespace or getattr(self._settings, "execution_namespace", "")).strip()
        if not namespace:
            raise ValueError("execution_namespace is required for authorized recovery claims")
        scope = {
            "dispatch_id": dispatch_id,
            "task_id": task_id,
            "owner_id": owner_id,
            "workspace_id": workspace_id,
            "lease_owner": lease_owner,
            "lease_seconds": lease_seconds,
            "batch_size": batch_size,
            'execution_namespace': namespace,
            'max_inflight_per_task': max_inflight_per_task,
            "providerless_recovery_quiet_seconds": max(
                120,
                int(lease_seconds) * 4,
            ),
        }
        recovered: list[AuthorizedDispatch] = []
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('openair.execution_namespace', %s, true)",
                    (namespace,),
                )
                cursor.execute(
                    """UPDATE openair_provider_http_attempts AS a
                          SET finished_at = clock_timestamp(),
                              transport_outcome = CASE
                                  WHEN COALESCE(a.io_started, FALSE) IS TRUE
                                  THEN 'transport_uncertain'
                                  ELSE 'transport_not_submitted'
                              END,
                              execution_state = CASE
                                  WHEN COALESCE(a.io_started, FALSE) IS TRUE
                                  THEN 'unknown'
                                  ELSE 'failed'
                              END,
                              execution_reason = CASE
                                  WHEN COALESCE(a.io_started, FALSE) IS TRUE
                                  THEN 'transport_uncertain'
                                  ELSE 'transport_not_submitted'
                              END,
                              execution_stage = CASE
                                  WHEN COALESCE(a.io_started, FALSE) IS TRUE
                                  THEN 'response_observation'
                                  ELSE 'pre_io_recovery'
                              END,
                              submission_proven = CASE
                                  WHEN COALESCE(a.io_started, FALSE) IS TRUE
                                  THEN NULL
                                  ELSE FALSE
                              END,
                              io_started = COALESCE(a.io_started, FALSE)
                         FROM openair_external_execution AS x
                         JOIN openair_dispatch AS d ON d.dispatch_id = x.dispatch_id
                         JOIN openair_tasks AS t ON t.task_id = d.task_id
                        WHERE a.capability_call_id = x.capability_call_id
                          AND a.attempt_number = x.attempt_number
                          AND a.evidence_kind = 'provider_submission'
                          AND COALESCE(a.transport_outcome, 'in_flight') = 'in_flight'
                          AND a.finished_at IS NULL
                          AND (
                              a.execution_state IS NULL
                              OR a.execution_state IN ('running', 'unknown')
                          )
                          AND d.status = 'submission_authorized'
                          AND d.submission_mode = 'single_attempt_no_replay'
                          AND d.external_action_started_at IS NOT NULL
                          AND d.lease_expires_at <= clock_timestamp()
                          AND d.execution_namespace = %(execution_namespace)s::text
                          AND (%(dispatch_id)s::text IS NULL OR d.dispatch_id = %(dispatch_id)s::text)
                          AND (%(task_id)s::text IS NULL OR d.task_id = %(task_id)s::text)
                          AND (%(owner_id)s::text IS NULL OR t.owner_id = %(owner_id)s::text)
                          AND (%(workspace_id)s::text IS NULL OR t.workspace_id = %(workspace_id)s::text)""",
                    scope,
                )
                cursor.execute(
                    """SELECT d.dispatch_id, d.task_id, d.execution_decision_id,
                              d.work_unit_id, d.idempotency_key,
                              d.status AS observed_status,
                              d.authorization_task_revision AS observed_authorization_task_revision,
                              d.submission_mode, d.external_action_started_at,
                              d.result_storage_type, d.result_storage_scope, d.result_storage_ref,
                              t.owner_id, t.workspace_id,
                              d.lease_owner AS observed_lease_owner,
                              d.attempt_count AS observed_attempt_count
                         FROM openair_dispatch AS d
                         JOIN openair_tasks AS t ON t.task_id = d.task_id
                        WHERE (
                              (d.status = 'submission_authorized'
                               AND d.lease_expires_at <= clock_timestamp())
                           OR (d.status = 'submission_unknown'
                               AND d.submission_mode = 'single_attempt_no_replay'
                               AND d.external_action_started_at IS NOT NULL
                               AND d.result_storage_ref IS NOT NULL
                               AND d.last_error_code IS DISTINCT FROM
                                   'invalid_research_orchestration_payload'
                               AND (d.lease_expires_at IS NULL
                                    OR d.lease_expires_at <= clock_timestamp()))
                          )
                          AND d.execution_namespace = %(execution_namespace)s::text
                          AND d.authorization_task_revision IS NOT NULL
                          AND (
                              %(max_inflight_per_task)s::integer IS NULL
                              OR (
                                  SELECT count(*) FROM openair_dispatch AS active
                                   WHERE active.task_id = d.task_id
                                     AND active.execution_namespace = d.execution_namespace
                                     AND active.status IN ('claimed', 'submission_authorized')
                                     AND active.lease_expires_at > clock_timestamp()
                              ) < %(max_inflight_per_task)s::integer
                          )
                          AND (
                              (t.payload->'stopReceipt'->>'active') IS DISTINCT FROM 'true'
                              OR d.external_action_started_at IS NOT NULL
                          )
                          AND NOT (
                              d.external_action_started_at IS NOT NULL
                              AND d.submission_mode = 'single_attempt_no_replay'
                              AND EXISTS (
                                  SELECT 1
                                    FROM openair_external_execution AS x
                                    JOIN openair_provider_http_attempts AS a
                                      ON a.capability_call_id = x.capability_call_id
                                     AND a.attempt_number = x.attempt_number
                                     AND a.evidence_kind = 'provider_submission'
                                   WHERE x.dispatch_id = d.dispatch_id
                                     AND COALESCE(a.transport_outcome, 'in_flight') = 'in_flight'
                              )
                          )
                          AND NOT (
                              d.status = 'submission_authorized'
                              AND d.submission_mode = 'single_attempt_no_replay'
                              AND d.external_action_started_at IS NOT NULL
                              AND d.result_storage_ref IS NULL
                              AND NOT EXISTS (
                                  SELECT 1
                                    FROM openair_external_execution AS x
                                    JOIN openair_provider_http_attempts AS a
                                      ON a.capability_call_id = x.capability_call_id
                                     AND a.attempt_number = x.attempt_number
                                     AND a.evidence_kind = 'provider_submission'
                                   WHERE x.dispatch_id = d.dispatch_id
                                     AND COALESCE(a.transport_outcome, 'in_flight') = 'in_flight'
                              )
                              AND d.lease_expires_at > clock_timestamp()
                                  - make_interval(secs => %(providerless_recovery_quiet_seconds)s)
                          )
                          AND (%(dispatch_id)s::text IS NULL OR d.dispatch_id = %(dispatch_id)s::text)
                          AND (%(task_id)s::text IS NULL OR d.task_id = %(task_id)s::text)
                          AND (%(owner_id)s::text IS NULL OR t.owner_id = %(owner_id)s::text)
                          AND (%(workspace_id)s::text IS NULL OR t.workspace_id = %(workspace_id)s::text)
                        ORDER BY d.lease_expires_at, d.created_at, d.dispatch_id
                        FOR UPDATE OF t SKIP LOCKED
                        LIMIT %(batch_size)s""",
                    scope,
                )
                candidates = cursor.fetchall()
                for candidate in candidates:
                    task, raw_payload = self._load_locked_task(
                        cursor,
                        task_id=candidate["task_id"],
                        owner_id=candidate["owner_id"],
                        workspace_id=candidate["workspace_id"],
                    )
                    if (
                        self._execution_stop_active(raw_payload)
                        and candidate.get("external_action_started_at") is None
                    ):
                        continue
                    cursor.execute(
                        """SELECT *, lease_expires_at > clock_timestamp() AS lease_live
                             FROM openair_dispatch
                            WHERE dispatch_id = %s AND task_id = %s
                            FOR UPDATE""",
                        (candidate["dispatch_id"], candidate["task_id"]),
                    )
                    row = cursor.fetchone()
                    recovering_stored_unknown = (
                        candidate.get("observed_status") == DispatchStatus.SUBMISSION_UNKNOWN.value
                    )
                    valid_recovery_status = (
                        row is not None
                        and row["status"] == candidate.get("observed_status")
                        and row["status"] in {
                            DispatchStatus.SUBMISSION_AUTHORIZED.value,
                            DispatchStatus.SUBMISSION_UNKNOWN.value,
                        }
                    )
                    if (
                        row is None
                        or not valid_recovery_status
                        or row["lease_live"]
                        or row["lease_owner"] != candidate["observed_lease_owner"]
                        or int(row["attempt_count"]) != int(candidate["observed_attempt_count"])
                        or row["authorization_task_revision"] is None
                        or (
                            recovering_stored_unknown
                            and (
                                row.get("submission_mode") != "single_attempt_no_replay"
                                or row.get("external_action_started_at") is None
                                or row.get("result_storage_ref") is None
                            )
                        )
                    ):
                        continue
                    work_unit = self._persisted_dispatch_work_unit(task, row)
                    if candidate.get("external_action_started_at") is None:
                        recovered_work_unit_payload, _command_ids = (
                            ControllerCommandRepository.prepare_canonical_at_authorization(
                                cursor,
                                task_payload=task.model_dump(mode="json", by_alias=True),
                                work_unit_id=work_unit.work_unit_id,
                                owner_id=candidate["owner_id"],
                                workspace_id=candidate["workspace_id"],
                            )
                        )
                        if not recovered_work_unit_payload:
                            recovered_work_unit_payload = work_unit.model_dump(mode="json", by_alias=True)
                    else:
                        recovered_work_unit_payload = ControllerCommandRepository.restore_canonical_execution_context(
                            cursor,
                            task_id=candidate["task_id"],
                            owner_id=candidate["owner_id"],
                            workspace_id=candidate["workspace_id"],
                            work_unit_payload=work_unit.model_dump(mode="json", by_alias=True),
                        )
                    cursor.execute(
                        """UPDATE openair_dispatch
                              SET status = 'submission_authorized',
                                  lease_owner = %(lease_owner)s,
                                  attempt_count = CASE
                                      WHEN %(observed_status)s = 'submission_unknown'
                                      THEN attempt_count + 1
                                      ELSE attempt_count
                                  END,
                                  lease_expires_at = clock_timestamp() + make_interval(secs => %(lease_seconds)s),
                                  last_error_code = NULL,
                                  updated_at = clock_timestamp()
                            WHERE dispatch_id = %(dispatch_id)s
                              AND task_id = %(task_id)s
                              AND execution_decision_id = %(execution_decision_id)s
                              AND work_unit_id = %(work_unit_id)s
                              AND idempotency_key = %(idempotency_key)s
                              AND status = %(observed_status)s
                              AND execution_namespace = %(execution_namespace)s::text
                              AND lease_owner IS NOT DISTINCT FROM %(observed_lease_owner)s
                              AND attempt_count = %(observed_attempt_count)s
                              AND (lease_expires_at IS NULL
                                   OR lease_expires_at <= clock_timestamp())
                              AND authorization_task_revision = %(observed_authorization_task_revision)s
                              AND (
                                  %(observed_status)s <> 'submission_unknown'
                                  OR (submission_mode = 'single_attempt_no_replay'
                                      AND external_action_started_at IS NOT NULL
                                      AND result_storage_ref IS NOT NULL)
                              )
                        RETURNING dispatch_id, task_id, execution_decision_id, work_unit_id,
                                  idempotency_key, lease_owner, attempt_count,
                                  authorization_task_revision, submission_mode,
                                  external_action_started_at, result_storage_type,
                                  result_storage_scope, result_storage_ref""",
                        {**scope, **dict(candidate)},
                    )
                    updated = cursor.fetchone()
                    if updated is not None:
                        recovered_fence = DispatchFence(
                            dispatch_id=str(updated["dispatch_id"]),
                            task_id=str(updated["task_id"]),
                            execution_decision_id=str(updated["execution_decision_id"]),
                            work_unit_id=str(updated["work_unit_id"]),
                            idempotency_key=str(updated["idempotency_key"]),
                            lease_owner=str(updated["lease_owner"]),
                            attempt_count=int(updated["attempt_count"]),
                        )
                        recovered.append(
                            AuthorizedDispatch(
                                **dict(updated),
                                work_unit_payload=recovered_work_unit_payload,
                                external_attempt=self._authorized_external_attempt(
                                    cursor,
                                    recovered_fence,
                                ),
                                result_recovery_from_unknown=recovering_stored_unknown,
                            )
                        )
        return recovered

    def renew_authorized_dispatch(
        self,
        fence: AuthorizedDispatch,
        *,
        lease_seconds: int,
    ) -> bool:
        self._require_write_enabled()
        self._require_postgres()
        self._require_authorized_fence(fence)
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """UPDATE openair_dispatch
                          SET lease_expires_at = clock_timestamp() + make_interval(secs => %(lease_seconds)s),
                              updated_at = clock_timestamp()
                        WHERE dispatch_id = %(dispatch_id)s
                          AND task_id = %(task_id)s
                          AND execution_decision_id = %(execution_decision_id)s
                          AND work_unit_id = %(work_unit_id)s
                          AND idempotency_key = %(idempotency_key)s
                          AND status = 'submission_authorized'
                          AND lease_owner = %(lease_owner)s
                          AND attempt_count = %(attempt_count)s
                          AND authorization_task_revision = %(authorization_task_revision)s
                          AND lease_expires_at > clock_timestamp()""",
                    {**fence.__dict__, "lease_seconds": lease_seconds},
                )
                return cursor.rowcount == 1

    def claim_dispatch_continuation(
        self,
        *,
        lease_owner: str,
        lease_seconds: int,
        retry_delay_seconds: int,
        execution_namespace: str | None = None,
    ) -> DispatchContinuationFence | None:
        self._require_write_enabled()
        self._require_postgres()
        if not lease_owner.strip() or lease_seconds < 1 or retry_delay_seconds < 1:
            raise ValueError("invalid dispatch continuation claim parameters")
        namespace = (execution_namespace or self._settings.execution_namespace).strip()
        if not namespace:
            raise ValueError("execution_namespace is required for continuation claims")
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    '''UPDATE openair_dispatch_continuation
                          SET status = 'pending', lease_owner = NULL, lease_expires_at = NULL,
                              next_attempt_at = clock_timestamp() + make_interval(secs => %(delay)s),
                              last_error_code = 'continuation_lease_expired',
                              updated_at = clock_timestamp()
                        WHERE execution_namespace = %(namespace)s
                          AND status = 'claimed'
                          AND lease_expires_at <= clock_timestamp()''',
                    {"namespace": namespace, "delay": retry_delay_seconds},
                )
                return self._claim_due_dispatch_continuation(
                    cursor,
                    namespace=namespace,
                    lease_owner=lease_owner,
                    lease_seconds=lease_seconds,
                    zombie_deprioritize_attempts=(
                        self._settings.research_dispatch_continuation_zombie_claim_deprioritize_attempts
                    ),
                )

    @staticmethod
    def _claim_due_dispatch_continuation(
        cursor: psycopg.Cursor[Any],
        *,
        namespace: str,
        lease_owner: str,
        lease_seconds: int,
        zombie_deprioritize_attempts: int = 3,
    ) -> DispatchContinuationFence | None:
        cursor.execute(
            '''WITH candidate AS (
                   SELECT c.dispatch_id
                     FROM openair_dispatch_continuation c
                     JOIN openair_tasks t ON t.task_id = c.task_id
                    WHERE c.execution_namespace = %(namespace)s AND c.status = 'pending'
                      AND c.next_attempt_at <= clock_timestamp()
                    ORDER BY
                      CASE WHEN t.status IN ('completed', 'failed', 'cancelled', 'deleted')
                           THEN 1 ELSE 0 END,
                      CASE WHEN c.last_error_code = 'dispatch_continuation_no_responsibility_proof'
                                AND c.attempt_count >= %(zombie_deprioritize)s
                           THEN 1 ELSE 0 END,
                      c.next_attempt_at, c.updated_at, c.dispatch_id
                    FOR UPDATE OF c SKIP LOCKED LIMIT 1
               )
               UPDATE openair_dispatch_continuation c
                  SET status = 'claimed', attempt_count = c.attempt_count + 1,
                      lease_owner = %(owner)s,
                      lease_expires_at = clock_timestamp() + make_interval(secs => %(lease)s),
                      updated_at = clock_timestamp()
                 FROM candidate, openair_tasks t, openair_dispatch d
                WHERE c.dispatch_id = candidate.dispatch_id AND t.task_id = c.task_id
                  AND d.dispatch_id = c.dispatch_id
            RETURNING c.dispatch_id, c.task_id, c.execution_decision_id,
                      d.work_unit_id, t.owner_id, t.workspace_id,
                      c.lease_owner, c.attempt_count''',
            {
                "namespace": namespace,
                "owner": lease_owner,
                "lease": lease_seconds,
                "zombie_deprioritize": max(1, zombie_deprioritize_attempts),
            },
        )
        row = cursor.fetchone()
        return DispatchContinuationFence(**dict(row)) if row is not None else None

    def terminalize_dispatch_continuation(
        self,
        fence: DispatchContinuationFence,
        *,
        error_code: str,
    ) -> bool:
        """Close a continuation that can no longer make durable progress."""
        if not error_code.strip():
            raise ValueError("invalid dispatch continuation terminalization")
        return self._settle_dispatch_continuation(
            fence,
            completed=True,
            error_code=error_code,
            retry_delay_seconds=1,
        )

    def load_continuation_owner_for_work_unit(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        execution_decision_id: str,
        work_unit_id: str,
    ) -> ContinuationOwnerRef | None:
        """Read the exact durable owner for one frozen WorkUnit.

        This is a factual lookup only.  It never broadens to another WorkUnit or
        another task-scoped Dispatch/Continuation.
        """
        self._require_postgres()
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                # Scope authorization is established by the locked canonical Task.
                self._load_locked_task(
                    cursor,
                    task_id=task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                return self._exact_continuation_owner_cursor(
                    cursor,
                    task_id=task_id,
                    execution_decision_id=execution_decision_id,
                    work_unit_id=work_unit_id,
                )

    @staticmethod
    def _exact_continuation_owner_cursor(
        cursor: psycopg.Cursor[Any],
        *,
        task_id: str,
        execution_decision_id: str,
        work_unit_id: str,
    ) -> ContinuationOwnerRef | None:
        cursor.execute(
            """SELECT d.dispatch_id, d.status AS dispatch_status,
                      c.status AS continuation_status
                 FROM openair_dispatch d
                 LEFT JOIN openair_dispatch_continuation c
                   ON c.dispatch_id = d.dispatch_id
                WHERE d.task_id = %s
                  AND d.execution_decision_id = %s
                  AND d.work_unit_id = %s""",
            (task_id, execution_decision_id, work_unit_id),
        )
        owners: list[ContinuationOwnerRef] = []
        for row in cursor.fetchall():
            dispatch_status = str(row["dispatch_status"])
            continuation_status = (
                None
                if row["continuation_status"] is None
                else str(row["continuation_status"])
            )
            if dispatch_status in {
                DispatchStatus.PENDING.value,
                DispatchStatus.CLAIMED.value,
                DispatchStatus.SUBMISSION_AUTHORIZED.value,
                DispatchStatus.SUBMISSION_UNKNOWN.value,
            }:
                owners.append(
                    ContinuationOwnerRef(
                        kind=ContinuationOwnerKind.DISPATCH,
                        owner_id=str(row["dispatch_id"]),
                        execution_decision_id=execution_decision_id,
                        work_unit_id=work_unit_id,
                    )
                )
            elif (
                dispatch_status == DispatchStatus.SUBMITTED.value
                and continuation_status in {"pending", "claimed"}
            ):
                owners.append(
                    ContinuationOwnerRef(
                        kind=ContinuationOwnerKind.CONTINUATION,
                        owner_id=str(row["dispatch_id"]),
                        execution_decision_id=execution_decision_id,
                        work_unit_id=work_unit_id,
                    )
                )
        unique = {
            (row.kind.value, row.owner_id): row
            for row in owners
        }
        if len(unique) > 1:
            raise ResearchOrchestrationPersistenceError(
                "continuation_successor_owner_ambiguous"
            )
        return next(iter(unique.values()), None)

    def complete_dispatch_continuation(
        self,
        fence: DispatchContinuationFence,
        *,
        proof: ContinuationResponsibilityProof | None = None,
    ) -> bool:
        """Complete only after one named responsibility proof survives re-check.

        The repository validates references and durability in the same transaction
        as the fenced CAS.  It never substitutes Task status, selection ledger, or
        task-wide EXISTS queries for the proof supplied by the service.
        """
        self._require_write_enabled()
        self._require_postgres()
        if proof is None or proof.kind is ContinuationResponsibilityKind.NO_PROOF:
            return False
        if proof.source_execution_decision_id != fence.execution_decision_id:
            return False
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                task, raw_payload = self._load_locked_task(
                    cursor,
                    task_id=fence.task_id,
                    owner_id=fence.owner_id,
                    workspace_id=fence.workspace_id,
                )
                if not self._validate_continuation_responsibility_proof_cursor(
                    cursor,
                    task=task,
                    raw_payload=raw_payload,
                    fence=fence,
                    proof=proof,
                ):
                    return False
                return self._settle_dispatch_continuation_cursor(
                    cursor,
                    fence,
                    completed=True,
                    error_code=(
                        "task_stopped"
                        if proof.kind is ContinuationResponsibilityKind.USER_STOPPED
                        else None
                    ),
                )

    def _validate_continuation_responsibility_proof_cursor(
        self,
        cursor: psycopg.Cursor[Any],
        *,
        task: ResearchTaskV12,
        raw_payload: Mapping[str, Any],
        fence: DispatchContinuationFence,
        proof: ContinuationResponsibilityProof,
    ) -> bool:
        if proof.source_execution_decision_id != fence.execution_decision_id:
            return False
        if proof.kind is ContinuationResponsibilityKind.TASK_TERMINAL:
            return bool(
                proof.target_type == "task"
                and proof.target_id == task.task_id
                and proof.target_revision == task.revision
                and task.status in {
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                }
                and proof.reason_code == f"task_{task.status.value}"
            )
        if proof.kind is ContinuationResponsibilityKind.USER_STOPPED:
            return bool(
                proof.target_type == "task"
                and proof.target_id == task.task_id
                and proof.target_revision == task.revision
                and proof.reason_code == "user_stop_requested"
                and task.status is TaskStatus.CANCELLED
                and self._execution_stop_active(raw_payload)
            )
        if proof.kind is ContinuationResponsibilityKind.USER_RESPONSIBILITY:
            return self._validate_user_responsibility_proof_cursor(
                cursor, task=task, fence=fence, proof=proof
            )
        if proof.kind is ContinuationResponsibilityKind.SUCCESSOR_EXECUTION:
            return self._validate_successor_execution_proof_cursor(
                cursor, task=task, fence=fence, proof=proof
            )
        if proof.kind is ContinuationResponsibilityKind.SYSTEM_RECOVERY:
            return self._validate_system_recovery_proof_cursor(
                cursor, fence=fence, proof=proof
            )
        return False

    @staticmethod
    def _validate_system_recovery_proof_cursor(
        cursor: psycopg.Cursor[Any],
        *,
        fence: DispatchContinuationFence,
        proof: ContinuationResponsibilityProof,
    ) -> bool:
        if not (
            proof.target_type == "work_unit"
            and proof.target_id == fence.work_unit_id
            and proof.owner_kind is not None
            and proof.owner_id
            and proof.reason_code == "external_result_recovery_due"
        ):
            return False
        owner = ResearchOrchestrationRepository._exact_continuation_owner_cursor(
            cursor,
            task_id=fence.task_id,
            execution_decision_id=fence.execution_decision_id,
            work_unit_id=fence.work_unit_id,
        )
        return bool(
            owner is not None
            and owner.kind is proof.owner_kind
            and owner.owner_id == proof.owner_id
        )

    def _validate_user_responsibility_proof_cursor(
        self,
        cursor: psycopg.Cursor[Any],
        *,
        task: ResearchTaskV12,
        fence: DispatchContinuationFence,
        proof: ContinuationResponsibilityProof,
    ) -> bool:
        if not (
            proof.target_type
            and proof.target_id
            and proof.target_revision is not None
            and proof.user_action
            and proof.resume_path
        ):
            return False
        contract = user_responsibility_contract(proof.reason_code)
        if proof.reason_code == "research_design_confirmation_required":
            if (
                task.status is not TaskStatus.AWAITING_CONFIRMATION
                or contract != (proof.user_action, proof.resume_path)
            ):
                return False
            return any(
                row.research_design_id == proof.target_id
                and row.version == proof.target_revision
                and row.status is ResearchDesignStatus.DRAFT
                for row in task.research_design_versions
            )

        runtime = next(
            (
                row
                for row in task.substage_runtime_states
                if row.substage_instance_id == proof.target_id
                and row.instance_revision == proof.target_revision
            ),
            None,
        )
        if runtime is None or runtime.status_reason_code != proof.reason_code:
            return False
        if proof.reason_code in {
            "stage_confirmation_required",
            "manual_step_confirmation_required",
            "reviewer_human_gate_required",
        }:
            if (
                task.status is not TaskStatus.AWAITING_CONFIRMATION
                or contract != (proof.user_action, proof.resume_path)
            ):
                return False
            if runtime.status.value != "ready":
                return False
            if (
                proof.reason_code == "stage_confirmation_required"
                and task.execution_mode is not TaskExecutionMode.STAGE_CONFIRMATION
            ):
                return False
            if (
                proof.reason_code == "manual_step_confirmation_required"
                and task.execution_mode is not TaskExecutionMode.MANUAL_STEP
            ):
                return False
            return True

        if not needs_user_input(proof.reason_code):
            return False
        if (
            task.status is not TaskStatus.AWAITING_CONFIRMATION
            or runtime.status.value != "needs_input"
            or proof.user_action != NEEDS_INPUT_USER_ACTION
            or proof.resume_path != NEEDS_INPUT_RESUME_PATH
            or proof.selection_request_id
            != f"continuation:{fence.execution_decision_id}"
        ):
            return False
        cursor.execute(
            """SELECT outcome
                 FROM openair_execution_start_attempts
                WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                  AND operation = 'research_execution_selection'
                  AND idempotency_key_sha256 = %s
                  AND outcome LIKE 'blocked:%%'
                ORDER BY occurred_at DESC, attempt_id DESC
                LIMIT 1
                FOR UPDATE""",
            (
                task.task_id,
                fence.owner_id,
                fence.workspace_id,
                hashlib.sha256(proof.selection_request_id.encode("utf-8")).hexdigest(),
            ),
        )
        row = cursor.fetchone()
        if row is None:
            return False
        parsed = _parse_selection_blocked_outcome(row["outcome"])
        if parsed is None:
            return False
        reason_code, _command_hash, instance_id, blocker_facts = parsed
        blocker_revision = blocker_facts.get("taskRevision")
        return bool(
            reason_code == proof.reason_code
            and instance_id == proof.target_id
            and type(blocker_revision) is int
            and blocker_revision == task.revision
        )

    def _validate_successor_execution_proof_cursor(
        self,
        cursor: psycopg.Cursor[Any],
        *,
        task: ResearchTaskV12,
        fence: DispatchContinuationFence,
        proof: ContinuationResponsibilityProof,
    ) -> bool:
        if not (
            proof.successor_execution_decision_id
            and proof.successor_assignment_id
            and proof.successor_work_unit_id
            and proof.owner_kind is not None
            and proof.owner_id
            and proof.decision_lineage
            and proof.decision_lineage[0] == fence.execution_decision_id
            and proof.decision_lineage[-1] == proof.successor_execution_decision_id
        ):
            return False
        source_decision = next(
            (
                row for row in task.execution_decisions
                if row.execution_decision_id == fence.execution_decision_id
            ),
            None,
        )
        source_work = next(
            (
                row for row in task.work_units
                if row.work_unit_id == fence.work_unit_id
                and row.execution_decision_id == fence.execution_decision_id
            ),
            None,
        )
        successor_decision = next(
            (
                row for row in task.execution_decisions
                if row.execution_decision_id == proof.successor_execution_decision_id
            ),
            None,
        )
        successor_work = next(
            (
                row for row in task.work_units
                if row.work_unit_id == proof.successor_work_unit_id
                and row.execution_decision_id == proof.successor_execution_decision_id
                and row.assignment_id == proof.successor_assignment_id
                and row.source_mode is WorkUnitSourceMode.LIVE
            ),
            None,
        )
        if (
            source_decision is None
            or source_work is None
            or successor_decision is None
            or successor_work is None
            or successor_work.work_unit_id == source_work.work_unit_id
            or proof.target_type != "work_unit"
            or proof.target_id != proof.successor_work_unit_id
            or proof.target_revision != successor_work.substage_instance_revision
            or successor_decision.status in {
                ExecutionDecisionStatus.SUPERSEDED,
                ExecutionDecisionStatus.CANCELLED,
            }
        ):
            return False
        successor_assignment = next(
            (
                row for row in successor_decision.assignments
                if row.assignment_id == proof.successor_assignment_id
            ),
            None,
        )
        if successor_assignment is None:
            return False

        lineage = tuple(proof.decision_lineage)
        if len(lineage) == 1:
            if (
                proof.reason_code != "same_decision_dependency_released"
                or proof.successor_execution_decision_id != fence.execution_decision_id
                or source_decision.status is not ExecutionDecisionStatus.DISPATCHED
                or proof.selection_request_id is not None
                or source_work.assignment_id
                not in successor_assignment.dependency_assignment_ids
            ):
                return False
        else:
            if proof.reason_code != "successor_execution_started":
                return False
            expected_first_request = f"continuation:{fence.execution_decision_id}"
            if proof.selection_request_id != expected_first_request:
                return False
            decisions_by_id = {
                row.execution_decision_id: row for row in task.execution_decisions
            }
            if len(set(lineage)) != len(lineage):
                return False
            for previous_id, next_id in zip(lineage, lineage[1:]):
                previous = decisions_by_id.get(previous_id)
                following = decisions_by_id.get(next_id)
                expected_request = f"continuation:{previous_id}"
                if (
                    previous is None
                    or following is None
                    or previous.status is not ExecutionDecisionStatus.CLOSED
                    or following.selection_request_id != expected_request
                ):
                    return False
                cursor.execute(
                    """SELECT execution_decision_id
                         FROM openair_execution_selection_request
                        WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                          AND selection_request_id = %s
                        FOR UPDATE""",
                    (
                        task.task_id,
                        fence.owner_id,
                        fence.workspace_id,
                        expected_request,
                    ),
                )
                selection_row = cursor.fetchone()
                if (
                    selection_row is None
                    or str(selection_row["execution_decision_id"]) != next_id
                ):
                    return False

        owner = self._exact_continuation_owner_cursor(
            cursor,
            task_id=task.task_id,
            execution_decision_id=proof.successor_execution_decision_id,
            work_unit_id=proof.successor_work_unit_id,
        )
        return bool(
            owner is not None
            and owner.kind is proof.owner_kind
            and owner.owner_id == proof.owner_id
        )

    def renew_dispatch_continuation(
        self,
        fence: DispatchContinuationFence,
        *,
        lease_seconds: int,
    ) -> bool:
        self._require_write_enabled()
        self._require_postgres()
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """UPDATE openair_dispatch_continuation
                          SET lease_expires_at = clock_timestamp() + make_interval(secs => %(lease_seconds)s),
                              updated_at = clock_timestamp()
                        WHERE dispatch_id = %(dispatch_id)s AND task_id = %(task_id)s
                          AND execution_decision_id = %(execution_decision_id)s
                          AND status = 'claimed' AND lease_owner = %(lease_owner)s
                          AND attempt_count = %(attempt_count)s
                          AND lease_expires_at > clock_timestamp()""",
                    {**fence.__dict__, "lease_seconds": lease_seconds},
                )
                return cursor.rowcount == 1

    def pause_dispatch_continuation(
        self,
        fence: DispatchContinuationFence,
        *,
        proof: ContinuationResponsibilityProof,
    ) -> bool:
        """Compatibility entrypoint; completion still goes through the one proof gate."""
        return self.complete_dispatch_continuation(fence, proof=proof)

    def fail_dispatch_continuation(
        self,
        fence: DispatchContinuationFence,
        *,
        error_code: str,
        retry_delay_seconds: int,
    ) -> bool:
        """Persist a recoverable blocker while retaining this continuation owner.

        Internal-error exhaustion is not business completion.  The same fenced
        continuation is returned to ``pending`` with a future retry in the same
        transaction that projects the canonical recoverable blocker.
        """
        if not error_code.strip() or retry_delay_seconds < 1:
            raise ValueError("invalid dispatch continuation failure")
        if needs_user_input(error_code):
            raise ResearchOrchestrationPersistenceError(
                "system_recovery_blocker_cannot_enter_needs_input"
            )
        self._require_write_enabled()
        self._require_postgres()
        now = datetime.now(timezone.utc)
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                task, raw_payload = self._load_locked_task(
                    cursor,
                    task_id=fence.task_id,
                    owner_id=fence.owner_id,
                    workspace_id=fence.workspace_id,
                )
                if not self._lock_dispatch_continuation_fence_cursor(cursor, fence):
                    return False
                if task.status not in {
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                }:
                    payload = task.model_dump(mode="json", by_alias=True)
                    work_unit = next(
                        (
                            row
                            for row in payload["workUnits"]
                            if row["workUnitId"] == fence.work_unit_id
                            and row["executionDecisionId"] == fence.execution_decision_id
                        ),
                        None,
                    )
                    if work_unit is None:
                        raise ResearchOrchestrationPersistenceError(
                            "dispatch_continuation_work_unit_missing"
                        )
                    if work_unit["status"] == WorkUnitStatus.COMPLETED.value:
                        # A successor scheduling failure cannot revoke delivered
                        # research work. Keep recovery ownership in its queue.
                        return self._settle_dispatch_continuation_cursor(
                            cursor, fence, completed=False, error_code=error_code,
                            retry_delay_seconds=retry_delay_seconds,
                            require_live_lease=False,
                        )
                    runtime = next(
                        (
                            row
                            for row in payload["substageRuntimeStates"]
                            if row["substageInstanceId"] == work_unit["substageInstanceId"]
                            and row["instanceRevision"] == work_unit["substageInstanceRevision"]
                        ),
                        None,
                    )
                    if runtime is None:
                        raise ResearchOrchestrationPersistenceError(
                            "dispatch_continuation_runtime_missing"
                        )
                    runtime.update(
                        {
                            "status": runtime_blocker_status(error_code),
                            "statusReasonCode": error_code,
                            "activeExecutionDecisionId": None,
                            "updatedAt": now.isoformat(),
                        }
                    )
                    updated = self._reconcile_task_lifecycle_with_frozen_snapshot(
                        ResearchTaskV12.model_validate(payload),
                        occurred_at=now,
                    )
                    self._write_locked_task(cursor, raw_payload, updated)

                # Compute the recovery deadline only after canonical blocker
                # reconciliation. The row lock preserves the validated fence,
                # so an elapsed lease cannot transfer ownership underneath this
                # transaction while the final database-time settlement runs.
                return self._settle_dispatch_continuation_cursor(
                    cursor,
                    fence,
                    completed=False,
                    error_code=error_code,
                    retry_delay_seconds=retry_delay_seconds,
                    require_live_lease=False,
                )

    def defer_dispatch_continuation(
        self, fence: DispatchContinuationFence, *, error_code: str, retry_delay_seconds: int,
    ) -> bool:
        if not error_code.strip() or retry_delay_seconds < 1:
            raise ValueError("invalid dispatch continuation retry")
        return self._settle_dispatch_continuation(
            fence, completed=False, error_code=error_code,
            retry_delay_seconds=retry_delay_seconds,
        )

    def _settle_dispatch_continuation(
        self,
        fence: DispatchContinuationFence,
        *,
        completed: bool,
        error_code: str | None = None,
        retry_delay_seconds: int = 1,
    ) -> bool:
        self._require_write_enabled()
        self._require_postgres()
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                return self._settle_dispatch_continuation_cursor(
                    cursor,
                    fence,
                    completed=completed,
                    error_code=error_code,
                    retry_delay_seconds=retry_delay_seconds,
                )

    @staticmethod
    def _lock_dispatch_continuation_fence_cursor(
        cursor: psycopg.Cursor[Any],
        fence: DispatchContinuationFence,
    ) -> bool:
        cursor.execute(
            '''SELECT 1 FROM openair_dispatch_continuation
                WHERE dispatch_id = %(dispatch_id)s AND task_id = %(task_id)s
                  AND execution_decision_id = %(execution_decision_id)s
                  AND status = 'claimed' AND lease_owner = %(lease_owner)s
                  AND attempt_count = %(attempt_count)s
                  AND lease_expires_at > clock_timestamp()
                FOR UPDATE''',
            fence.__dict__,
        )
        return cursor.fetchone() is not None

    @staticmethod
    def _settle_dispatch_continuation_cursor(
        cursor: psycopg.Cursor[Any],
        fence: DispatchContinuationFence,
        *,
        completed: bool,
        error_code: str | None = None,
        retry_delay_seconds: int = 1,
        require_live_lease: bool = True,
    ) -> bool:
        target = "completed" if completed else "pending"
        cursor.execute(
            '''UPDATE openair_dispatch_continuation
                  SET status = %(target)s, lease_owner = NULL, lease_expires_at = NULL,
                      next_attempt_at = CASE WHEN %(completed)s THEN next_attempt_at
                          ELSE clock_timestamp() + make_interval(secs => %(delay)s) END,
                      last_error_code = %(error)s,
                      completed_at = CASE WHEN %(completed)s THEN clock_timestamp() ELSE NULL END,
                      updated_at = clock_timestamp()
                WHERE dispatch_id = %(dispatch_id)s AND task_id = %(task_id)s
                  AND execution_decision_id = %(execution_decision_id)s
                  AND status = 'claimed' AND lease_owner = %(lease_owner)s
                  AND attempt_count = %(attempt_count)s
                  AND (
                      NOT %(require_live_lease)s
                      OR lease_expires_at > clock_timestamp()
                  )''',
            {
                **fence.__dict__,
                "target": target,
                "completed": completed,
                "delay": retry_delay_seconds,
                "error": error_code,
                "require_live_lease": require_live_lease,
            },
        )
        return cursor.rowcount == 1

    def authorize_dispatch_submission(
        self,
        fence: DispatchFence,
        *,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int,
        submission_mode: str = "durable_idempotent",
        lease_seconds: int = 30,
    ) -> AuthorizedDispatch | None:
        """Lock Task then Dispatch and persist authorization without provider I/O."""
        self._require_write_enabled()
        self._require_postgres()
        if submission_mode not in {"durable_idempotent", "single_attempt_no_replay"}:
            raise ValueError("invalid submission mode")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """WITH locked_task AS MATERIALIZED (
                           SELECT payload, revision
                             FROM openair_tasks
                            WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                              AND status <> 'deleted'
                            FOR UPDATE
                       )
                       SELECT d.*,
                              locked_task.payload AS locked_task_payload,
                              locked_task.revision AS locked_task_revision
                         FROM locked_task
                         LEFT JOIN LATERAL (
                             SELECT *, lease_expires_at > clock_timestamp() AS lease_live
                               FROM openair_dispatch
                              WHERE dispatch_id = %s AND task_id = %s
                              FOR UPDATE
                         ) AS d ON TRUE""",
                    (
                        fence.task_id,
                        owner_id,
                        workspace_id,
                        fence.dispatch_id,
                        fence.task_id,
                    ),
                )
                dispatch_row = cursor.fetchone()
                if dispatch_row is None:
                    raise ResearchTaskNotFoundError("research_task_not_found")
                task, raw_payload = self._task_from_database_payload(
                    dispatch_row["locked_task_payload"],
                    int(dispatch_row["locked_task_revision"]),
                )
                if dispatch_row["dispatch_id"] is None:
                    return None
                if not self._matches_fence(dispatch_row, fence, status=DispatchStatus.CLAIMED):
                    return None
                if not dispatch_row["lease_live"]:
                    return None
                if task.revision != expected_task_revision or raw_payload.get("revision") != expected_task_revision:
                    raise DispatchAuthorizationError("task_revision_mismatch")
                if self._execution_stop_active(raw_payload):
                    return None
                work_unit = self._validate_authorization(task, dispatch_row)
                work_unit_payload, _command_ids = ControllerCommandRepository.prepare_canonical_at_authorization(
                    cursor,
                    task_payload=task.model_dump(mode="json", by_alias=True),
                    work_unit_id=work_unit.work_unit_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                if not work_unit_payload:
                    work_unit_payload = work_unit.model_dump(mode="json", by_alias=True)
                cursor.execute(
                    """UPDATE openair_dispatch
                          SET status = 'submission_authorized',
                              authorization_task_revision = %s,
                              submission_mode = %s,
                              lease_expires_at = clock_timestamp()
                                  + make_interval(secs => %s),
                              updated_at = clock_timestamp()
                        WHERE dispatch_id = %s
                          AND task_id = %s
                          AND execution_decision_id = %s
                          AND work_unit_id = %s
                          AND idempotency_key = %s
                          AND status = 'claimed'
                          AND lease_owner = %s
                          AND attempt_count = %s
                          AND lease_expires_at > clock_timestamp()""",
                    (
                        expected_task_revision,
                        submission_mode,
                        lease_seconds,
                        fence.dispatch_id,
                        fence.task_id,
                        fence.execution_decision_id,
                        fence.work_unit_id,
                        fence.idempotency_key,
                        fence.lease_owner,
                        fence.attempt_count,
                    ),
                )
                if cursor.rowcount != 1:
                    return None
                external_attempt = self._authorized_external_attempt(cursor, fence)
        return AuthorizedDispatch(
            **fence.__dict__,
            authorization_task_revision=expected_task_revision,
            work_unit_payload=work_unit_payload,
            external_attempt=external_attempt,
            submission_mode=submission_mode,
        )

    @staticmethod
    def _authorized_external_attempt(
        cursor: psycopg.Cursor[Any],
        fence: DispatchFence,
    ) -> ExternalAttemptIdentity:
        cursor.execute(
            """SELECT task_id, execution_decision_id, work_unit_id,
                      capability_call_id, attempt_number
                 FROM openair_external_execution
                WHERE dispatch_id = %s
                FOR SHARE""",
            (fence.dispatch_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return initial_external_attempt_identity(fence)
        if (
            row["task_id"] != fence.task_id
            or row["execution_decision_id"] != fence.execution_decision_id
            or row["work_unit_id"] != fence.work_unit_id
        ):
            raise ResearchOrchestrationPersistenceError(
                "external_execution_lineage_conflict"
            )
        return ExternalAttemptIdentity(
            capability_call_id=str(row["capability_call_id"]),
            attempt_number=int(row["attempt_number"]),
        )

    @staticmethod
    def _external_execution_values(
        *,
        fence: AuthorizedDispatch,
        work_unit: Any,
        owner_id: str,
        workspace_id: str,
        provider_id: str,
        adapter: str,
    ) -> dict[str, Any]:
        if not owner_id or not workspace_id:
            raise ResearchOrchestrationPersistenceError(
                "external_execution_scope_missing"
            )
        return {
            "dispatch_id": fence.dispatch_id,
            "task_id": fence.task_id,
            "execution_decision_id": fence.execution_decision_id,
            "work_unit_id": fence.work_unit_id,
            "capability_call_id": fence.external_attempt.capability_call_id,
            "attempt_number": fence.external_attempt.attempt_number,
            "owner_id": owner_id,
            "workspace_id": workspace_id,
            "provider_id": provider_id,
            "adapter": adapter,
            "provider_idempotency_key": fence.idempotency_key,
            "canonical_payload_fingerprint": ResearchOrchestrationRepository._canonical_fingerprint(
                work_unit.model_dump(mode="json", by_alias=True)
            ),
        }

    @staticmethod
    def _write_external_attempt(
        cursor: psycopg.Cursor[Any],
        *,
        operation: str,
        values: Mapping[str, Any],
        prior_attempt: ExternalAttemptIdentity | None = None,
    ) -> Mapping[str, Any]:
        if operation == "start":
            cursor.execute(
                """INSERT INTO openair_external_execution (
                       dispatch_id, task_id, execution_decision_id, work_unit_id,
                       capability_call_id, attempt_number, owner_id, workspace_id,
                       provider_id, adapter, provider_idempotency_key, external_state,
                       failure_code, canonical_payload_fingerprint)
                   VALUES (
                       %(dispatch_id)s, %(task_id)s, %(execution_decision_id)s,
                       %(work_unit_id)s, %(capability_call_id)s, %(attempt_number)s,
                       %(owner_id)s, %(workspace_id)s, %(provider_id)s, %(adapter)s,
                       %(provider_idempotency_key)s, 'unknown',
                       'external_action_outcome_pending', %(canonical_payload_fingerprint)s)
                   ON CONFLICT (dispatch_id) DO NOTHING""",
                values,
            )
            cursor.execute(
                """UPDATE openair_external_execution
                      SET external_state = 'unknown',
                          failure_code = 'external_action_outcome_pending',
                          provider_id = %(provider_id)s,
                          adapter = %(adapter)s,
                          canonical_payload_fingerprint = %(canonical_payload_fingerprint)s,
                          updated_at = clock_timestamp()
                    WHERE dispatch_id = %(dispatch_id)s
                      AND capability_call_id = %(capability_call_id)s
                      AND attempt_number = %(attempt_number)s
                      AND external_state = 'never_submitted'""",
                values,
            )
        elif operation == "retry":
            if prior_attempt is None:
                raise ValueError("retry attempt transition requires prior identity")
            cursor.execute(
                """UPDATE openair_external_execution
                      SET capability_call_id = %(capability_call_id)s,
                          attempt_number = %(attempt_number)s,
                          external_state = 'never_submitted',
                          failure_code = NULL,
                          canonical_payload_fingerprint = %(canonical_payload_fingerprint)s,
                          updated_at = clock_timestamp()
                    WHERE dispatch_id = %(dispatch_id)s
                      AND capability_call_id = %(prior_capability_call_id)s
                      AND attempt_number = %(prior_attempt_number)s
                      AND (external_state IN ('never_submitted', 'known_failed')
                           OR (%(proven_no_external_action_retry)s AND external_state = 'unknown'))
                      AND remote_run_id IS NULL""",
                {
                    **values,
                    "proven_no_external_action_retry": bool(
                        values.get("proven_no_external_action_retry", False)
                    ),
                    "prior_capability_call_id": prior_attempt.capability_call_id,
                    "prior_attempt_number": prior_attempt.attempt_number,
                },
            )
            if cursor.rowcount != 1:
                raise ExternalRecoveryConflictError(
                    "retry_external_state_conflict"
                )
        else:
            raise ValueError("invalid external attempt transition")

        cursor.execute(
            """SELECT * FROM openair_external_execution
                WHERE dispatch_id = %s FOR UPDATE""",
            (values["dispatch_id"],),
        )
        persisted = cursor.fetchone()
        if persisted is None:
            raise ResearchOrchestrationPersistenceError(
                "external_execution_fact_missing"
            )
        if (
            persisted["capability_call_id"] != values["capability_call_id"]
            or int(persisted["attempt_number"]) != int(values["attempt_number"])
            or persisted["canonical_payload_fingerprint"]
            != values["canonical_payload_fingerprint"]
        ):
            raise ResearchOrchestrationPersistenceError(
                "external_execution_identity_conflict"
            )
        return persisted

    @staticmethod
    def _start_external_attempt(
        cursor: psycopg.Cursor[Any],
        *,
        fence: AuthorizedDispatch,
        work_unit: Any,
        owner_id: str,
        workspace_id: str,
        provider_id: str,
        adapter: str,
    ) -> None:
        values = ResearchOrchestrationRepository._external_execution_values(
            fence=fence,
            work_unit=work_unit,
            owner_id=owner_id,
            workspace_id=workspace_id,
            provider_id=provider_id,
            adapter=adapter,
        )
        persisted = ResearchOrchestrationRepository._write_external_attempt(
            cursor,
            operation="start",
            values=values,
        )
        if any(
            persisted[key] != value
            for key, value in values.items()
            if key != "dispatch_id"
        ):
            raise ResearchOrchestrationPersistenceError(
                "external_execution_identity_conflict"
            )

    @staticmethod
    def _insert_unknown_outcome_evidence(
        cursor: psycopg.Cursor[Any],
        *,
        external_execution: Mapping[str, Any],
        error_code: str,
    ) -> str:
        identity = ":".join(
            (
                str(external_execution["dispatch_id"]),
                str(external_execution["capability_call_id"]),
                str(external_execution["attempt_number"]),
                error_code,
            )
        )
        evidence_id = (
            "evidence:dispatch:"
            + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        )
        cursor.execute(
            """SELECT evidence_id, task_id, execution_decision_id, work_unit_id,
                      capability_call_id, dispatch_id, attempt_number, owner_id,
                      workspace_id, provider_id, adapter, normalized_finish_reason
                 FROM openair_external_evidence
                WHERE evidence_id = %s""",
            (evidence_id,),
        )
        existing = cursor.fetchone()
        if existing is not None:
            expected = {
                "evidence_id": evidence_id,
                "task_id": str(external_execution["task_id"]),
                "execution_decision_id": str(
                    external_execution["execution_decision_id"]
                ),
                "work_unit_id": str(external_execution["work_unit_id"]),
                "capability_call_id": str(
                    external_execution["capability_call_id"]
                ),
                "dispatch_id": str(external_execution["dispatch_id"]),
                "attempt_number": int(external_execution["attempt_number"]),
                "owner_id": str(external_execution["owner_id"]),
                "workspace_id": str(external_execution["workspace_id"]),
                "provider_id": str(external_execution["provider_id"]),
                "adapter": str(external_execution["adapter"]),
                "normalized_finish_reason": error_code,
            }
            if any(existing[key] != value for key, value in expected.items()):
                raise ResearchOrchestrationPersistenceError(
                    "external_execution_evidence_identity_conflict"
                )
            return evidence_id
        metadata = {
            "adapter": str(external_execution["adapter"]),
            "errorCode": error_code,
            "providerId": str(external_execution["provider_id"]),
            "source": "research_dispatch_outcome",
        }
        request = EvidenceCaptureRequest(
            evidence_id=evidence_id,
            lineage=EvidenceLineage(
                task_id=str(external_execution["task_id"]),
                execution_decision_id=str(
                    external_execution["execution_decision_id"]
                ),
                work_unit_id=str(external_execution["work_unit_id"]),
                capability_call_id=str(
                    external_execution["capability_call_id"]
                ),
                dispatch_id=str(external_execution["dispatch_id"]),
                attempt_number=int(external_execution["attempt_number"]),
            ),
            owner_id=str(external_execution["owner_id"]),
            workspace_id=str(external_execution["workspace_id"]),
            provider_id=str(external_execution["provider_id"]),
            adapter=str(external_execution["adapter"]),
            evidence_kind=EvidenceKind.TRANSPORT_FAILURE,
            failure_class=FailureClass.TRANSPORT_UNKNOWN,
            normalized_finish_reason=error_code,
            diagnostics=(
                SafeValidatorDiagnostic(
                    error_code=error_code,
                    category="external_result_unknown",
                    validator_keyword="dispatch_outcome",
                ),
            ),
            canonical_metadata_fingerprint=hashlib.sha256(
                json.dumps(
                    metadata,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("ascii")
            ).hexdigest(),
            canonical_payload_fingerprint=str(
                external_execution["canonical_payload_fingerprint"]
            ),
            reconciliation_evidence_refs=(),
        )
        envelope = ExternalEvidenceRecorder().record(
            request,
            b"",
        )
        ResearchExternalEvidenceRepository.insert_with_cursor(cursor, envelope)
        return evidence_id

    def mark_external_action_started(
        self,
        fence: AuthorizedDispatch,
        *,
        provider_id: str,
        adapter: str,
        lease_seconds: int = 30,
    ) -> AuthorizedDispatch | None:
        """Lock Task then Dispatch and persist the boundary before provider transport."""
        self._require_write_enabled()
        self._require_postgres()
        self._require_authorized_fence(fence)
        provider_id = str(provider_id or "").strip()
        adapter = str(adapter or "").strip()
        if not provider_id or not adapter:
            raise ValueError("external execution provider identity is required")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """WITH locked_task AS MATERIALIZED (
                           SELECT payload, revision
                             FROM openair_tasks
                            WHERE task_id = %(task_id)s
                              AND status <> 'deleted'
                            FOR UPDATE
                       )
                       SELECT d.*,
                              locked_task.payload AS locked_task_payload,
                              locked_task.revision AS locked_task_revision
                         FROM locked_task
                         LEFT JOIN LATERAL (
                             SELECT *, lease_expires_at > clock_timestamp() AS lease_live
                               FROM openair_dispatch
                              WHERE dispatch_id = %(dispatch_id)s
                                AND task_id = %(task_id)s
                              FOR UPDATE
                         ) AS d ON TRUE""",
                    fence.__dict__,
                )
                dispatch_row = cursor.fetchone()
                if dispatch_row is None or dispatch_row["dispatch_id"] is None:
                    return None
                task, raw_payload = self._task_from_database_payload(
                    dispatch_row["locked_task_payload"],
                    int(dispatch_row["locked_task_revision"]),
                )
                if (
                    task.revision != fence.authorization_task_revision
                    or raw_payload.get("revision") != fence.authorization_task_revision
                    or not self._matches_authorized_fence(dispatch_row, fence)
                    or not dispatch_row["lease_live"]
                ):
                    return None
                try:
                    work_unit = self._validate_authorization(task, dispatch_row)
                except DispatchAuthorizationError:
                    return None
                cursor.execute(
                    """UPDATE openair_dispatch
                          SET external_action_started_at = COALESCE(
                                  external_action_started_at, clock_timestamp()
                              ),
                              lease_expires_at = clock_timestamp()
                                  + make_interval(secs => %(lease_seconds)s),
                              updated_at = clock_timestamp()
                        WHERE dispatch_id = %(dispatch_id)s
                          AND task_id = %(task_id)s
                          AND execution_decision_id = %(execution_decision_id)s
                          AND work_unit_id = %(work_unit_id)s
                          AND idempotency_key = %(idempotency_key)s
                          AND status = 'submission_authorized'
                          AND lease_owner = %(lease_owner)s
                          AND attempt_count = %(attempt_count)s
                          AND authorization_task_revision = %(authorization_task_revision)s
                          AND submission_mode = %(submission_mode)s
                          AND external_action_started_at IS NULL
                          AND lease_expires_at > clock_timestamp()""",
                    {**fence.__dict__, "lease_seconds": lease_seconds},
                )
                if cursor.rowcount != 1:
                    return None
                self._start_external_attempt(
                    cursor,
                    fence=fence,
                    work_unit=work_unit,
                    owner_id=str(raw_payload.get("ownerId") or ""),
                    workspace_id=str(raw_payload.get("workspaceId") or ""),
                    provider_id=provider_id,
                    adapter=adapter,
                )
                command_ids = ControllerCommandRepository.command_ids_in_execution_payload(
                    fence.work_unit_payload
                )
                if command_ids:
                    command_owner_id = str(raw_payload.get("ownerId") or "").strip()
                    command_workspace_id = str(raw_payload.get("workspaceId") or "").strip()
                    if not command_owner_id or not command_workspace_id:
                        raise ResearchOrchestrationPersistenceError(
                            "controller_command_scope_missing_at_action_start"
                        )
                    ControllerCommandRepository.mark_canonical_applied_at_action_start(
                        cursor,
                        task_id=fence.task_id,
                        owner_id=command_owner_id,
                        workspace_id=command_workspace_id,
                        execution_decision_id=fence.execution_decision_id,
                        work_unit_id=fence.work_unit_id,
                        command_ids=command_ids,
                    )
                updated_task = self._task_with_running_dispatch_call(
                    task,
                    fence,
                    datetime.now(timezone.utc),
                )
                self._write_locked_task(cursor, raw_payload, updated_task)
                cursor.execute(
                    """SELECT external_action_started_at
                         FROM openair_dispatch
                        WHERE dispatch_id = %s AND task_id = %s""",
                    (fence.dispatch_id, fence.task_id),
                )
                started = cursor.fetchone()
                if started is None:
                    raise ResearchOrchestrationPersistenceError("dispatch_action_start_readback_missing")
                return replace(
                    fence,
                    external_action_started_at=started["external_action_started_at"],
                    task_revision_after_action_started=task.revision + 1,
                )

    def record_dispatch_outcome(
        self,
        fence: AuthorizedDispatch,
        *,
        owner_id: str,
        workspace_id: str,
        outcome: str,
        remote_run_id: str | None = None,
        error_code: str | None = None,
        failure_detail: Mapping[str, str] | None = None,
        provider_id: str | None = None,
        adapter: str | None = None,
    ) -> bool:
        """Persist an authorized external fact; lease expiry is deliberately ignored."""
        self._require_write_enabled()
        self._require_postgres()
        self._require_authorized_fence(fence)
        if outcome not in {
            "accepted",
            "running",
            "completed",
            "known_rejected",
            "known_failed",
            "not_submitted",
            "unknown",
        }:
            raise ValueError("invalid dispatch outcome")
        if outcome == "accepted" and not str(remote_run_id or "").strip():
            raise ValueError("accepted outcome requires remote_run_id")
        if outcome not in {"accepted", "running", "completed"} and not str(error_code or "").strip():
            raise ValueError("non-accepted outcome requires error_code")
        if failure_detail is not None and (
            outcome not in {"known_rejected", "known_failed"}
            or set(failure_detail) - {"failedStage", "firstMismatch"}
            or not all(isinstance(value, str) and value for value in failure_detail.values())
        ):
            raise ValueError("invalid dispatch failure detail")
        next_status = {
            "accepted": DispatchStatus.SUBMITTED,
            "running": DispatchStatus.SUBMITTED,
            "completed": DispatchStatus.SUBMITTED,
            "known_rejected": DispatchStatus.FAILED,
            "known_failed": DispatchStatus.FAILED,
            "not_submitted": DispatchStatus.FAILED,
            "unknown": DispatchStatus.SUBMISSION_UNKNOWN,
        }[outcome]
        now = datetime.now(timezone.utc)
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                task, raw_payload = self._load_locked_task(
                    cursor,
                    task_id=fence.task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                cursor.execute(
                    """SELECT * FROM openair_dispatch
                        WHERE dispatch_id = %s AND task_id = %s
                        FOR UPDATE""",
                    (fence.dispatch_id, fence.task_id),
                )
                dispatch_row = cursor.fetchone()
                if dispatch_row is None or not self._matches_authorized_fence(
                    dispatch_row, fence
                ):
                    return False
                updated_task = self._task_with_outcome(
                    task,
                    fence,
                    outcome,
                    error_code,
                    now,
                    external_action_started=dispatch_row["external_action_started_at"] is not None,
                    raw_payload=raw_payload,
                )
                detail_patch = (
                    {"dispatchFailureDetail": dict(failure_detail)}
                    if failure_detail
                    else None
                )
                self._write_locked_task(cursor, raw_payload, updated_task, payload_patch=detail_patch)
                cursor.execute(
                    """UPDATE openair_dispatch
                          SET status = %(status)s,
                              remote_run_id = %(remote_run_id)s::text,
                              last_error_code = %(error_code)s::text,
                              last_failure_at = CASE
                                  WHEN %(error_code)s::text IS NULL THEN last_failure_at
                                  ELSE clock_timestamp()
                              END,
                              next_attempt_at = NULL,
                              lease_owner = NULL,
                              lease_expires_at = NULL,
                              updated_at = clock_timestamp()
                        WHERE dispatch_id = %(dispatch_id)s
                          AND status = 'submission_authorized'
                          AND task_id = %(task_id)s
                          AND execution_decision_id = %(execution_decision_id)s
                          AND work_unit_id = %(work_unit_id)s
                          AND idempotency_key = %(idempotency_key)s
                          AND lease_owner = %(lease_owner)s
                          AND attempt_count = %(attempt_count)s
                          AND authorization_task_revision = %(authorization_task_revision)s""",
                    {
                        "status": next_status.value,
                        "remote_run_id": remote_run_id,
                        "error_code": error_code,
                        "dispatch_id": fence.dispatch_id,
                        "task_id": fence.task_id,
                        "execution_decision_id": fence.execution_decision_id,
                        "work_unit_id": fence.work_unit_id,
                        "idempotency_key": fence.idempotency_key,
                        "lease_owner": fence.lease_owner,
                        "attempt_count": fence.attempt_count,
                        "authorization_task_revision": fence.authorization_task_revision,
                    },
                )
                if cursor.rowcount != 1:
                    raise ResearchOrchestrationPersistenceError("dispatch_outcome_race")
                cursor.execute(
                    """SELECT * FROM openair_external_execution
                        WHERE dispatch_id = %s FOR UPDATE""",
                    (fence.dispatch_id,),
                )
                external_execution = cursor.fetchone()
                if external_execution is None:
                    normalized_provider_id = str(provider_id or "").strip()
                    normalized_adapter = str(adapter or "").strip()
                    if not normalized_provider_id or not normalized_adapter:
                        raise ResearchOrchestrationPersistenceError(
                            "external_execution_fact_missing"
                        )
                    work_unit = next(
                        (
                            row
                            for row in task.work_units
                            if row.work_unit_id == fence.work_unit_id
                        ),
                        None,
                    )
                    if work_unit is None:
                        raise ResearchOrchestrationPersistenceError(
                            "dispatch_work_unit_missing"
                        )
                    self._start_external_attempt(
                        cursor,
                        fence=fence,
                        work_unit=work_unit,
                        owner_id=owner_id,
                        workspace_id=workspace_id,
                        provider_id=normalized_provider_id,
                        adapter=normalized_adapter,
                    )
                    cursor.execute(
                        """SELECT * FROM openair_external_execution
                            WHERE dispatch_id = %s FOR UPDATE""",
                        (fence.dispatch_id,),
                    )
                    external_execution = cursor.fetchone()
                if external_execution is None:
                    raise ResearchOrchestrationPersistenceError(
                        "external_execution_fact_missing"
                    )
                if (
                    external_execution["capability_call_id"]
                    != fence.external_attempt.capability_call_id
                    or int(external_execution["attempt_number"])
                    != fence.external_attempt.attempt_number
                ):
                    raise ResearchOrchestrationPersistenceError(
                        "external_execution_identity_conflict"
                    )
                next_external_state = {
                    "accepted": ExternalExecutionState.SUBMITTED.value,
                    "running": ExternalExecutionState.RUNNING.value,
                    "completed": ExternalExecutionState.COMPLETED.value,
                    "known_rejected": ExternalExecutionState.KNOWN_FAILED.value,
                    "known_failed": ExternalExecutionState.KNOWN_FAILED.value,
                    "not_submitted": ExternalExecutionState.NEVER_SUBMITTED.value,
                    "unknown": ExternalExecutionState.UNKNOWN.value,
                }[outcome]
                cursor.execute(
                    """UPDATE openair_external_execution
                          SET external_state = %s,
                              remote_run_id = %s,
                              failure_code = %s,
                              updated_at = clock_timestamp()
                        WHERE dispatch_id = %s
                          AND task_id = %s
                          AND execution_decision_id = %s
                          AND work_unit_id = %s
                          AND capability_call_id = %s
                           AND attempt_number = %s
                          AND provider_idempotency_key = %s""",
                    (
                        next_external_state,
                        remote_run_id,
                        error_code,
                        fence.dispatch_id,
                        fence.task_id,
                        fence.execution_decision_id,
                        fence.work_unit_id,
                        fence.external_attempt.capability_call_id,
                        fence.external_attempt.attempt_number,
                        fence.idempotency_key,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ResearchOrchestrationPersistenceError(
                        "external_execution_outcome_race"
                    )
                if outcome == "unknown":
                    _ensure_unknown_recovery_obligation(
                        self,
                        cursor,
                        dispatch_id=fence.dispatch_id,
                        error_code=str(error_code),
                        persistence_error=ResearchOrchestrationPersistenceError,
                    )
                elif outcome in {
                    "known_rejected",
                    "known_failed",
                    "not_submitted",
                }:
                    self._enqueue_dispatch_continuation(cursor, fence.dispatch_id)
        return True

    def ensure_unknown_recovery_facts(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        work_unit_id: str,
        provider_id: str,
        adapter: str,
    ) -> dict[str, Any]:
        """Idempotently materialize recovery facts for a legacy unknown Dispatch."""
        self._require_write_enabled()
        self._require_postgres()
        provider_id = str(provider_id or "").strip()
        adapter = str(adapter or "").strip()
        if not provider_id or not adapter:
            raise ValueError("external execution provider identity is required")
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                task, _raw_payload = self._load_locked_task(
                    cursor,
                    task_id=task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                work_unit = next(
                    (
                        row
                        for row in task.work_units
                        if row.work_unit_id == work_unit_id
                    ),
                    None,
                )
                if (
                    work_unit is None
                    or work_unit.status != WorkUnitStatus.EXTERNAL_RESULT_UNKNOWN
                ):
                    raise ExternalRecoveryStateError(
                        "unknown_recovery_work_unit_state_invalid"
                    )
                cursor.execute(
                    """SELECT * FROM openair_dispatch
                        WHERE task_id = %s AND work_unit_id = %s
                        FOR UPDATE""",
                    (task_id, work_unit_id),
                )
                dispatch_row = cursor.fetchone()
                if (
                    dispatch_row is None
                    or dispatch_row["status"]
                    != DispatchStatus.SUBMISSION_UNKNOWN.value
                    or dispatch_row["external_action_started_at"] is None
                    or not str(dispatch_row.get("last_error_code") or "").strip()
                ):
                    raise ExternalRecoveryStateError(
                        "unknown_recovery_dispatch_state_invalid"
                    )
                fence = AuthorizedDispatch(
                    dispatch_id=str(dispatch_row["dispatch_id"]),
                    task_id=task_id,
                    execution_decision_id=str(
                        dispatch_row["execution_decision_id"]
                    ),
                    work_unit_id=work_unit_id,
                    idempotency_key=str(dispatch_row["idempotency_key"]),
                    lease_owner=str(dispatch_row.get("lease_owner") or "recovery_backfill"),
                    attempt_count=int(dispatch_row["attempt_count"]),
                    authorization_task_revision=int(
                        dispatch_row.get("authorization_task_revision") or 0
                    ),
                    work_unit_payload=work_unit.model_dump(
                        mode="json", by_alias=True
                    ),
                    external_attempt=initial_external_attempt_identity(
                        DispatchFence(
                            dispatch_id=str(dispatch_row["dispatch_id"]),
                            task_id=task_id,
                            execution_decision_id=str(
                                dispatch_row["execution_decision_id"]
                            ),
                            work_unit_id=work_unit_id,
                            idempotency_key=str(dispatch_row["idempotency_key"]),
                            lease_owner=str(
                                dispatch_row.get("lease_owner") or "recovery_backfill"
                            ),
                            attempt_count=int(dispatch_row["attempt_count"]),
                        )
                    ),
                    submission_mode=str(
                        dispatch_row.get("submission_mode")
                        or "single_attempt_no_replay"
                    ),
                    external_action_started_at=dispatch_row[
                        "external_action_started_at"
                    ],
                )
                self._start_external_attempt(
                    cursor,
                    fence=fence,
                    work_unit=work_unit,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    provider_id=provider_id,
                    adapter=adapter,
                )
                error_code = str(dispatch_row["last_error_code"])
                evidence_id = _ensure_unknown_recovery_obligation(
                    self,
                    cursor,
                    dispatch_id=fence.dispatch_id,
                    error_code=error_code,
                    persistence_error=ResearchOrchestrationPersistenceError,
                )
                return {
                    "taskId": task_id,
                    "workUnitId": work_unit_id,
                    "dispatchId": fence.dispatch_id,
                    "capabilityCallId": fence.external_attempt.capability_call_id,
                    "evidenceRef": evidence_id,
                    "status": "ready",
                }

    def reload_authorized_dispatch_for_result_persistence(
        self,
        fence: AuthorizedDispatch,
    ) -> AuthorizedDispatch | None:
        """Return the current authorized fence row for canonical result persistence."""
        self._require_postgres()
        self._require_authorized_fence(fence)
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT dispatch_id, task_id, execution_decision_id, work_unit_id,
                              idempotency_key, lease_owner, attempt_count,
                              authorization_task_revision, submission_mode,
                              external_action_started_at, result_storage_type,
                              result_storage_scope, result_storage_ref, status
                         FROM openair_dispatch
                        WHERE dispatch_id = %s AND task_id = %s
                        FOR UPDATE""",
                    (fence.dispatch_id, fence.task_id),
                )
                row = cursor.fetchone()
                if (
                    row is None
                    or row["status"] != DispatchStatus.SUBMISSION_AUTHORIZED.value
                    or row["external_action_started_at"] is None
                    or row["authorization_task_revision"] is None
                ):
                    return None
                if (
                    row["execution_decision_id"] != fence.execution_decision_id
                    or row["work_unit_id"] != fence.work_unit_id
                    or row["idempotency_key"] != fence.idempotency_key
                    or int(row["authorization_task_revision"])
                    != fence.authorization_task_revision
                ):
                    return None
        return AuthorizedDispatch(
            dispatch_id=str(row["dispatch_id"]),
            task_id=str(row["task_id"]),
            execution_decision_id=str(row["execution_decision_id"]),
            work_unit_id=str(row["work_unit_id"]),
            idempotency_key=str(row["idempotency_key"]),
            lease_owner=str(row["lease_owner"] or fence.lease_owner),
            attempt_count=int(row["attempt_count"]),
            authorization_task_revision=int(row["authorization_task_revision"]),
            work_unit_payload=fence.work_unit_payload,
            external_attempt=fence.external_attempt,
            submission_mode=str(row["submission_mode"] or fence.submission_mode),
            external_action_started_at=row["external_action_started_at"],
            result_storage_type=row.get("result_storage_type"),
            result_storage_scope=row.get("result_storage_scope"),
            result_storage_ref=row.get("result_storage_ref"),
            result_recovery_from_unknown=fence.result_recovery_from_unknown,
        )

    def reserve_dispatch_result_storage(
        self,
        fence: AuthorizedDispatch,
        *,
        storage_type: str,
        storage_scope: str | None,
        storage_ref: str,
    ) -> bool:
        """Persist the physical result identity before writing bytes/provider-result recovery."""
        self._require_write_enabled()
        self._require_postgres()
        self._require_authorized_fence(fence)
        if storage_type == "external" and not str(storage_scope or "").strip():
            raise ResearchOrchestrationPersistenceError("dispatch_result_storage_scope_missing")
        if storage_type == "local":
            storage_scope = fence.task_id
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT * FROM openair_dispatch
                         WHERE dispatch_id = %s AND task_id = %s
                         FOR UPDATE""",
                    (fence.dispatch_id, fence.task_id),
                )
                row = cursor.fetchone()
                if row is None or row["external_action_started_at"] is None:
                    return False
                existing_ref = row.get("result_storage_ref")
                if existing_ref is not None:
                    if (
                        row.get("result_storage_type") != storage_type
                        or row.get("result_storage_scope") != storage_scope
                        or existing_ref != storage_ref
                    ):
                        raise ResearchOrchestrationPersistenceError(
                            "dispatch_result_storage_reservation_conflict"
                        )
                    return True
                if not self._matches_authorized_fence(row, fence):
                    return False
                cursor.execute(
                    """UPDATE openair_dispatch
                          SET result_storage_type = %(storage_type)s,
                              result_storage_scope = %(storage_scope)s,
                              result_storage_ref = %(storage_ref)s,
                              updated_at = clock_timestamp()
                        WHERE dispatch_id = %(dispatch_id)s
                          AND task_id = %(task_id)s
                          AND status = 'submission_authorized'
                          AND lease_owner = %(lease_owner)s
                          AND attempt_count = %(attempt_count)s
                          AND authorization_task_revision = %(authorization_task_revision)s
                          AND result_storage_ref IS NULL""",
                    {
                        **fence.__dict__,
                        "storage_type": storage_type,
                        "storage_scope": storage_scope,
                        "storage_ref": storage_ref,
                    },
                )
                return cursor.rowcount == 1

    def record_dispatch_completed_result(
        self,
        fence: AuthorizedDispatch,
        *,
        owner_id: str,
        workspace_id: str,
        result: DispatchSubmissionCompleted,
        artifact_contract: str,
        artifact_schema_digest: str,
        storage_type: str,
        storage_scope: str | None,
        storage_ref: str,
        content_sha256: str,
        artifact_storage_type: str | None = None,
        artifact_storage_scope: str | None = None,
        artifact_storage_ref: str | None = None,
        produced_artifact_type: str | None = None,
        accepted_artifact_content: str | None = None,
        source_identity_acceptance: Mapping[str, Any] | None = None,
        scientific_state_fact_projections: Mapping[str, Any] | None = None,
        scientific_state_projection_declaration: Mapping[str, Any] | None = None,
    ) -> bool:
        """Persist Result facts, settle the Dispatch, and enqueue continuation."""
        self._require_write_enabled()
        self._require_postgres()
        self._require_authorized_fence(fence)
        now = datetime.now(timezone.utc)
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                task, raw_payload = self._load_locked_task(
                    cursor,
                    task_id=fence.task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                cursor.execute(
                    """SELECT * FROM openair_dispatch
                        WHERE dispatch_id = %s AND task_id = %s
                        FOR UPDATE""",
                    (fence.dispatch_id, fence.task_id),
                )
                dispatch_row = cursor.fetchone()
                if dispatch_row is None or dispatch_row["external_action_started_at"] is None:
                    return False
                if not self._matches_authorized_fence(dispatch_row, fence):
                    submitted_replay = (
                        dispatch_row["status"] == DispatchStatus.SUBMITTED.value
                        and int(dispatch_row["attempt_count"]) == fence.attempt_count
                        and dispatch_row["task_id"] == fence.task_id
                        and dispatch_row["execution_decision_id"]
                        == fence.execution_decision_id
                        and dispatch_row["work_unit_id"] == fence.work_unit_id
                        and dispatch_row["idempotency_key"] == fence.idempotency_key
                        and dispatch_row["authorization_task_revision"] is not None
                        and int(dispatch_row["authorization_task_revision"])
                        == fence.authorization_task_revision
                    )
                    if not submitted_replay:
                        return False
                    if (
                        dispatch_row.get("result_storage_type") != storage_type
                        or dispatch_row.get("result_storage_scope") != storage_scope
                        or dispatch_row.get("result_storage_ref") != storage_ref
                        or dispatch_row.get("result_content_sha256") != content_sha256
                    ):
                        raise ResearchOrchestrationPersistenceError(
                            "dispatch_completed_result_identity_conflict"
                        )
                    persisted_result = next(
                        (
                            row
                            for row in task.results
                            if row.result_id == dispatch_result_id(fence)
                        ),
                        None,
                    )
                    if persisted_result is None:
                        return False
                    existing_result_protocol = persisted_result.check_result.get(
                        "resultProtocol"
                    )
                    if existing_result_protocol is not None:
                        try:
                            protocol_compatible = normalize_result_protocol(
                                existing_result_protocol
                            ) == merge_result_protocols(
                                result.result_protocol,
                                warnings=result.warnings,
                            )
                        except ValueError:
                            protocol_compatible = False
                        if not protocol_compatible:
                            raise ResearchOrchestrationPersistenceError(
                                "dispatch_completed_result_identity_conflict"
                            )
                    return False
                result_already_stored = False
                canonical_artifact_already_materialized = False
                if dispatch_row.get("result_storage_ref") is not None:
                    if (
                        dispatch_row.get("result_storage_type") != storage_type
                        or dispatch_row.get("result_storage_scope") != storage_scope
                        or dispatch_row.get("result_storage_ref") != storage_ref
                    ):
                        raise ResearchOrchestrationPersistenceError(
                            "dispatch_completed_result_identity_conflict"
                        )
                    if dispatch_row.get("result_content_sha256") is not None:
                        if dispatch_row.get("result_content_sha256") != content_sha256:
                            raise ResearchOrchestrationPersistenceError(
                                "dispatch_completed_result_identity_conflict"
                            )
                        result_already_stored = True
                        call_id = fence.external_attempt.capability_call_id
                        result_id = dispatch_result_id(fence)
                        persisted_result = next(
                            (
                                row
                                for row in task.results
                                if row.result_id == result_id
                            ),
                            None,
                        )
                        call = next(
                            (
                                row for row in task.capability_calls
                                if row.capability_call_id == call_id
                            ),
                            None,
                        )
                        expected_artifact_ids = list(
                            persisted_result.artifact_refs
                            if persisted_result is not None and persisted_result.artifact_refs
                            else [f"artifact:{fence.dispatch_id}"]
                        )
                        artifacts_by_id = {
                            row.artifact_id: row for row in task.artifacts
                        }
                        materialized_artifacts = [
                            artifacts_by_id[artifact_id]
                            for artifact_id in expected_artifact_ids
                            if artifact_id in artifacts_by_id
                        ]
                        artifact = (
                            materialized_artifacts[0] if materialized_artifacts else None
                        )
                        if persisted_result is not None and (
                            persisted_result.work_unit_id != fence.work_unit_id
                            or persisted_result.capability_call_id != call_id
                            or persisted_result.storage_type != storage_type
                            or persisted_result.storage_scope != storage_scope
                            or persisted_result.storage_ref != storage_ref
                            or persisted_result.content_sha256 != content_sha256
                        ):
                            raise ResearchOrchestrationPersistenceError(
                                "dispatch_completed_result_identity_conflict"
                            )
                        if len(materialized_artifacts) == len(expected_artifact_ids):
                            all_canonical = True
                            for artifact_id in expected_artifact_ids:
                                cursor.execute(
                                    """SELECT EXISTS (
                                       SELECT 1 FROM openair_artifacts
                                        WHERE artifact_id = %s
                                          AND owner_id = %s
                                          AND workspace_id = %s
                                   ) AS artifact_exists,
                                   EXISTS (
                                       SELECT 1 FROM openair_artifact_lineage
                                        WHERE task_id = %s AND artifact_id = %s
                                   ) AS lineage_exists""",
                                    (
                                        artifact_id,
                                        owner_id,
                                        workspace_id,
                                        fence.task_id,
                                        artifact_id,
                                    ),
                                )
                                canonical = cursor.fetchone()
                                if not (
                                    canonical["artifact_exists"]
                                    and canonical["lineage_exists"]
                                ):
                                    all_canonical = False
                                    break
                            canonical_artifact_already_materialized = all_canonical
                            if (
                                call is not None
                                and call.status.value == "succeeded"
                                and canonical_artifact_already_materialized
                            ):
                                if not call.output_artifact_refs:
                                    self._settle_completed_dispatch_and_enqueue_continuation(
                                        cursor,
                                        fence,
                                    )
                                    return True
                        # A continuation failure may project an already-persisted
                        # provider result as external_result_unknown before the
                        # worker can finalize its Dispatch.  That result and its
                        # Artifact are the durable authority; do not append the
                        # deterministic resultId again during recovery.
                        if (
                            persisted_result is not None
                            and call is not None
                            and len(materialized_artifacts) == len(expected_artifact_ids)
                            and call.status.value == "external_result_unknown"
                        ):
                            work = next(
                                (
                                    row
                                    for row in task.work_units
                                    if row.work_unit_id == fence.work_unit_id
                                ),
                                None,
                            )
                            if work is None or work.status.value != "external_result_unknown":
                                raise ResearchOrchestrationPersistenceError(
                                    "dispatch_completed_result_recovery_state_conflict"
                                )
                            cursor.execute(
                                """SELECT EXISTS (
                                       SELECT 1 FROM openair_artifacts
                                        WHERE artifact_id = %s
                                          AND owner_id = %s
                                          AND workspace_id = %s
                                   ) AS artifact_exists,
                                   EXISTS (
                                       SELECT 1 FROM openair_artifact_lineage
                                        WHERE task_id = %s AND artifact_id = %s
                                   ) AS lineage_exists""",
                                (artifact_id, owner_id, workspace_id, fence.task_id, artifact_id),
                            )
                            canonical = cursor.fetchone()
                            if canonical["artifact_exists"] and canonical["lineage_exists"]:
                                payload = task.model_dump(mode="json", by_alias=True)
                                persisted_call = next(
                                    row
                                    for row in payload["capabilityCalls"]
                                    if row["capabilityCallId"] == call_id
                                )
                                persisted_work = next(
                                    row
                                    for row in payload["workUnits"]
                                    if row["workUnitId"] == fence.work_unit_id
                                )
                                persisted_call.update(
                                    {
                                        "status": "succeeded",
                                        "outputArtifactRefs": list(persisted_result.artifact_refs),
                                        "finishedAt": now.isoformat(),
                                    }
                                )
                                persisted_work.update(
                                    {
                                        "status": WorkUnitStatus.COMPLETED.value,
                                        "terminalReasonCode": None,
                                        "finishedAt": now.isoformat(),
                                    }
                                )
                                self._write_locked_task(
                                    cursor,
                                    raw_payload,
                                    ResearchTaskV12.model_validate(payload),
                                )
                                self._settle_completed_dispatch_and_enqueue_continuation(
                                    cursor,
                                    fence,
                                )
                                return True
                task, _execution_policy = self._apply_pending_execution_policy_at_boundary(
                    cursor,
                    task=task,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    changed_by=owner_id,
                )
                previous_artifact_states = {
                    row.artifact_id: row.artifact_state
                    for row in task.artifacts
                }
                updated, catalog_artifact, pending_dispatches, agent_message = self._task_with_completed_result(
                    task,
                    fence=fence,
                    result=result,
                    artifact_contract=artifact_contract,
                    artifact_schema_digest=artifact_schema_digest,
                    content_sha256=content_sha256,
                    storage_type=storage_type,
                    storage_scope=storage_scope,
                    storage_ref=storage_ref,
                    now=now,
                    produced_artifact_type=produced_artifact_type,
                    accepted_artifact_content=accepted_artifact_content,
                    source_identity_acceptance=source_identity_acceptance,
                    scientific_state_fact_projections=(
                        scientific_state_fact_projections or {}
                    ),
                    scientific_state_projection_declaration=(
                        scientific_state_projection_declaration
                    ),
                )
                if canonical_artifact_already_materialized:
                    # The formal Result and Artifact are already durable.  This
                    # pass only settles their interrupted lifecycle state.
                    catalog_artifact = None
                catalog_entries: list[dict[str, Any]] = []
                if isinstance(catalog_artifact, list):
                    catalog_entries = catalog_artifact
                elif catalog_artifact is not None:
                    catalog_entries = [catalog_artifact]
                for catalog_entry in catalog_entries:
                    entry_storage_type = artifact_storage_type
                    entry_storage_scope = artifact_storage_scope
                    entry_storage_ref = artifact_storage_ref
                    suffix = catalog_entry.get("storage_ref_suffix")
                    if isinstance(suffix, str) and suffix:
                        entry_storage_ref = (
                            f"durable/research-dispatch/{fence.dispatch_id}/{suffix}"
                        )
                    workspace_file = catalog_entry.get("workspaceFile")
                    if isinstance(workspace_file, Mapping):
                        # Declared skill output files were stored straight from
                        # the shared workspace volume by the aggregate builder;
                        # the catalog row only mirrors that storage truth.
                        entry_storage_type = str(workspace_file["storage_type"])
                        entry_storage_scope = workspace_file.get("storage_scope")
                        entry_storage_ref = str(workspace_file["storage_ref"])
                        persisted_content = None
                        persisted_sha = str(workspace_file["content_sha256"])
                        persisted_size = int(workspace_file["content_size"])
                        content_override = None
                        content_sha_override = None
                        mime_override = None
                    else:
                        if entry_storage_type is None or entry_storage_ref is None:
                            raise ResearchOrchestrationPersistenceError(
                                "producer_artifact_storage_missing"
                            )
                        content_override = catalog_entry.get("contentOverride")
                        content_sha_override = catalog_entry.get("contentSha256Override")
                        mime_override = catalog_entry.get("mimeTypeOverride")
                        if isinstance(content_override, str) and content_override:
                            override_blob = content_override.encode("utf-8")
                            override_sha = (
                                str(content_sha_override)
                                if isinstance(content_sha_override, str) and content_sha_override
                                else hashlib.sha256(override_blob).hexdigest()
                            )
                            try:
                                rewritten = artifact_write_service.store_durable_bytes(
                                    fence.task_id,
                                    entry_storage_ref,
                                    override_blob,
                                    str(
                                        mime_override
                                        or catalog_entry.get("mime_type")
                                        or "text/markdown"
                                    ),
                                    storage_scope=entry_storage_scope,
                                )
                            except TypeError:
                                rewritten = artifact_write_service.store_durable_bytes(
                                    fence.task_id,
                                    entry_storage_ref,
                                    override_blob,
                                    str(
                                        mime_override
                                        or catalog_entry.get("mime_type")
                                        or "text/markdown"
                                    ),
                                )
                            entry_storage_type = rewritten.storage_type
                            entry_storage_scope = getattr(
                                rewritten, "storage_scope", entry_storage_scope
                            )
                            entry_storage_ref = rewritten.storage_ref
                            persisted_content = content_override
                            persisted_sha = override_sha
                            persisted_size = len(override_blob)
                        else:
                            persisted_content = result.content
                            persisted_sha = content_sha256
                            persisted_size = len(result.content.encode("utf-8"))
                    versioned_artifact = ensure_initial_version_truth({
                        "id": catalog_entry["artifact_id"],
                        "sourceTaskId": fence.task_id,
                        "createdBy": owner_id,
                        "createdAt": now.isoformat(),
                        "storageType": entry_storage_type,
                        "storageScope": entry_storage_scope,
                        "path": entry_storage_ref if entry_storage_type == "local" else None,
                        "externalKey": (
                            entry_storage_ref if entry_storage_type == "external" else None
                        ),
                        "contentSha256": persisted_sha,
                        "contentSize": persisted_size,
                    })
                    artifact_metadata = dict(catalog_entry["metadata"])
                    artifact_metadata["currentVersionId"] = versioned_artifact.get(
                        "currentVersionId"
                    )
                    artifact_metadata["versions"] = versioned_artifact.get("versions") or []
                    cursor.execute(
                        """INSERT INTO openair_artifacts (
                               artifact_id, owner_id, workspace_id, created_by, kind, origin,
                               title, format, mime_type, storage_type, storage_scope, storage_ref, content_size,
                               content_sha256, source_task_id, source_task_title, source_capability_call_id,
                               related_skill_id,
                               metadata, created_at, updated_at, source_execution_run_id, source_mode
                           ) VALUES (
                               %(artifact_id)s, %(owner_id)s, %(workspace_id)s, %(owner_id)s,
                               'execution_output', 'generated', %(title)s, %(format)s, %(mime_type)s,
                               %(storage_type)s, %(storage_scope)s, %(storage_ref)s, %(content_size)s, %(content_sha256)s,
                               %(task_id)s, %(source_task_title)s, %(capability_call_id)s,
                               %(related_skill_id)s,
                               %(metadata)s::jsonb,
                               %(now)s, %(now)s, NULL, 'live'
                           )""",
                        {
                            **{
                                key: value
                                for key, value in catalog_entry.items()
                                if key
                                not in {
                                    "contentOverride",
                                    "contentSha256Override",
                                    "mimeTypeOverride",
                                    "storage_ref_suffix",
                                    "workspaceFile",
                                }
                            },
                            "owner_id": owner_id,
                            "workspace_id": workspace_id,
                            "related_skill_id": catalog_entry.get("related_skill_id"),
                            "storage_type": entry_storage_type,
                            "storage_scope": entry_storage_scope,
                            "storage_ref": entry_storage_ref,
                            "content_size": persisted_size,
                            "content_sha256": persisted_sha,
                            "task_id": fence.task_id,
                            "source_task_title": (
                                str(raw_payload.get("title") or "").strip() or None
                            ),
                            "now": now,
                            "metadata": json.dumps(artifact_metadata, ensure_ascii=False),
                        },
                    )
                    _ = persisted_content  # content is durable via storage_ref
                reviewed_artifacts = [
                    row
                    for row in updated.artifacts
                    if previous_artifact_states.get(row.artifact_id)
                    == ArtifactState.NEEDS_REVIEW
                    and row.artifact_state != ArtifactState.NEEDS_REVIEW
                ]
                for reviewed in reviewed_artifacts:
                    # Catalog metadata is a projection of the canonical Task artifact.
                    # Mirror every terminal review transition, not only approval.
                    cursor.execute(
                        """UPDATE openair_artifacts
                              SET metadata = jsonb_set(
                                      jsonb_set(
                                          COALESCE(metadata, '{}'::jsonb),
                                          '{artifactState}',
                                          %(artifact_state)s::jsonb,
                                          true
                                      ),
                                      '{validatorResult}',
                                      %(validator_result)s::jsonb,
                                      true
                                  ),
                                  updated_at = clock_timestamp()
                            WHERE artifact_id = %(artifact_id)s
                              AND owner_id = %(owner_id)s
                              AND workspace_id = %(workspace_id)s
                              AND source_task_id = %(task_id)s""",
                        {
                            "artifact_id": reviewed.artifact_id,
                            "owner_id": owner_id,
                            "workspace_id": workspace_id,
                            "task_id": fence.task_id,
                            "artifact_state": json.dumps(reviewed.artifact_state.value),
                            "validator_result": json.dumps(
                                reviewed.validator_result,
                                ensure_ascii=False,
                            ),
                        },
                    )
                    if cursor.rowcount != 1:
                        raise ResearchOrchestrationPersistenceError(
                            "reviewed_artifact_catalog_state_missing"
                        )
                if agent_message is not None:
                    TaskMessageRepository().append(
                        cursor,
                        task_id=fence.task_id,
                        owner_id=str(raw_payload.get("ownerId") or ""),
                        workspace_id=str(raw_payload.get("workspaceId") or ""),
                        role=agent_message["role"],
                        text=agent_message["text"],
                        message_uid=agent_message["id"],
                        runtime=agent_message.get("runtime"),
                        message_type=agent_message.get("messageType", "research_dispatch_result"),
                        capability_call_id=agent_message.get("capabilityCallId"),
                        metadata={
                            key: agent_message[key]
                            for key in (
                                "authorLabel", "artifactRefs", "confirmationRequests",
                                "userFacingNarrative", "workUnitId",
                            )
                            if key in agent_message
                        },
                    )
                # A stored Result is an immutable delivery fact.  A later recovery
                # may need to reconcile a torn lifecycle transition, but once the
                # Task already contains the identical Result/Artifact/Call facts it
                # must be a true no-op.  Bumping the Task revision in that state
                # makes the following continuation look stale and turns a finished
                # delivery into a pointless recovery loop.
                if updated != task:
                    self._write_locked_task(cursor, raw_payload, updated, payload_patch=None)
                for record in pending_dispatches:
                    self._insert_pending_dispatch(cursor, record)
                if not result_already_stored:
                    cursor.execute(
                        """UPDATE openair_dispatch
                          SET remote_run_id = NULL,
                              result_storage_type = %(storage_type)s,
                              result_storage_scope = %(storage_scope)s,
                              result_storage_ref = %(storage_ref)s,
                              result_content_sha256 = %(content_sha256)s,
                              last_error_code = NULL, updated_at = clock_timestamp()
                        WHERE dispatch_id = %(dispatch_id)s
                          AND status = 'submission_authorized'
                          AND lease_owner = %(lease_owner)s
                          AND attempt_count = %(attempt_count)s
                          AND authorization_task_revision = %(authorization_task_revision)s
                          AND result_storage_ref = %(storage_ref)s
                          AND result_storage_scope IS NOT DISTINCT FROM %(storage_scope)s
                          AND result_content_sha256 IS NULL""",
                        {
                            **fence.__dict__,
                            "storage_type": storage_type,
                            "storage_scope": storage_scope,
                            "storage_ref": storage_ref,
                            "content_sha256": content_sha256,
                        },
                    )
                    if cursor.rowcount != 1:
                        raise ResearchOrchestrationPersistenceError("dispatch_completed_result_race")
                capability_call_id = fence.external_attempt.capability_call_id
                self._settle_completed_dispatch_and_enqueue_continuation(
                    cursor, fence,
                    rearm_completed=fence.external_attempt.attempt_number > 1,
                )
                cursor.execute(
                    """UPDATE openair_external_execution_checkpoints
                          SET phase = 'canonical_completed',
                              canonical_completed_at = COALESCE(
                                  canonical_completed_at, clock_timestamp()
                              ),
                              updated_at = clock_timestamp()
                        WHERE capability_call_id = %s
                          AND phase = 'response_received'
                          AND response_kind = 'dispatch_completed'
                       RETURNING attempt_id""",
                    (capability_call_id,),
                )
                if cursor.fetchone() is None:
                    cursor.execute(
                        """SELECT 1 FROM openair_external_execution_checkpoints
                            WHERE capability_call_id = %s""",
                        (capability_call_id,),
                    )
                    if cursor.fetchone() is not None:
                        raise ResearchOrchestrationPersistenceError(
                            "external_checkpoint_canonical_transition_conflict"
                        )
        return True

    @staticmethod
    def _settle_completed_dispatch_and_enqueue_continuation(
        cursor: psycopg.Cursor[Any],
        fence: AuthorizedDispatch,
        *,
        rearm_completed: bool = False,
    ) -> None:
        cursor.execute(
            '''UPDATE openair_dispatch
                  SET status = 'submitted', lease_owner = NULL, lease_expires_at = NULL,
                      next_attempt_at = NULL, last_error_code = NULL,
                      updated_at = clock_timestamp()
                WHERE dispatch_id = %(dispatch_id)s AND task_id = %(task_id)s
                  AND status = 'submission_authorized'
                  AND lease_owner = %(lease_owner)s AND attempt_count = %(attempt_count)s
                  AND authorization_task_revision = %(authorization_task_revision)s
                  AND result_storage_ref IS NOT NULL''',
            fence.__dict__,
        )
        if cursor.rowcount != 1:
            raise ResearchOrchestrationPersistenceError(
                "dispatch_completed_settlement_race"
            )
        cursor.execute(
            '''UPDATE openair_external_execution x
                  SET external_state = 'completed', receipt = d.result_storage_ref,
                      remote_run_id = d.result_storage_ref, failure_code = NULL,
                      updated_at = GREATEST(clock_timestamp(), x.created_at)
                 FROM openair_dispatch d
                WHERE x.dispatch_id = d.dispatch_id AND d.dispatch_id = %(dispatch_id)s
                  AND x.task_id = d.task_id
                  AND x.execution_decision_id = %(execution_decision_id)s
                  AND x.work_unit_id = %(work_unit_id)s
                  AND x.capability_call_id = %(capability_call_id)s
                  AND d.result_storage_ref IS NOT NULL''',
            {
                **fence.__dict__,
                "capability_call_id": fence.external_attempt.capability_call_id,
            },
        )
        if cursor.rowcount != 1:
            raise ResearchOrchestrationPersistenceError(
                "external_execution_completion_missing"
            )
        _enqueue_dispatch_continuation(
            cursor, fence.dispatch_id, rearm_completed=rearm_completed,
        )

    @staticmethod
    def _enqueue_dispatch_continuation(
        cursor: psycopg.Cursor[Any], dispatch_id: str,
    ) -> None:
        _enqueue_dispatch_continuation(cursor, dispatch_id)

    def prepare_execution_continuation(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        completed_execution_decision_id: str,
        changed_by: str,
    ) -> ExecutionContinuationDirective:
        """Resolve the latest execution policy at a safe completed-Decision boundary."""
        self._require_write_enabled()
        self._require_postgres()
        from app.modules.repeatable_definition_reentry import (
            reactivate_repeatable_definition_instances,
        )
        from app.modules.research_execution_decision_service import (
            materialize_execution_continuation_pause,
            plan_execution_continuation,
            reconcile_closed_successful_result_progress,
            reconcile_persisted_accepted_revision_progress,
            reconcile_task_lifecycle_from_runtime,
        )

        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                task, raw_payload = self._load_locked_task(
                    cursor,
                    task_id=task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                # Repeatable re-entry is Definition-owned authority to reopen
                # a completed confirmed task when new facts arrived. Apply it
                # BEFORE the terminal early-return, otherwise a task that just
                # completed its last scheduled instance can never re-arm its
                # repeatable successors (chain-2 S5 close → S6 reactivation).
                if task.status == TaskStatus.COMPLETED:
                    reactivated = reactivate_repeatable_definition_instances(
                        task,
                        completed_execution_decision_id=completed_execution_decision_id,
                        occurred_at=datetime.now(UTC),
                    )
                    if reactivated != task:
                        self._write_locked_task(cursor, raw_payload, reactivated)
                        task, raw_payload = self._load_locked_task(
                            cursor,
                            task_id=task_id,
                            owner_id=owner_id,
                            workspace_id=workspace_id,
                        )
                if task.status in {
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                }:
                    return ExecutionContinuationDirective(
                        action="terminal",
                        expected_task_revision=task.revision,
                        next_instance_id=None,
                        selection_request_id=None,
                        reason_code=f"task_{task.status.value}",
                    )
                accepted = reconcile_closed_successful_result_progress(
                    task,
                    completed_execution_decision_id=completed_execution_decision_id,
                    occurred_at=datetime.now(timezone.utc),
                    snapshot=self.load_registry_snapshot(task.registry_snapshot_ref.content_hash),
                )
                if accepted != task:
                    self._write_locked_task(cursor, raw_payload, accepted)
                    task, raw_payload = self._load_locked_task(
                        cursor, task_id=task_id, owner_id=owner_id, workspace_id=workspace_id,
                    )
                selection_request_id = f"continuation:{completed_execution_decision_id}"
                if self._execution_stop_active(raw_payload):
                    return ExecutionContinuationDirective(
                        action="stopped",
                        expected_task_revision=task.revision,
                        next_instance_id=None,
                        selection_request_id=None,
                        reason_code="user_stop_requested",
                    )
                cursor.execute(
                    """SELECT execution_decision_id
                         FROM openair_execution_selection_request
                        WHERE task_id = %s
                          AND owner_id = %s
                          AND workspace_id = %s
                          AND selection_request_id = %s""",
                    (task_id, owner_id, workspace_id, selection_request_id),
                )
                if cursor.fetchone() is not None:
                    return ExecutionContinuationDirective(
                        action="already_started",
                        expected_task_revision=task.revision,
                        next_instance_id=None,
                        selection_request_id=selection_request_id,
                        reason_code=None,
                    )
                projected, policy = self._apply_pending_execution_policy_at_boundary(
                    cursor,
                    task=task,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    changed_by=changed_by,
                )
                boundary_time = datetime.now(timezone.utc)
                projected = reconcile_persisted_accepted_revision_progress(
                    projected,
                    completed_execution_decision_id=completed_execution_decision_id,
                    occurred_at=boundary_time,
                )
                projected = reactivate_repeatable_definition_instances(
                    projected,
                    completed_execution_decision_id=completed_execution_decision_id,
                    occurred_at=boundary_time,
                )
                # The completed Decision may have just satisfied the dependency
                # facts for the next planned instance. This reconciliation starts
                # with canonical dependency promotion (PLANNED -> READY), and must
                # happen before continuation planning at the completed-Decision
                # boundary.
                projected = self._reconcile_task_lifecycle_with_frozen_snapshot(
                    projected,
                    occurred_at=boundary_time,
                )
                projected = self._preserve_live_dispatch_work_units(
                    cursor,
                    before=task,
                    after=projected,
                )
                plan = plan_execution_continuation(
                    projected,
                    completed_execution_decision_id=completed_execution_decision_id,
                    execution_mode=TaskExecutionMode(policy["current_mode"]),
                )
                if plan.action == "pause":
                    projected = materialize_execution_continuation_pause(
                        projected,
                        reason_code=plan.reason_code,
                        next_instance_id=plan.next_instance_id,
                        occurred_at=boundary_time,
                    )
                    projected = self._reconcile_task_lifecycle_with_frozen_snapshot(
                        projected,
                        occurred_at=boundary_time,
                    )
                    projected = self._preserve_live_dispatch_work_units(
                        cursor,
                        before=task,
                        after=projected,
                    )
                changed = (
                    projected.model_dump(mode="json", by_alias=True)
                    != task.model_dump(mode="json", by_alias=True)
                )
                expected_revision = task.revision
                if changed:
                    self._write_locked_task(cursor, raw_payload, projected)
                    expected_revision += 1
                if plan.action != "start":
                    selection_request_id = None
                return ExecutionContinuationDirective(
                    action=plan.action,
                    expected_task_revision=expected_revision,
                    next_instance_id=plan.next_instance_id,
                    selection_request_id=selection_request_id,
                    reason_code=plan.reason_code,
                )

    def record_execution_continuation_blocked(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int,
        next_instance_id: str,
        reason_code: str,
    ) -> int:
        """Persist a continuation blocker using the canonical ownership boundary."""
        self._require_write_enabled()
        self._require_postgres()
        self._require_expected_revision(expected_task_revision)
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                task, raw_payload = self._load_locked_task(
                    cursor,
                    task_id=task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                if task.revision != expected_task_revision:
                    raise TaskRevisionConflictError("task_revision_conflict")
                if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                    return task.revision
                payload = task.model_dump(mode="json", by_alias=True)
                runtime = next(
                    (
                        row
                        for row in payload["substageRuntimeStates"]
                        if row["substageInstanceId"] == next_instance_id
                    ),
                    None,
                )
                if runtime is None or runtime["status"] != "ready":
                    return task.revision
                runtime["status"] = runtime_blocker_status(reason_code)
                runtime["statusReasonCode"] = reason_code
                runtime["activeExecutionDecisionId"] = None
                updated = self._reconcile_task_lifecycle_with_frozen_snapshot(
                    ResearchTaskV12.model_validate(payload),
                    occurred_at=datetime.now(timezone.utc),
                )
                if updated == task:
                    return task.revision
                self._write_locked_task(cursor, raw_payload, updated)
                return task.revision + 1

    def finalize_dispatch_completed_result(
        self,
        fence: AuthorizedDispatch,
        *,
        owner_id: str,
        workspace_id: str,
    ) -> bool:
        """Close a result-persisted Dispatch only after continuation is handled."""
        self._require_write_enabled()
        self._require_postgres()
        self._require_authorized_fence(fence)
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                self._load_locked_task(
                    cursor,
                    task_id=fence.task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                cursor.execute(
                    """UPDATE openair_dispatch
                          SET status = 'submitted', updated_at = clock_timestamp()
                        WHERE dispatch_id = %(dispatch_id)s
                          AND task_id = %(task_id)s
                          AND execution_decision_id = %(execution_decision_id)s
                          AND work_unit_id = %(work_unit_id)s
                          AND idempotency_key = %(idempotency_key)s
                          AND status = 'submission_authorized'
                          AND lease_owner = %(lease_owner)s
                          AND attempt_count = %(attempt_count)s
                          AND authorization_task_revision = %(authorization_task_revision)s
                          AND result_storage_ref IS NOT NULL""",
                    fence.__dict__,
                )
                if cursor.rowcount != 1:
                    return False
                capability_call_id = fence.external_attempt.capability_call_id
                cursor.execute(
                    """UPDATE openair_external_execution x
                          SET external_state = 'completed',
                              receipt = d.result_storage_ref,
                              remote_run_id = d.result_storage_ref,
                              failure_code = NULL,
                              updated_at = clock_timestamp()
                         FROM openair_dispatch d
                        WHERE x.dispatch_id = d.dispatch_id
                          AND d.dispatch_id = %(dispatch_id)s
                          AND d.task_id = %(task_id)s
                          AND x.task_id = d.task_id
                          AND x.execution_decision_id = %(execution_decision_id)s
                          AND x.work_unit_id = %(work_unit_id)s
                          AND x.capability_call_id = %(capability_call_id)s
                          AND d.result_storage_ref IS NOT NULL""",
                    {
                        **fence.__dict__,
                        "capability_call_id": capability_call_id,
                    },
                )
                if cursor.rowcount != 1:
                    raise ResearchOrchestrationPersistenceError(
                        "external_execution_completion_missing"
                    )
                return True

    @staticmethod
    def _insert_pending_dispatch(cursor: psycopg.Cursor[Any], record: DispatchRecord) -> None:
        record.assert_pending_insert()
        cursor.execute(
            """INSERT INTO openair_dispatch (
                   dispatch_id, task_id, execution_decision_id, work_unit_id,
                   idempotency_key, execution_namespace, status, attempt_count, lease_owner,
                   lease_expires_at, authorization_task_revision, remote_run_id,
                   next_attempt_at, last_error_code, created_at, updated_at
               ) VALUES (
                   %(dispatch_id)s, %(task_id)s, %(execution_decision_id)s,
                   %(work_unit_id)s, %(idempotency_key)s, %(execution_namespace)s, %(status)s,
                   %(attempt_count)s, %(lease_owner)s, %(lease_expires_at)s,
                   %(authorization_task_revision)s, %(remote_run_id)s,
                   %(next_attempt_at)s, %(last_error_code)s,
                   clock_timestamp(), clock_timestamp()
               )""",
            record.model_dump(mode="python"),
        )

    @staticmethod
    def _producer_completion_authority(
        decision: Mapping[str, Any],
        assignment: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        from app.modules.research_execution_decision_service import _completion_authority_candidate

        candidate = _completion_authority_candidate(decision, assignment)
        snapshot_ref = decision.get("registrySnapshotRef") or {}
        content_hash = str(snapshot_ref.get("contentHash") or "").strip()
        if candidate is None or not content_hash:
            return None
        return {
            "candidateEvaluationId": candidate["candidateEvaluationId"],
            "registrySnapshotContentHash": content_hash,
            "executorRef": dict(assignment.get("executorRef") or {}),
            "sourceBindingRefs": [dict(row) for row in assignment.get("sourceBindingRefs") or []],
        }

    def _declared_skill_file_artifacts(
        self,
        *,
        fence: AuthorizedDispatch,
        work: Mapping[str, Any],
        assignment: Mapping[str, Any],
        call_id: str,
        result_id: str,
        existing_artifacts_by_id: Mapping[str, Mapping[str, Any]],
        requires_any_review: bool,
        artifact_contract: str,
        produced_artifact_type: str | None,
        expected_types: list[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        """Register skill-declared workspace files as supplementary Artifacts.

        Mirrors the legacy durable-run hook for the orchestration v1 dispatch
        path: files matched by ``runtime_contract.artifact_files`` under the
        work unit's workspace dir become platform artifacts (markdown/cif/png/
        svg/csv) rendered by the workbench.  Artifact ids are deterministic
        per (dispatch, rel path) so replays recompute identical refs; a file
        already materialized in the Task payload is not re-registered.  Every
        failure degrades to "no declared files" — the formal Result must
        never fail because an optional supplement could not be read or
        stored.
        """
        executor = assignment.get("executorRef")
        if not isinstance(executor, Mapping) or str(executor.get("executorType") or "").strip() != "skill":
            return [], [], []
        skill_id = str(executor.get("executorId") or "").strip()
        if not skill_id:
            return [], [], []
        ws_root = str(os.environ.get("OPENAIR_WORKSPACE_ROOT") or "").strip()
        if not ws_root:
            return [], [], []
        try:
            from app.execution.declared_skill_files import (
                collect_declared_files,
                declared_file_segment,
                read_declared_file_bytes,
            )
            from app.execution.durable_skill_runtime import resolve_registered_durable_skill

            skill = resolve_registered_durable_skill(skill_id)
            source_contract = skill.get("sourceContract") if isinstance(skill, dict) else None
            contract = source_contract.get("runtime_contract") if isinstance(source_contract, dict) else None
            patterns = contract.get("artifact_files") if isinstance(contract, dict) else None
            files = collect_declared_files(ws_root, fence.task_id, fence.work_unit_id, patterns)
        except Exception:  # noqa: BLE001 — best-effort supplement, never block the Result
            logger.warning(
                "declared skill file collection failed (skillId=%s taskId=%s workUnitId=%s)",
                skill_id, fence.task_id, fence.work_unit_id, exc_info=True,
            )
            return [], [], []
        if not files:
            return [], [], []
        # Author the catalog title with the registry's Chinese display name so
        # the workbench footer shows a readable label (researchArtifactTitle
        # falls back to the generic 研究成果 for pure-ASCII titles).
        display_name = str(skill.get("displayName") or "").strip() if isinstance(skill, dict) else ""
        producer_label = display_name or skill_id
        base = Path(ws_root) / declared_file_segment(fence.task_id) / declared_file_segment(fence.work_unit_id)
        type_base = str(
            produced_artifact_type
            or (expected_types[0] if expected_types else "")
            or "execution_output"
        ).strip() or "execution_output"
        artifact_state = "needs_review" if requires_any_review else "usable"
        registered: dict[str, str] = {}
        rows: list[dict[str, Any]] = []
        entries: list[dict[str, Any]] = []
        refs: list[str] = []
        for declared in files:
            # Deterministic identity keeps the refs-only-grow replay invariant
            # (``refs_compatible`` subset check) intact across recovery passes.
            artifact_id = f"artifact:{fence.dispatch_id}:file:{declared_file_segment(declared.rel)}"
            refs.append(artifact_id)
            if artifact_id in existing_artifacts_by_id:
                continue
            try:
                content = read_declared_file_bytes(
                    declared, base=base, task_id=fence.task_id, registered=registered
                )
                if not content:
                    continue
                stored = artifact_write_service.store_durable_bytes(
                    fence.task_id,
                    f"durable/research-dispatch/{fence.dispatch_id}/files/{declared.rel}",
                    content,
                    declared.mime_type,
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "declared skill file persist skipped (skillId=%s file=%s)",
                    skill_id, declared.rel, exc_info=True,
                )
                continue
            digest = hashlib.sha256(content).hexdigest()
            registered[declared.rel] = artifact_id
            declared_type = f"{type_base}.declared_file"
            # artifactSchemaDigest is required by _read_verified_execution_artifacts
            # for any artifact that a downstream binding may consume as input; for
            # declared files the content digest is the integrity anchor (there is
            # no frozen JSON schema behind a raw file artifact).
            validator_result = {
                "status": "passed",
                "artifactContract": artifact_contract,
                "artifactSchemaDigest": digest,
                "declaredFile": declared.rel,
                "contentSha256": digest,
            }
            rows.append(
                {
                    "artifactId": artifact_id,
                    "workUnitId": work["workUnitId"],
                    "substageInstanceId": work["substageInstanceId"],
                    "sourceCapabilityCallId": call_id,
                    "artifactType": declared_type,
                    "artifactState": artifact_state,
                    "version": 1,
                    "sourceRefs": [],
                    "validatorResult": validator_result,
                    "lineageKind": "capability_call_output",
                    "sourceResultId": result_id,
                }
            )
            entries.append(
                {
                    "artifact_id": artifact_id,
                    "title": f"{producer_label} · {declared.rel.rsplit('/', 1)[-1]}",
                    "format": declared.format,
                    "mime_type": declared.mime_type,
                    "capability_call_id": call_id,
                    "related_skill_id": skill_id,
                    "metadata": {
                        "taskId": fence.task_id,
                        "workUnitId": fence.work_unit_id,
                        "resultId": result_id,
                        "artifactState": artifact_state,
                        "artifactType": declared_type,
                        "declaredFile": declared.rel,
                        "relatedSkillId": skill_id,
                        "validatorResult": validator_result,
                    },
                    "workspaceFile": {
                        "storage_type": stored.storage_type,
                        "storage_scope": getattr(stored, "storage_scope", None),
                        "storage_ref": stored.storage_ref,
                        "content_sha256": digest,
                        "content_size": len(content),
                    },
                }
            )
        return rows, entries, refs

    def _task_with_completed_result(
        self,
        task: ResearchTaskV12,
        *,
        fence: AuthorizedDispatch,
        result: DispatchSubmissionCompleted,
        artifact_contract: str,
        artifact_schema_digest: str,
        content_sha256: str,
        storage_type: str,
        storage_scope: str | None,
        storage_ref: str,
        now: datetime,
        produced_artifact_type: str | None = None,
        accepted_artifact_content: str | None = None,
        source_identity_acceptance: Mapping[str, Any] | None = None,
        scientific_state_fact_projections: Mapping[str, Any] | None = None,
        scientific_state_projection_declaration: Mapping[str, Any] | None = None,
    ) -> tuple[
        ResearchTaskV12,
        dict[str, Any] | None,
        tuple[DispatchRecord, ...],
        dict[str, Any] | None,
    ]:
        payload = task.model_dump(mode="json", by_alias=True)
        work = next(row for row in payload["workUnits"] if row["workUnitId"] == fence.work_unit_id)
        decision = next(
            row for row in payload["executionDecisions"]
            if row["executionDecisionId"] == fence.execution_decision_id
        )
        assignment = next(
            row for row in decision["assignments"]
            if row["assignmentId"] == work["assignmentId"]
        )
        call_id = fence.external_attempt.capability_call_id
        result_id = dispatch_result_id(fence)
        existing_result = next(
            (row for row in payload.get("results", []) if row.get("resultId") == result_id),
            None,
        )
        artifact_source_content = (
            accepted_artifact_content
            if accepted_artifact_content is not None
            else result.content
        )
        is_reviewer = assignment["role"] == AssignmentRole.REVIEWER.value
        is_progress = decision.get("purpose") == "progress"
        produced_artifact_type = str(produced_artifact_type or "").strip() or None
        expected_types = list(
            work.get("expectedArtifactTypes") or assignment.get("expectedArtifactTypes") or []
        )
        requires_completion_artifact = (
            not is_reviewer
            and decision.get("purpose") == "completion"
            and bool(expected_types)
        )
        completion_policy = decision.get("completionPolicy")
        reviewer_assignment_ids = (
            set(completion_policy.get("reviewerAssignmentIds") or [])
            if isinstance(completion_policy, dict)
            else set()
        )
        reviewed_assignment_ids = {
            dependency_id
            for candidate_assignment in decision.get("assignments") or []
            if candidate_assignment.get("assignmentId") in reviewer_assignment_ids
            for dependency_id in candidate_assignment.get("dependencyAssignmentIds") or []
        }
        reviewer_work_materialized = any(
            candidate_work.get("executionDecisionId") == fence.execution_decision_id
            and candidate_work.get("assignmentId") in reviewer_assignment_ids
            for candidate_work in payload.get("workUnits", [])
        )
        pre_fix_revision_intermediate_result = bool(
            not is_reviewer
            and decision.get("purpose") == "completion"
            and not expected_types
            and produced_artifact_type is not None
            and assignment.get("executorRef", {}).get("executorType") == "skill"
            and decision.get("revisionReasonCode") == "research_revision"
            and str(decision.get("supersedesExecutionDecisionId") or "").strip()
            and not (
                (decision.get("resultAssemblyPolicy") or {}).get("finalArtifactRequirements")
            )
            and not reviewer_work_materialized
        )
        requires_durable_review = (
            not pre_fix_revision_intermediate_result
            and decision.get("purpose") == "completion"
            and assignment.get("assignmentId") in reviewed_assignment_ids
        )
        # Only the reviewer relationship frozen into the ExecutionDecision may
        # gate acceptance. Runtime/provider diagnostics remain traceable facts.
        requires_any_review = requires_durable_review
        # Each production is one durable typed Artifact. Multi expected types
        # split only when the producer frozen output schema's required top-level
        # fields exactly authorize those types; single expected keeps the
        # historical whole-document identity ``artifact:{dispatch_id}``.
        artifact_productions: list[dict[str, Any]] = []
        if is_reviewer:
            pass
        elif len(expected_types) == 0:
            if produced_artifact_type is not None:
                artifact_productions.append(
                    {
                        "artifact_id": _producer_artifact_id(fence.dispatch_id),
                        "artifact_type": produced_artifact_type,
                        "content": artifact_source_content,
                        "content_sha256": hashlib.sha256(
                            artifact_source_content.encode("utf-8")
                        ).hexdigest(),
                    }
                )
            elif not (is_progress or pre_fix_revision_intermediate_result):
                raise ResearchOrchestrationPersistenceError(
                    "producer_artifact_type_ambiguous"
                )
        elif len(expected_types) == 1:
            artifact_type = expected_types[0]
            if produced_artifact_type is not None and produced_artifact_type != artifact_type:
                raise ResearchOrchestrationPersistenceError("producer_artifact_type_conflict")
            artifact_productions.append(
                {
                    "artifact_id": _producer_artifact_id(fence.dispatch_id),
                    "artifact_type": artifact_type,
                    "content": artifact_source_content,
                    "content_sha256": hashlib.sha256(
                        artifact_source_content.encode("utf-8")
                    ).hexdigest(),
                }
            )
        else:
            if produced_artifact_type is not None and produced_artifact_type not in expected_types:
                raise ResearchOrchestrationPersistenceError("producer_artifact_type_conflict")
            snapshot_ref = decision.get("registrySnapshotRef") or {}
            snapshot_hash = str(snapshot_ref.get("contentHash") or "").strip()
            producer_snapshot: Mapping[str, Any] | None = None
            if snapshot_hash:
                try:
                    producer_snapshot = self.load_registry_snapshot(snapshot_hash)
                except ResearchOrchestrationPersistenceError as exc:
                    raise ResearchOrchestrationPersistenceError(
                        "producer_multi_artifact_schema_missing"
                    ) from exc
            typed_payloads = _split_provider_result_by_expected_types(
                artifact_source_content,
                expected_types,
                output_schema=_producer_frozen_output_schema(
                    producer_snapshot, assignment
                ),
            )
            for artifact_type in expected_types:
                typed_content = typed_payloads[artifact_type]
                artifact_productions.append(
                    {
                        "artifact_id": _producer_artifact_id(
                            fence.dispatch_id, artifact_type
                        ),
                        "artifact_type": artifact_type,
                        "content": typed_content,
                        "content_sha256": hashlib.sha256(
                            typed_content.encode("utf-8")
                        ).hexdigest(),
                    }
                )
        produces_artifact = bool(artifact_productions)
        artifact_id = (
            artifact_productions[0]["artifact_id"] if len(artifact_productions) == 1 else None
        )
        artifact_type = (
            artifact_productions[0]["artifact_type"] if len(artifact_productions) == 1 else None
        )
        existing_artifacts_by_id = {
            row.get("artifactId"): row
            for row in payload.get("artifacts", [])
            if isinstance(row, dict) and row.get("artifactId")
        }
        existing_artifact = (
            existing_artifacts_by_id.get(artifact_productions[0]["artifact_id"])
            if len(artifact_productions) == 1
            else None
        )
        # Invariant: COMPLETION with required Artifact types must never persist as
        # WorkUnit.completed + empty outputArtifactRefs.
        if requires_completion_artifact and not produces_artifact:
            raise ResearchOrchestrationPersistenceError("completion_artifact_required")
        content_for_artifact = artifact_source_content
        content_sha_for_artifact = hashlib.sha256(
            artifact_source_content.encode("utf-8")
        ).hexdigest()
        degraded_override: dict[str, Any] | None = None
        is_leaf_report = (
            str(work.get("substageDefinitionId") or "").split(":", 1)[-1] == "report_generation"
        )
        if requires_completion_artifact and is_leaf_report:
            from app.modules.research_execution_decision_service import (
                _has_materializable_completion_content,
                assemble_degraded_final_report_markdown,
                collect_final_fallback_upstream_artifacts,
            )

            if not _has_materializable_completion_content(result.content):
                if len(artifact_productions) != 1:
                    raise ResearchOrchestrationPersistenceError(
                        "final_report_fallback_multi_artifact_unsupported"
                    )
                upstream = collect_final_fallback_upstream_artifacts(payload, work=work)
                if not upstream:
                    raise ResearchOrchestrationPersistenceError(
                        "final_report_fallback_upstream_missing"
                    )
                markdown = assemble_degraded_final_report_markdown(upstream)
                content_for_artifact = markdown
                content_sha_for_artifact = hashlib.sha256(
                    markdown.encode("utf-8")
                ).hexdigest()
                degraded_override = {
                    "content": markdown,
                    "mimeType": "text/markdown",
                    "title": "Degraded final research report",
                    "contentSha256": content_sha_for_artifact,
                }
                artifact_productions[0]["content"] = markdown
                artifact_productions[0]["content_sha256"] = content_sha_for_artifact
        result_protocol = merge_result_protocols(
            result.result_protocol,
            warnings=[
                *result.warnings,
                *(
                    ["degraded_final_report_assembled_from_upstream_artifacts"]
                    if degraded_override is not None
                    else []
                ),
            ],
        )
        artifact_quality = artifact_quality_for_protocol(result_protocol)
        output_refs = [row["artifact_id"] for row in artifact_productions]
        if requires_completion_artifact and not output_refs:
            raise ResearchOrchestrationPersistenceError("completion_artifact_required")
        # Declared workspace files (runtime_contract.artifact_files) join the
        # same Result refs so the workbench footer renders them natively.
        # Registration is best-effort: it must never fail the formal Result.
        declared_rows, declared_catalog, declared_refs = self._declared_skill_file_artifacts(
            fence=fence,
            work=work,
            assignment=assignment,
            call_id=call_id,
            result_id=result_id,
            existing_artifacts_by_id=existing_artifacts_by_id,
            requires_any_review=requires_any_review,
            artifact_contract=artifact_contract,
            produced_artifact_type=produced_artifact_type,
            expected_types=expected_types,
        )
        output_refs.extend(declared_refs)
        payload["artifacts"].extend(declared_rows)
        if existing_result is not None:
            existing_refs = list(existing_result.get("artifactRefs") or [])
            existing_check_result = existing_result.get("checkResult") or {}
            existing_result_protocol = (
                existing_check_result.get("resultProtocol")
                if isinstance(existing_check_result, Mapping)
                else None
            )
            if existing_result_protocol is None:
                protocol_compatible = True
            else:
                try:
                    protocol_compatible = (
                        normalize_result_protocol(existing_result_protocol)
                        == result_protocol
                    )
                except ValueError:
                    protocol_compatible = False
            refs_compatible = (
                existing_refs == output_refs
                or (
                    # Recovery may resume after a partial materialization: the
                    # durable provider Result identity is authoritative, and
                    # Artifact refs may only grow toward the full expected set.
                    set(existing_refs).issubset(set(output_refs))
                    and existing_result.get("contentSha256") == content_sha256
                )
            )
            if (
                existing_result.get("workUnitId") != work["workUnitId"]
                or existing_result.get("capabilityCallId") != call_id
                or existing_result.get("storageType") != storage_type
                or existing_result.get("storageScope") != storage_scope
                or existing_result.get("storageRef") != storage_ref
                or existing_result.get("contentSha256") != content_sha256
                or existing_result.get("mimeType") != result.mime_type
                or not protocol_compatible
                or not refs_compatible
            ):
                raise ResearchOrchestrationPersistenceError(
                    "dispatch_completed_result_identity_conflict"
                )
        for production in artifact_productions:
            existing_typed = existing_artifacts_by_id.get(production["artifact_id"])
            if existing_typed is not None and (
                existing_typed.get("workUnitId") != work["workUnitId"]
                or existing_typed.get("sourceCapabilityCallId") != call_id
                or existing_typed.get("sourceResultId") != result_id
                or existing_typed.get("artifactType") != production["artifact_type"]
            ):
                raise ResearchOrchestrationPersistenceError(
                    "dispatch_completed_result_identity_conflict"
                )
        call = next(
            (row for row in payload["capabilityCalls"] if row["capabilityCallId"] == call_id),
            None,
        )
        materialized_result_recovery = existing_result is not None
        if materialized_result_recovery:
            # A matching durable Result is stronger evidence than a partially
            # persisted lifecycle projection.  Reconcile only non-conflicting
            # in-flight/recoverable states; known failure or cancellation remains
            # fail-closed rather than being rewritten as success.
            allowed_call_statuses = {
                "queued",
                "running",
                "external_result_unknown",
                "succeeded",
            }
            allowed_work_statuses = {
                "queued",
                "running",
                "reviewing",
                "external_result_unknown",
                "completed",
            }
        else:
            allowed_call_statuses = {"running"}
            if fence.result_recovery_from_unknown:
                allowed_call_statuses.add("external_result_unknown")
            allowed_work_statuses = {work["status"]}
        if (
            call is None
            or call["workUnitId"] != work["workUnitId"]
            or call["attemptNumber"] != fence.external_attempt.attempt_number
            or call["status"] not in allowed_call_statuses
            or work["status"] not in allowed_work_statuses
        ):
            raise ResearchOrchestrationPersistenceError("dispatch_completed_result_state_conflict")
        result_check_status = (
            "pending"
            if requires_any_review
            else "passed"
        )
        result_check = {
            "reasonCode": (
                "result_available_with_warnings"
                if result_protocol.get("status") == "degraded"
                else "durable_review_required"
                if requires_durable_review
                else "initial_contract_validation_passed"
            ),
            "artifactContract": artifact_contract,
            "artifactSchemaDigest": artifact_schema_digest,
            "resultProtocol": deepcopy(result_protocol),
            "artifactQuality": artifact_quality,
            "warnings": list(result_protocol.get("warnings") or []),
        }
        if source_identity_acceptance is not None:
            result_check["sourceIdentityAcceptance"] = deepcopy(
                dict(source_identity_acceptance)
            )
        if scientific_state_fact_projections:
            if scientific_state_projection_declaration is None:
                raise ResearchOrchestrationPersistenceError(
                    "result_scientific_state_projection_declaration_missing"
                )
            result_check["scientificStateProjection"] = {
                "declaration": deepcopy(dict(scientific_state_projection_declaration)),
                "facts": deepcopy(dict(scientific_state_fact_projections)),
            }
        source_refs: list[str] = []
        for ref in [
            *work.get("inputArtifactRefs", []),
            *work.get("inputMemoryRefs", []),
            *(
                str(row.get("evidenceRef") or "").strip()
                for row in result.external_action_evidence
            ),
        ]:
            if ref and ref not in source_refs:
                source_refs.append(ref)
        if existing_result is None:
            payload.setdefault("results", []).append(
                {
                    "resultId": result_id,
                    "workUnitId": work["workUnitId"],
                    "capabilityCallId": call_id,
                    "storageType": storage_type,
                    "storageScope": storage_scope,
                    "storageRef": storage_ref,
                    "contentSha256": content_sha256,
                    "mimeType": result.mime_type,
                    "checkStatus": result_check_status,
                    "checkResult": result_check,
                    "sourceRefs": source_refs,
                    "artifactRefs": output_refs,
                    "scientificStateVersions": [],
                    "createdAt": now.isoformat(),
                }
            )
        else:
            # Recovery: grow Artifact refs to the full expected set without
            # rewriting the durable provider Result identity.
            existing_result["artifactRefs"] = output_refs
        call.update(
            {
                "status": "succeeded",
                "outputArtifactRefs": output_refs,
                "finishedAt": (
                    call.get("finishedAt")
                    or existing_result.get("createdAt")
                    if materialized_result_recovery
                    else now.isoformat()
                ),
            }
        )
        work.update(
            {
                "status": WorkUnitStatus.COMPLETED.value,
                "terminalReasonCode": None,
                "finishedAt": (
                    work.get("finishedAt")
                    or call["finishedAt"]
                    if materialized_result_recovery
                    else now.isoformat()
                ),
            }
        )
        readable_text = _provider_result_message_text(content_for_artifact)
        if readable_text is None and degraded_override is not None:
            readable_text = "已基于上游可用产物生成降级最终报告（有限证据 / 低置信度）。"
        executor = assignment.get("executorRef")
        agent_message = None
        if readable_text is not None and existing_result is None:
            agent_message = {
                "id": _dispatch_agent_message_id(fence),
                "role": "assistant",
                "text": readable_text,
                "at": now.isoformat(),
                "runtime": None,
                "messageType": "research_dispatch_result",
                "authorLabel": executor.get("executorId") if isinstance(executor, dict) else None,
                "capabilityCallId": fence.external_attempt.capability_call_id,
                "artifactRefs": output_refs,
                "confirmationRequests": [],
                "userFacingNarrative": _dispatch_message_narrative(
                    assignment=assignment,
                    result=result,
                    content=content_for_artifact,
                    capability_call_id=call_id,
                    is_reviewer=is_reviewer,
                ),
                "workUnitId": work["workUnitId"],
            }
        catalog_artifacts: list[dict[str, Any]] = []
        if produces_artifact:
            from app.modules.research_execution_decision_service import (
                _has_materializable_completion_content,
            )

            producer_artifact_state = (
                ArtifactState.PARTIAL_SUCCESS
                if (artifact_contract == "general_research_reasoning_artifact_v1"
                    and not _has_materializable_completion_content(content_for_artifact))
                else ArtifactState.NEEDS_REVIEW if requires_any_review
                else ArtifactState.USABLE
            )
            multi_typed = len(artifact_productions) > 1
            for production in artifact_productions:
                production_artifact_id = production["artifact_id"]
                production_artifact_type = production["artifact_type"]
                production_content = production["content"]
                production_content_sha = production["content_sha256"]
                producer_completion_authority = None
                if production_artifact_type.endswith(".planned_output"):
                    producer_completion_authority = (
                        ResearchOrchestrationRepository._producer_completion_authority(
                            decision,
                            assignment,
                        )
                    )
                    if producer_completion_authority is None:
                        raise ResearchOrchestrationPersistenceError(
                            "producer_completion_authority_missing"
                        )
                # Scientific-state projections remain attached to the first
                # production only (historical single-artifact contract); typed
                # payloads themselves stay field-faithful per Artifact.
                attach_scientific_projection = bool(
                    scientific_state_fact_projections
                    and "unfinalized_scientific_content" not in result_protocol.get("warnings", [])
                    and production is artifact_productions[0]
                )
                validator_result = merge_artifact_field_projection(
                    {
                        "status": "passed",
                        "artifactContract": (
                            "degraded_final_report_v1"
                            if degraded_override is not None
                            else artifact_contract
                        ),
                        "artifactSchemaDigest": (
                            production_content_sha
                            if degraded_override is not None
                            else artifact_schema_digest
                        ),
                        "contentSha256": production_content_sha,
                        "resultProtocol": deepcopy(result_protocol),
                        "artifactQuality": artifact_quality,
                        "warnings": list(result_protocol.get("warnings") or []),
                        **(
                            {
                                "sourceIdentityAcceptance": deepcopy(
                                    dict(source_identity_acceptance)
                                )
                            }
                            if source_identity_acceptance is not None
                            else {}
                        ),
                        **(
                            {
                                "qualityMarkers": [
                                    "degraded",
                                    "limited_evidence",
                                    "low_confidence",
                                ],
                                "assemblyMode": "upstream_deterministic_markdown",
                            }
                            if degraded_override is not None
                            else {}
                        ),
                        **(
                            {
                                "producerCompletionAuthority": (
                                    producer_completion_authority
                                )
                            }
                            if producer_completion_authority is not None
                            else {}
                        ),
                        **(
                            {
                                "scientificStateFactProjections": [
                                    {
                                        "factKey": fact_key,
                                        "value": fact_value,
                                        "sourceArtifactId": production_artifact_id,
                                        "sourceContentSha256": production_content_sha,
                                        "producerCapabilityRef": {
                                            "capabilityType": call["capabilityType"],
                                            "capabilityId": call["capabilityId"],
                                        },
                                        "registrySnapshotContentHash": decision[
                                            "registrySnapshotRef"
                                        ]["contentHash"],
                                    }
                                    for fact_key, fact_value in sorted(
                                        (scientific_state_fact_projections or {}).items()
                                    )
                                ]
                            }
                            if attach_scientific_projection
                            else {}
                        ),
                    },
                    production_content if multi_typed else content_for_artifact,
                )
                if production_artifact_id not in existing_artifacts_by_id:
                    payload["artifacts"].append(
                        {
                            "artifactId": production_artifact_id,
                            "workUnitId": work["workUnitId"],
                            "substageInstanceId": work["substageInstanceId"],
                            "sourceCapabilityCallId": call_id,
                            "artifactType": production_artifact_type,
                            "artifactState": producer_artifact_state.value,
                            "version": 1,
                            "sourceRefs": [
                                *work["inputArtifactRefs"],
                                *work["inputMemoryRefs"],
                            ],
                            "validatorResult": validator_result,
                            "lineageKind": "capability_call_output",
                            "sourceResultId": result_id,
                        }
                    )
                result_for_format = result
                if degraded_override is not None:
                    result_for_format = DispatchSubmissionCompleted(
                        title=degraded_override["title"],
                        mime_type=degraded_override["mimeType"],
                        content=degraded_override["content"],
                        provider_id=result.provider_id,
                        adapter=result.adapter,
                        external_action_evidence=result.external_action_evidence,
                        model=result.model,
                        result_protocol=result_protocol,
                        warnings=tuple(result_protocol.get("warnings") or ()),
                    )
                catalog_entry = {
                    "artifact_id": production_artifact_id,
                    "title": (
                        degraded_override["title"]
                        if degraded_override is not None
                        else (
                            f"{result.title}:{production_artifact_type}"
                            if multi_typed
                            else result.title
                        )
                    ),
                    "format": (
                        "markdown"
                        if degraded_override is not None
                        else _canonical_artifact_format(
                            assignment=assignment, result=result_for_format
                        )
                    ),
                    "mime_type": (
                        degraded_override["mimeType"]
                        if degraded_override is not None
                        else result.mime_type
                    ),
                    "capability_call_id": call_id,
                    "related_skill_id": (
                        executor.get("executorId")
                        if isinstance(executor, Mapping)
                        and executor.get("executorType") == "skill"
                        else None
                    ),
                    "metadata": {
                        "taskId": fence.task_id,
                        "workUnitId": fence.work_unit_id,
                        "resultId": result_id,
                        "artifactState": producer_artifact_state.value,
                        "artifactType": production_artifact_type,
                        "validatorResult": validator_result,
                        "resultProtocol": deepcopy(result_protocol),
                        "artifactQuality": artifact_quality,
                        "warnings": list(result_protocol.get("warnings") or []),
                        "providerId": result.provider_id,
                        "adapter": result.adapter,
                    },
                    "storage_ref_suffix": (
                        f"artifact.{production_artifact_type}.json"
                        if multi_typed
                        else None
                    ),
                }
                if degraded_override is not None:
                    catalog_entry["contentOverride"] = degraded_override["content"]
                    catalog_entry["contentSha256Override"] = degraded_override[
                        "contentSha256"
                    ]
                    catalog_entry["mimeTypeOverride"] = degraded_override["mimeType"]
                elif multi_typed:
                    catalog_entry["contentOverride"] = production_content
                    catalog_entry["contentSha256Override"] = production_content_sha
                    catalog_entry["mimeTypeOverride"] = "application/json"
                elif production_content != result.content:
                    catalog_entry["contentOverride"] = production_content
                    catalog_entry["contentSha256Override"] = production_content_sha
                    catalog_entry["mimeTypeOverride"] = result.mime_type
                if production_artifact_id not in existing_artifacts_by_id:
                    catalog_artifacts.append(catalog_entry)
        catalog_artifacts.extend(declared_catalog)
        catalog_artifact = catalog_artifacts[0] if len(catalog_artifacts) == 1 else (
            catalog_artifacts if catalog_artifacts else None
        )
        if is_reviewer and existing_result is None:
            review = json.loads(result.content)
            verdict = review["verdict"]
            if verdict == "external_result_unknown":
                raise ResearchOrchestrationPersistenceError(
                    "external_result_unknown_is_not_a_completed_review"
                )
            automatic_review_recovery = bool(
                _confirmed_automatic_research(task)
                and isinstance(completion_policy, dict)
                and completion_policy.get("onReviewerReject")
                == "new_revision_decision"
                and verdict in {"needs_revision", "needs_input", "insufficient_materials"}
            )
            decision_value = (
                "approved"
                if verdict == "usable"
                else "rejected"
                if verdict == "needs_revision"
                else "insufficient_evidence"
            )
            dependency_id = assignment["dependencyAssignmentIds"][0] if assignment["dependencyAssignmentIds"] else None
            dependency_work = next(
                (row for row in payload["workUnits"] if row["assignmentId"] == dependency_id),
                None,
            )
            reviewer_result = next(
                row for row in payload["results"] if row["resultId"] == result_id
            )
            reviewer_result["checkResult"].update(
                {
                    "reviewVerdict": verdict,
                    "reviewDecision": decision_value,
                }
            )
            reviewed_result_ids: list[str] = []
            reviewed_artifact_refs = set(work["inputArtifactRefs"])
            for candidate_result in payload["results"]:
                if candidate_result["resultId"] == result_id or not (
                    reviewed_artifact_refs & set(candidate_result.get("artifactRefs") or [])
                ):
                    continue
                candidate_result["checkStatus"] = (
                    "passed" if decision_value == "approved" else "failed"
                )
                candidate_result["checkResult"].update(
                    {
                        "reviewResultId": result_id,
                        "reviewVerdict": verdict,
                        "reviewDecision": decision_value,
                    }
                )
                reviewed_result_ids.append(candidate_result["resultId"])
            review_record = {
                "interactionId": f"interaction:{fence.dispatch_id}",
                "idempotencyKey": f"{fence.idempotency_key}:review",
                "taskId": fence.task_id,
                "executionDecisionId": fence.execution_decision_id,
                "sourceAssignmentId": assignment["assignmentId"],
                "sourceWorkUnitId": work["workUnitId"],
                "targetAssignmentId": dependency_id,
                "targetWorkUnitId": None if dependency_work is None else dependency_work["workUnitId"],
                "interactionType": "review_submitted",
                "inputArtifactRefs": list(work["inputArtifactRefs"]),
                "outputArtifactRefs": [],
                "verdict": {
                    "verdictType": "review",
                    "decision": decision_value,
                    "rationaleCode": f"review_{verdict}",
                },
                "summary": "Independent result review completed under the frozen review contract.",
                "createdAt": now.isoformat(),
            }
            payload["agentInteractionRecords"].append(review_record)
            reviewed_artifacts = [
                artifact
                for artifact in payload["artifacts"]
                if artifact["artifactId"] in work["inputArtifactRefs"]
            ]
            for artifact in reviewed_artifacts:
                validator = dict(artifact.get("validatorResult") or {})
                validator.update(
                    {
                        "reviewVerdict": verdict,
                        "reviewDecision": decision_value,
                        "reviewBasis": review.get("basis"),
                        "reviewIssues": review.get("issues") or [],
                        "reviewMissingInputs": review.get("missingInputs") or [],
                    }
                )
                artifact["validatorResult"] = validator
            if decision_value == "approved":
                for artifact in reviewed_artifacts:
                    if artifact["artifactState"] == ArtifactState.NEEDS_REVIEW.value:
                        artifact["artifactState"] = ArtifactState.USABLE.value
                    elif artifact["artifactState"] != ArtifactState.USABLE.value:
                        raise ResearchOrchestrationPersistenceError(
                            "review_artifact_state_not_promotable"
                        )
                payload["agentInteractionRecords"].append(
                    {
                        **review_record,
                        "interactionId": f"interaction:{fence.dispatch_id}:acceptance",
                        "idempotencyKey": f"{fence.idempotency_key}:acceptance",
                        "interactionType": "acceptance_recorded",
                        "verdict": {
                            "verdictType": "acceptance",
                            "decision": "approved",
                            "rationaleCode": f"review_{verdict}",
                        },
                        "summary": "Approved independent review recorded as current-project acceptance.",
                    }
                )
            else:
                outcome = {
                    "needs_revision": "rejected",
                    "needs_input": "needs_input",
                    "insufficient_materials": "blocked",
                    "human_decision_required": "blocked",
                }[verdict]
                decision.update(
                    {
                        "status": "closed",
                        "outcome": outcome,
                        "closedAt": now.isoformat(),
                    }
                )
                runtime = next(
                    row for row in payload["substageRuntimeStates"]
                    if row["substageInstanceId"] == work["substageInstanceId"]
                )
                runtime.update(
                    {
                        "status": (
                            "ready"
                            if automatic_review_recovery
                            else "needs_input"
                            if verdict in {"needs_input", "human_decision_required"}
                            else "insufficient_evidence"
                            if verdict == "insufficient_materials"
                            else "blocked_recoverable"
                        ),
                        "activeExecutionDecisionId": None,
                        "statusReasonCode": f"review_{verdict}",
                        "updatedAt": now.isoformat(),
                    }
                )
                artifact_state = {
                    "needs_revision": "rejected_or_needs_revision",
                    "needs_input": "needs_input",
                    "insufficient_materials": "insufficient_materials",
                    "human_decision_required": "rejected_or_needs_revision",
                }[verdict]
                for artifact in reviewed_artifacts:
                    artifact["artifactState"] = artifact_state
            if decision_value == "approved":
                for reviewed_result_id in reviewed_result_ids:
                    payload = _materialize_checked_result_projection(
                        payload,
                        result_id=reviewed_result_id,
                        occurred_at=now,
                    )
        if not is_reviewer:
            from app.modules.research_execution_decision_service import (
                general_completion_result_authorized,
            )

            acceptance_id = f"interaction:{fence.dispatch_id}:general-acceptance"
            if (
                general_completion_result_authorized(
                    task,
                    decision=decision,
                    work=work,
                    artifact_contract=artifact_contract,
                    result_check_status=result_check_status,
                    artifact_content=content_for_artifact,
                )
                and not any(
                    row.get("interactionId") == acceptance_id
                    for row in payload.get("agentInteractionRecords", [])
                )
            ):
                payload.setdefault("agentInteractionRecords", []).append(
                    {
                        "interactionId": acceptance_id,
                        "idempotencyKey": f"{fence.idempotency_key}:general-acceptance",
                        "taskId": fence.task_id,
                        "executionDecisionId": fence.execution_decision_id,
                        "sourceAssignmentId": assignment["assignmentId"],
                        "sourceWorkUnitId": work["workUnitId"],
                        "targetAssignmentId": None,
                        "targetWorkUnitId": None,
                        "interactionType": "acceptance_recorded",
                        "inputArtifactRefs": [],
                        "outputArtifactRefs": output_refs,
                        "verdict": {
                            "verdictType": "acceptance",
                            "decision": "approved",
                            "rationaleCode": "general_validated_completion_artifact",
                        },
                        "summary": (
                            "Validated general research judgment accepted under "
                            "the exact completion authority."
                        ),
                        "createdAt": now.isoformat(),
                    }
                )
        if (scientific_state_fact_projections
            and "unfinalized_scientific_content" not in result_protocol.get("warnings", [])
            and existing_result is None):
            if not produces_artifact:
                raise ResearchOrchestrationPersistenceError(
                    "scientific_state_projection_without_artifact"
                )
            payload = _materialize_checked_result_projection(
                payload,
                result_id=result_id,
                occurred_at=now,
            )

        from app.modules.target_output_reuse_materialization import (
            materialize_checked_agent_scientific_facts,
        )

        payload = materialize_checked_agent_scientific_facts(
            task_payload=payload,
            result_id=result_id,
            validated_artifact_content=artifact_source_content,
            occurred_at=now,
        )

        updated = ResearchTaskV12.model_validate(payload)
        if pre_fix_revision_intermediate_result:
            from app.modules.research_execution_decision_service import (
                close_decision_from_durable_facts,
                reconcile_task_lifecycle_from_runtime,
            )

            updated = close_decision_from_durable_facts(
                updated,
                execution_decision_id=fence.execution_decision_id,
                closed_at=now,
                recoverable_terminal_outcome=ExecutionDecisionOutcome.BLOCKED,
            )
            updated = self._reconcile_task_lifecycle_with_frozen_snapshot(
                updated, occurred_at=now
            )
            return updated, catalog_artifact, (), agent_message
        if is_reviewer:
            from app.modules.research_execution_decision_service import (
                reconcile_task_lifecycle_from_runtime,
            )

            if decision_value == "approved":
                from app.modules.research_execution_decision_service import close_decision_from_durable_facts

                updated = close_decision_from_durable_facts(
                    updated,
                    execution_decision_id=fence.execution_decision_id,
                    closed_at=now,
                )
            updated = self._reconcile_task_lifecycle_with_frozen_snapshot(
                updated, occurred_at=now
            )
            return updated, None, (), agent_message
        try:
            from app.modules.research_execution_decision_service import (
                DecisionPolicyViolation,
                close_decision_from_durable_facts,
                reconcile_task_lifecycle_from_runtime,
            )

            closed = close_decision_from_durable_facts(
                updated,
                execution_decision_id=fence.execution_decision_id,
                closed_at=now,
            )
        except DecisionPolicyViolation as exc:
            if exc.code != "decision_not_closable":
                closed = updated
            else:
                from app.modules.research_execution_decision_service import (
                    reconcile_closed_successful_result_progress,
                )

                lifecycle_snapshot = self.load_registry_snapshot(
                    updated.registry_snapshot_ref.content_hash
                )
                closed = reconcile_closed_successful_result_progress(
                    updated,
                    completed_execution_decision_id=fence.execution_decision_id,
                    occurred_at=now,
                    snapshot=lifecycle_snapshot,
                )
        reconciled = self._reconcile_task_lifecycle_with_frozen_snapshot(
            closed, occurred_at=now
        )
        return reconciled, catalog_artifact, (), agent_message

    def record_dispatch_retryable_failure(
        self,
        fence: DispatchFence | AuthorizedDispatch,
        *,
        owner_id: str,
        workspace_id: str,
        error_code: str,
        max_attempts: int,
        retry_delay_seconds: int,
    ) -> str:
        """Release one live pre-submission fence to a bounded durable retry.

        Returns ``retry_scheduled``, ``failed``, ``submission_unknown`` or ``stale``.
        Defense in depth: an authorized dispatch whose external action already
        started is atomically moved to ``submission_unknown`` instead of pending,
        preserving no-replay while guaranteeing recovery liveness.
        """
        self._require_write_enabled()
        self._require_postgres()
        if not error_code.strip():
            raise ValueError("error_code is required")
        if max_attempts < 1 or retry_delay_seconds < 1:
            raise ValueError("invalid dispatch retry policy")
        authorized = isinstance(fence, AuthorizedDispatch)
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                task, raw_payload = self._load_locked_task(
                    cursor,
                    task_id=fence.task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                cursor.execute(
                    """SELECT *, lease_expires_at > clock_timestamp() AS lease_live
                         FROM openair_dispatch
                        WHERE dispatch_id = %s AND task_id = %s
                        FOR UPDATE""",
                    (fence.dispatch_id, fence.task_id),
                )
                row = cursor.fetchone()
                matches = (
                    self._matches_authorized_fence(row, fence)
                    if row is not None and authorized
                    else row is not None
                    and self._matches_fence(row, fence, status=DispatchStatus.CLAIMED)
                )
                if not matches or not row["lease_live"]:
                    return "stale"
                if authorized and row["external_action_started_at"] is not None:
                    now = datetime.now(timezone.utc)
                    unknown_task = self._task_with_outcome(
                        task,
                        fence,
                        "unknown",
                        error_code,
                        now,
                        external_action_started=True,
                        raw_payload=raw_payload,
                    )
                    self._write_locked_task(cursor, raw_payload, unknown_task)
                    cursor.execute(
                        """UPDATE openair_dispatch
                              SET status = 'submission_unknown',
                                  last_error_code = %s,
                                  last_failure_at = clock_timestamp(),
                                  next_attempt_at = NULL,
                                  lease_owner = NULL,
                                  lease_expires_at = NULL,
                                  updated_at = clock_timestamp()
                            WHERE dispatch_id = %s AND task_id = %s
                              AND execution_decision_id = %s AND work_unit_id = %s
                              AND idempotency_key = %s
                              AND status = 'submission_authorized'
                              AND lease_owner = %s
                              AND attempt_count = %s
                              AND authorization_task_revision = %s""",
                        (
                            error_code,
                            fence.dispatch_id,
                            fence.task_id,
                            fence.execution_decision_id,
                            fence.work_unit_id,
                            fence.idempotency_key,
                            fence.lease_owner,
                            fence.attempt_count,
                            fence.authorization_task_revision,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise ResearchOrchestrationPersistenceError(
                            "dispatch_started_unknown_race"
                        )
                    _ensure_unknown_recovery_obligation(
                        self,
                        cursor,
                        dispatch_id=fence.dispatch_id,
                        error_code=error_code,
                        persistence_error=ResearchOrchestrationPersistenceError,
                    )
                    return "submission_unknown"
                terminal = int(row["attempt_count"]) >= max_attempts
                if terminal:
                    failed_task = self._task_with_outcome(
                        task,
                        fence,
                        "known_rejected",
                        error_code,
                        datetime.now(timezone.utc),
                        external_action_started=False,
                        raw_payload=raw_payload,
                    )
                    self._write_locked_task(cursor, raw_payload, failed_task)
                    cursor.execute(
                        """UPDATE openair_dispatch
                              SET status = 'failed', authorization_task_revision = NULL,
                                  lease_owner = NULL, lease_expires_at = NULL,
                                  next_attempt_at = NULL, last_error_code = %(error_code)s,
                                  last_failure_at = clock_timestamp(),
                                  updated_at = clock_timestamp()
                            WHERE dispatch_id = %(dispatch_id)s
                              AND task_id = %(task_id)s
                              AND execution_decision_id = %(execution_decision_id)s
                              AND work_unit_id = %(work_unit_id)s
                              AND idempotency_key = %(idempotency_key)s
                              AND lease_owner = %(lease_owner)s
                              AND attempt_count = %(attempt_count)s
                              AND status = %(status)s
                              AND lease_expires_at > clock_timestamp()""",
                        {
                            **fence.__dict__,
                            "error_code": error_code,
                            "status": (
                                DispatchStatus.SUBMISSION_AUTHORIZED.value
                                if authorized
                                else DispatchStatus.CLAIMED.value
                            ),
                        },
                    )
                    if cursor.rowcount != 1:
                        raise ResearchOrchestrationPersistenceError(
                            "dispatch_retry_terminal_race"
                        )
                    self._enqueue_dispatch_continuation(cursor, fence.dispatch_id)
                    return "failed"
                cursor.execute(
                    """UPDATE openair_dispatch
                          SET status = 'pending', authorization_task_revision = NULL,
                              lease_owner = NULL, lease_expires_at = NULL,
                              next_attempt_at = clock_timestamp() + make_interval(secs => %(delay)s),
                              last_error_code = %(error_code)s,
                              last_failure_at = clock_timestamp(),
                              updated_at = clock_timestamp()
                        WHERE dispatch_id = %(dispatch_id)s
                          AND task_id = %(task_id)s
                          AND execution_decision_id = %(execution_decision_id)s
                          AND work_unit_id = %(work_unit_id)s
                          AND idempotency_key = %(idempotency_key)s
                          AND lease_owner = %(lease_owner)s
                          AND attempt_count = %(attempt_count)s
                          AND status = %(status)s
                          AND lease_expires_at > clock_timestamp()
                          AND external_action_started_at IS NULL""",
                    {
                        **fence.__dict__,
                        "delay": retry_delay_seconds,
                        "error_code": error_code,
                        "status": (
                            DispatchStatus.SUBMISSION_AUTHORIZED.value
                            if authorized
                            else DispatchStatus.CLAIMED.value
                        ),
                    },
                )
                if cursor.rowcount != 1:
                    return "stale"
                return "retry_scheduled"

    def _quarantine_invalid_task_dispatch_locked(
        self,
        cursor: psycopg.Cursor[Any],
        *,
        dispatch_id: str,
        task_id: str,
        execution_decision_id: str,
        work_unit_id: str,
        idempotency_key: str,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int,
    ) -> str:
        """Settle one legacy poison dispatch without parsing its invalid Task payload."""
        cursor.execute(
            """SELECT revision FROM openair_tasks
                WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                  AND status <> 'deleted'
                FOR UPDATE""",
            (task_id, owner_id, workspace_id),
        )
        task_row = cursor.fetchone()
        if task_row is None:
            raise ResearchTaskNotFoundError("research_task_not_found")
        if int(task_row["revision"]) != expected_task_revision:
            return "stale"

        cursor.execute(
            """SELECT *,
                      lease_expires_at IS NOT NULL
                      AND lease_expires_at <= clock_timestamp() AS lease_expired
                 FROM openair_dispatch
                WHERE dispatch_id = %s AND task_id = %s
                FOR UPDATE""",
            (dispatch_id, task_id),
        )
        row = cursor.fetchone()
        if (
            row is None
            or row["execution_decision_id"] != execution_decision_id
            or row["work_unit_id"] != work_unit_id
            or row["idempotency_key"] != idempotency_key
            or row["status"]
            not in {
                DispatchStatus.PENDING.value,
                DispatchStatus.CLAIMED.value,
                DispatchStatus.SUBMISSION_AUTHORIZED.value,
                DispatchStatus.SUBMISSION_UNKNOWN.value,
            }
            or (
                row["status"]
                in {
                    DispatchStatus.CLAIMED.value,
                    DispatchStatus.SUBMISSION_AUTHORIZED.value,
                }
                and not row["lease_expired"]
            )
            or (
                row["status"] == DispatchStatus.SUBMISSION_UNKNOWN.value
                and (
                    row.get("submission_mode") != "single_attempt_no_replay"
                    or row.get("external_action_started_at") is None
                    or row.get("result_storage_ref") is None
                    or (
                        row.get("lease_expires_at") is not None
                        and not row["lease_expired"]
                    )
                )
            )
        ):
            return "stale"

        action_started = row.get("external_action_started_at") is not None
        observed_attempt = int(row["attempt_count"])
        if action_started:
            cursor.execute(
                """UPDATE openair_dispatch
                      SET status = 'submission_unknown',
                          last_error_code = 'invalid_research_orchestration_payload',
                          last_failure_at = clock_timestamp(),
                          next_attempt_at = NULL,
                          lease_owner = NULL,
                          lease_expires_at = NULL,
                          updated_at = clock_timestamp()
                    WHERE dispatch_id = %(dispatch_id)s
                      AND task_id = %(task_id)s
                      AND execution_decision_id = %(execution_decision_id)s
                      AND work_unit_id = %(work_unit_id)s
                      AND idempotency_key = %(idempotency_key)s
                      AND status = %(observed_status)s
                      AND attempt_count = %(observed_attempt)s
                      AND lease_owner IS NOT DISTINCT FROM %(observed_lease_owner)s
                      AND authorization_task_revision IS NOT DISTINCT FROM
                          %(observed_authorization_task_revision)s
                      AND external_action_started_at IS NOT NULL
                    RETURNING dispatch_id""",
                {
                    "dispatch_id": dispatch_id,
                    "task_id": task_id,
                    "execution_decision_id": execution_decision_id,
                    "work_unit_id": work_unit_id,
                    "idempotency_key": idempotency_key,
                    "observed_status": row["status"],
                    "observed_attempt": observed_attempt,
                    "observed_lease_owner": row.get("lease_owner"),
                    "observed_authorization_task_revision": row.get(
                        "authorization_task_revision"
                    ),
                },
            )
            if cursor.fetchone() is None:
                return "stale"
            _ensure_unknown_recovery_obligation(
                self,
                cursor,
                dispatch_id=dispatch_id,
                error_code="invalid_research_orchestration_payload",
                persistence_error=ResearchOrchestrationPersistenceError,
            )
            return DispatchStatus.SUBMISSION_UNKNOWN.value

        cursor.execute(
            """UPDATE openair_dispatch
                  SET status = 'failed',
                      attempt_count = %(next_attempt)s,
                      authorization_task_revision = NULL,
                      last_error_code = 'invalid_research_orchestration_payload',
                      last_failure_at = clock_timestamp(),
                      next_attempt_at = NULL,
                      lease_owner = NULL,
                      lease_expires_at = NULL,
                      updated_at = clock_timestamp()
                WHERE dispatch_id = %(dispatch_id)s
                  AND task_id = %(task_id)s
                  AND execution_decision_id = %(execution_decision_id)s
                  AND work_unit_id = %(work_unit_id)s
                  AND idempotency_key = %(idempotency_key)s
                  AND status = %(observed_status)s
                  AND attempt_count = %(observed_attempt)s
                  AND lease_owner IS NOT DISTINCT FROM %(observed_lease_owner)s
                  AND authorization_task_revision IS NOT DISTINCT FROM
                      %(observed_authorization_task_revision)s
                  AND external_action_started_at IS NULL
                RETURNING dispatch_id""",
            {
                "dispatch_id": dispatch_id,
                "task_id": task_id,
                "execution_decision_id": execution_decision_id,
                "work_unit_id": work_unit_id,
                "idempotency_key": idempotency_key,
                "next_attempt": observed_attempt + 1,
                "observed_status": row["status"],
                "observed_attempt": observed_attempt,
                "observed_lease_owner": row.get("lease_owner"),
                "observed_authorization_task_revision": row.get(
                    "authorization_task_revision"
                ),
            },
        )
        if cursor.fetchone() is None:
            return "stale"
        return DispatchStatus.FAILED.value

    def record_dispatch_resolution_failure(
        self,
        *,
        dispatch_id: str,
        task_id: str,
        execution_decision_id: str,
        work_unit_id: str,
        idempotency_key: str,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int,
        error_code: str,
        retryable: bool,
        max_attempts: int,
        retry_delay_seconds: int,
    ) -> str:
        """Quarantine one due resolver row so poison rows cannot starve the queue."""
        self._require_write_enabled()
        self._require_postgres()
        self._require_expected_revision(expected_task_revision)
        if not error_code.strip() or max_attempts < 1 or retry_delay_seconds < 1:
            raise ValueError("invalid dispatch resolution failure")
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                try:
                    task, raw_payload = self._load_locked_task(
                        cursor,
                        task_id=task_id,
                        owner_id=owner_id,
                        workspace_id=workspace_id,
                    )
                except ResearchOrchestrationPersistenceError as exc:
                    if str(exc) != "invalid_research_orchestration_payload":
                        raise
                    return self._quarantine_invalid_task_dispatch_locked(
                        cursor,
                        dispatch_id=dispatch_id,
                        task_id=task_id,
                        execution_decision_id=execution_decision_id,
                        work_unit_id=work_unit_id,
                        idempotency_key=idempotency_key,
                        owner_id=owner_id,
                        workspace_id=workspace_id,
                        expected_task_revision=expected_task_revision,
                    )
                if task.revision != expected_task_revision:
                    return "stale"
                cursor.execute(
                    """SELECT *,
                              lease_expires_at IS NOT NULL
                              AND lease_expires_at <= clock_timestamp() AS lease_expired
                         FROM openair_dispatch
                        WHERE dispatch_id = %s AND task_id = %s
                        FOR UPDATE""",
                    (dispatch_id, task_id),
                )
                row = cursor.fetchone()
                if (
                    row is None
                    or row["execution_decision_id"] != execution_decision_id
                    or row["work_unit_id"] != work_unit_id
                    or row["idempotency_key"] != idempotency_key
                    or row["status"] not in {
                        DispatchStatus.PENDING.value,
                        DispatchStatus.CLAIMED.value,
                        DispatchStatus.SUBMISSION_AUTHORIZED.value,
                    }
                    or (
                        row["status"] != DispatchStatus.PENDING.value
                        and not row["lease_expired"]
                    )
                ):
                    return "stale"
                persisted_work_unit = (
                    next(
                        (item for item in task.work_units if item.work_unit_id == work_unit_id),
                        None,
                    )
                    if isinstance(task, ResearchTaskV12)
                    else None
                )
                if isinstance(task, ResearchTaskV12) and persisted_work_unit is None:
                    action_started = row["external_action_started_at"] is not None
                    terminal_reason = (
                        "dispatch_work_unit_missing_after_action_started"
                        if action_started
                        else "dispatch_work_unit_missing"
                    )
                    settled_status = (
                        DispatchStatus.SUBMISSION_UNKNOWN.value
                        if action_started
                        else DispatchStatus.FAILED.value
                    )
                    observed_attempt = int(row["attempt_count"])
                    settled_attempt = observed_attempt if action_started else observed_attempt + 1
                    now = datetime.now(timezone.utc)
                    settlement_sql = (
                        """UPDATE openair_dispatch
                              SET status = 'submission_unknown',"""
                        if action_started
                        else """UPDATE openair_dispatch
                              SET status = 'failed',"""
                    ) + """
                                  attempt_count = %(settled_attempt)s,
                                  authorization_task_revision = %(settled_authorization_task_revision)s,
                                  last_error_code = %(terminal_reason)s,
                                  last_failure_at = clock_timestamp(),
                                  next_attempt_at = NULL,
                                  lease_owner = NULL,
                                  lease_expires_at = NULL,
                                  updated_at = clock_timestamp()
                            WHERE dispatch_id = %(dispatch_id)s
                              AND task_id = %(task_id)s
                              AND execution_decision_id = %(execution_decision_id)s
                              AND work_unit_id = %(work_unit_id)s
                              AND idempotency_key = %(idempotency_key)s
                              AND status = %(observed_status)s
                              AND attempt_count = %(observed_attempt)s
                              AND lease_owner IS NOT DISTINCT FROM %(observed_lease_owner)s
                              AND authorization_task_revision IS NOT DISTINCT FROM
                                  %(observed_authorization_task_revision)s
                              AND (
                                    (%(observed_status)s = 'pending'
                                     AND lease_expires_at IS NULL)
                                 OR (%(observed_status)s <> 'pending'
                                     AND lease_expires_at IS NOT NULL
                                     AND lease_expires_at <= clock_timestamp())
                              )
                              AND (
                                    (%(action_started)s
                                     AND external_action_started_at IS NOT NULL)
                                 OR (NOT %(action_started)s
                                     AND external_action_started_at IS NULL)
                              )
                            RETURNING dispatch_id"""
                    cursor.execute(
                        settlement_sql,
                        {
                            "dispatch_id": dispatch_id,
                            "task_id": task_id,
                            "execution_decision_id": execution_decision_id,
                            "work_unit_id": work_unit_id,
                            "idempotency_key": idempotency_key,
                            "settled_attempt": settled_attempt,
                            "settled_authorization_task_revision": (
                                row.get("authorization_task_revision") if action_started else None
                            ),
                            "terminal_reason": terminal_reason,
                            "observed_status": row["status"],
                            "observed_attempt": observed_attempt,
                            "observed_lease_owner": row.get("lease_owner"),
                            "observed_authorization_task_revision": row.get(
                                "authorization_task_revision"
                            ),
                            "action_started": action_started,
                        },
                    )
                    if cursor.fetchone() is None:
                        return "stale"
                    self._write_locked_task(
                        cursor,
                        raw_payload,
                        task,
                        payload_patch=self._dispatch_integrity_blocker_patch(
                            raw_payload,
                            dispatch_id=dispatch_id,
                            execution_decision_id=execution_decision_id,
                            work_unit_id=work_unit_id,
                            reason_code=terminal_reason,
                            action_started=action_started,
                            recorded_at=now,
                        ),
                    )
                    if action_started:
                        _ensure_unknown_recovery_obligation(
                            self,
                            cursor,
                            dispatch_id=dispatch_id,
                            error_code=terminal_reason,
                            persistence_error=ResearchOrchestrationPersistenceError,
                        )
                    return settled_status
                if (
                    row["status"] == DispatchStatus.SUBMISSION_AUTHORIZED.value
                    and row["external_action_started_at"] is not None
                ):
                    # Resolver failures happen before provider construction, but an
                    # expired authorized row may already have crossed the external
                    # action-start boundary in a previous worker process.  Such a
                    # row can never return to pending or be treated as a normal
                    # resolution failure: the only safe liveness transition is an
                    # absorbing unknown result that requires explicit reconciliation.
                    observed_attempt = int(row["attempt_count"])
                    fence = DispatchFence(
                        dispatch_id=dispatch_id,
                        task_id=task_id,
                        execution_decision_id=execution_decision_id,
                        work_unit_id=work_unit_id,
                        idempotency_key=idempotency_key,
                        lease_owner=str(row.get("lease_owner") or "resolver"),
                        attempt_count=observed_attempt,
                    )
                    unknown_task = None
                    if isinstance(task, ResearchTaskV12):
                        unknown_task = self._task_with_outcome(
                            task,
                            fence,
                            "unknown",
                            error_code,
                            datetime.now(timezone.utc),
                            # Do not make liveness depend on a task-side call row:
                            # the durable dispatch action-start fact is authoritative.
                            external_action_started=False,
                            raw_payload=raw_payload,
                        )
                    cursor.execute(
                        """UPDATE openair_dispatch
                              SET status = 'submission_unknown',
                                  last_error_code = %(error_code)s,
                                  last_failure_at = clock_timestamp(),
                                  next_attempt_at = NULL,
                                  lease_owner = NULL,
                                  lease_expires_at = NULL,
                                  updated_at = clock_timestamp()
                            WHERE dispatch_id = %(dispatch_id)s
                              AND task_id = %(task_id)s
                              AND execution_decision_id = %(execution_decision_id)s
                              AND work_unit_id = %(work_unit_id)s
                              AND idempotency_key = %(idempotency_key)s
                              AND status = 'submission_authorized'
                              AND attempt_count = %(observed_attempt)s
                              AND external_action_started_at IS NOT NULL
                              AND lease_expires_at IS NOT NULL
                              AND lease_expires_at <= clock_timestamp()
                            RETURNING dispatch_id""",
                        {
                            "dispatch_id": dispatch_id,
                            "task_id": task_id,
                            "execution_decision_id": execution_decision_id,
                            "work_unit_id": work_unit_id,
                            "idempotency_key": idempotency_key,
                            "observed_attempt": observed_attempt,
                            "error_code": error_code,
                        },
                    )
                    if cursor.fetchone() is None:
                        return "stale"
                    if unknown_task is not None:
                        self._write_locked_task(cursor, raw_payload, unknown_task)
                    _ensure_unknown_recovery_obligation(
                        self,
                        cursor,
                        dispatch_id=dispatch_id,
                        error_code=error_code,
                        persistence_error=ResearchOrchestrationPersistenceError,
                    )
                    return "submission_unknown"
                next_attempt = int(row["attempt_count"]) + 1
                terminal = not retryable or next_attempt >= max_attempts
                fence = DispatchFence(
                    dispatch_id=dispatch_id,
                    task_id=task_id,
                    execution_decision_id=execution_decision_id,
                    work_unit_id=work_unit_id,
                    idempotency_key=idempotency_key,
                    lease_owner=str(row.get("lease_owner") or "resolver"),
                    attempt_count=next_attempt,
                )
                if terminal:
                    failed_task = self._task_with_outcome(
                        task,
                        fence,
                        "known_rejected",
                        error_code,
                        datetime.now(timezone.utc),
                        external_action_started=False,
                        raw_payload=raw_payload,
                    )
                    self._write_locked_task(cursor, raw_payload, failed_task)
                    cursor.execute(
                        """UPDATE openair_dispatch
                              SET status = 'failed', attempt_count = %(next_attempt)s,
                                  authorization_task_revision = NULL,
                                  lease_owner = NULL, lease_expires_at = NULL,
                                  next_attempt_at = NULL, last_error_code = %(error_code)s,
                                  last_failure_at = clock_timestamp(),
                                  updated_at = clock_timestamp()
                            WHERE dispatch_id = %(dispatch_id)s
                              AND task_id = %(task_id)s
                              AND execution_decision_id = %(execution_decision_id)s
                              AND work_unit_id = %(work_unit_id)s
                              AND idempotency_key = %(idempotency_key)s
                              AND status = %(observed_status)s
                              AND attempt_count = %(observed_attempt)s
                              AND external_action_started_at IS NULL""",
                        {
                            "dispatch_id": dispatch_id,
                            "task_id": task_id,
                            "execution_decision_id": execution_decision_id,
                            "work_unit_id": work_unit_id,
                            "idempotency_key": idempotency_key,
                            "next_attempt": next_attempt,
                            "error_code": error_code,
                            "observed_status": row["status"],
                            "observed_attempt": row["attempt_count"],
                        },
                    )
                    if cursor.rowcount != 1:
                        raise ResearchOrchestrationPersistenceError(
                            "dispatch_resolution_terminal_race"
                        )
                    self._enqueue_dispatch_continuation(cursor, dispatch_id)
                    return "failed"
                cursor.execute(
                    """UPDATE openair_dispatch
                          SET status = 'pending', attempt_count = %(next_attempt)s,
                              authorization_task_revision = NULL,
                              lease_owner = NULL, lease_expires_at = NULL,
                              next_attempt_at = clock_timestamp() + make_interval(secs => %(delay)s),
                              last_error_code = %(error_code)s,
                              last_failure_at = clock_timestamp(),
                              updated_at = clock_timestamp()
                        WHERE dispatch_id = %(dispatch_id)s
                          AND task_id = %(task_id)s
                          AND execution_decision_id = %(execution_decision_id)s
                          AND work_unit_id = %(work_unit_id)s
                          AND idempotency_key = %(idempotency_key)s
                          AND status = %(observed_status)s
                          AND attempt_count = %(observed_attempt)s
                          AND external_action_started_at IS NULL""",
                    {
                        "dispatch_id": dispatch_id,
                        "task_id": task_id,
                        "execution_decision_id": execution_decision_id,
                        "work_unit_id": work_unit_id,
                        "idempotency_key": idempotency_key,
                        "next_attempt": next_attempt,
                        "delay": retry_delay_seconds,
                        "error_code": error_code,
                        "observed_status": row["status"],
                        "observed_attempt": row["attempt_count"],
                    },
                )
                if cursor.rowcount != 1:
                    return "stale"
                return "retry_scheduled"

    def fail_unsubmitted_dispatch(
        self,
        fence: DispatchFence,
        *,
        owner_id: str,
        workspace_id: str,
        error_code: str,
    ) -> bool:
        """Fail a fenced claim only when no submission authorization exists."""
        self._require_write_enabled()
        self._require_postgres()
        if not error_code.strip():
            raise ValueError("error_code is required")
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                task, raw_payload = self._load_locked_task(
                    cursor,
                    task_id=fence.task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                cursor.execute(
                    """SELECT *, lease_expires_at > clock_timestamp() AS lease_live
                        FROM openair_dispatch
                        WHERE dispatch_id = %s AND task_id = %s
                        FOR UPDATE""",
                    (fence.dispatch_id, fence.task_id),
                )
                dispatch_row = cursor.fetchone()
                if dispatch_row is None or not self._matches_fence(
                    dispatch_row, fence, status=DispatchStatus.CLAIMED
                ) or not dispatch_row["lease_live"]:
                    return False
                updated_task = self._task_with_outcome(
                    task,
                    fence,
                    "known_rejected",
                    error_code,
                    datetime.now(timezone.utc),
                    external_action_started=False,
                    raw_payload=raw_payload,
                )
                self._write_locked_task(cursor, raw_payload, updated_task)
                cursor.execute(
                    """UPDATE openair_dispatch
                          SET status = 'failed', lease_owner = NULL, lease_expires_at = NULL,
                              last_error_code = %s, last_failure_at = clock_timestamp(),
                              updated_at = clock_timestamp()
                        WHERE dispatch_id = %s AND status = 'claimed'
                          AND task_id = %s
                          AND execution_decision_id = %s
                          AND work_unit_id = %s
                          AND idempotency_key = %s
                          AND lease_owner = %s AND attempt_count = %s
                          AND lease_expires_at > clock_timestamp()""",
                    (
                        error_code,
                        fence.dispatch_id,
                        fence.task_id,
                        fence.execution_decision_id,
                        fence.work_unit_id,
                        fence.idempotency_key,
                        fence.lease_owner,
                        fence.attempt_count,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ResearchOrchestrationPersistenceError("dispatch_failure_race")
        return True

    def suppress_dispatches(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int,
        reason_code: str = "task_stopped",
    ) -> int:
        receipt = self.suppress_dispatches_with_receipt(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            expected_task_revision=expected_task_revision,
            reason_code=reason_code,
        )
        return int(receipt.get("suppressedDispatchCount") or 0)

    def suppress_dispatches_with_receipt(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int | None,
        reason_code: str = "task_stopped",
    ) -> dict[str, Any]:
        """Commit stop and its replayable receipt under Task->Dispatch locks."""
        self._require_write_enabled()
        self._require_postgres()
        if expected_task_revision is not None:
            self._require_expected_revision(expected_task_revision)
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                task, raw_payload = self._load_locked_task(
                    cursor,
                    task_id=task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                prior_receipt = raw_payload.get("stopReceipt")
                valid_prior_receipt = (
                    isinstance(prior_receipt, dict)
                    and prior_receipt.get("taskId") == task_id
                    and prior_receipt.get("status") == "stopped"
                    and prior_receipt.get("active") is True
                )
                if valid_prior_receipt:
                    prior_request_revision = prior_receipt.get("requestExpectedRevision")
                    if (
                        expected_task_revision is None
                        or expected_task_revision == task.revision
                        or (
                            type(prior_request_revision) is int
                            and expected_task_revision == prior_request_revision
                        )
                    ):
                        return dict(prior_receipt)
                    raise TaskRevisionConflictError("task_revision_conflict")
                if task.status is not TaskStatus.RUNNING:
                    raise TaskRevisionConflictError(
                        f"task_not_stoppable:{task.status.value}"
                    )
                effective_expected_revision = (
                    task.revision if expected_task_revision is None else expected_task_revision
                )
                if (
                    task.revision != effective_expected_revision
                    or raw_payload.get("revision") != effective_expected_revision
                ):
                    raise TaskRevisionConflictError("task_revision_conflict")
                cursor.execute(
                    """SELECT dispatch_id FROM openair_dispatch
                        WHERE task_id = %s ORDER BY dispatch_id FOR UPDATE""",
                    (task_id,),
                )
                cursor.fetchall()
                cursor.execute(
                    """UPDATE openair_dispatch
                          SET status = 'suppressed', lease_owner = NULL, lease_expires_at = NULL,
                              last_error_code = %s, updated_at = clock_timestamp()
                        WHERE task_id = %s
                          AND (
                              status IN ('pending', 'claimed')
                              OR (
                                  status = 'submission_authorized'
                                  AND external_action_started_at IS NULL
                              )
                          )""",
                    (reason_code, task_id),
                )
                suppressed_count = cursor.rowcount
                committed_at = datetime.now(timezone.utc)
                control_generation = self._latest_lifecycle_control_generation(raw_payload) + 1
                receipt = {
                    "receiptId": f"stop:{task_id}:{task.revision + 1}",
                    "taskId": task_id,
                    "status": "stopped",
                    "active": True,
                    "controlAction": "stop",
                    "controlGeneration": control_generation,
                    "revision": task.revision + 1,
                    "suppressed": True,
                    "suppressedDispatchCount": suppressed_count,
                    "requestExpectedRevision": effective_expected_revision,
                    "reasonCode": reason_code,
                    "committedAt": committed_at.isoformat(),
                }
                stopped_task = self._reconcile_task_lifecycle_with_frozen_snapshot(
                    task,
                    occurred_at=committed_at,
                    lifecycle_control_fact=receipt,
                )
                receipt["taskStatus"] = stopped_task.status.value
                self._write_locked_task(
                    cursor,
                    raw_payload,
                    stopped_task,
                    payload_patch={"stopReceipt": receipt},
                )
                return receipt

    def resume_suppressed_dispatches_with_receipt(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int,
    ) -> dict[str, Any]:
        """Resume the same canonical Task without re-planning or cloning it."""
        self._require_write_enabled()
        self._require_postgres()
        self._require_expected_revision(expected_task_revision)
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                task, raw_payload = self._load_locked_task(
                    cursor,
                    task_id=task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                if task.revision != expected_task_revision or raw_payload.get("revision") != expected_task_revision:
                    raise TaskRevisionConflictError("task_revision_conflict")
                if task.status not in {TaskStatus.RUNNING, TaskStatus.CANCELLED}:
                    raise TaskRevisionConflictError(f'task_not_resumable:{task.status.value}')
                from app.modules.workbench_user_actions import project_continue_research_action

                action = project_continue_research_action({
                    **raw_payload, **task.model_dump(mode="json", by_alias=True),
                })
                if action["executionStopped"] is not True:
                    raise TaskRevisionConflictError("task_not_stopped")
                if action["allowed"] is not True:
                    raise TaskRevisionConflictError("resume_research_not_allowed")
                stop_receipt = raw_payload["stopReceipt"]
                cursor.execute(
                    """SELECT dispatch_id FROM openair_dispatch
                        WHERE task_id = %s ORDER BY dispatch_id FOR UPDATE""",
                    (task_id,),
                )
                cursor.fetchall()
                cursor.execute(
                    """UPDATE openair_dispatch
                          SET status = 'pending', lease_owner = NULL, lease_expires_at = NULL,
                              authorization_task_revision = NULL,
                              next_attempt_at = clock_timestamp(), last_error_code = NULL,
                              updated_at = clock_timestamp()
                        WHERE task_id = %s AND status = 'suppressed'
                          AND last_error_code = 'user_stop_requested'
                          AND external_action_started_at IS NULL""",
                    (task_id,),
                )
                resumed_dispatch_count = cursor.rowcount
                cursor.execute(
                    '''UPDATE openair_dispatch_continuation
                          SET status = 'pending', attempt_count = 0,
                              next_attempt_at = clock_timestamp(),
                              lease_owner = NULL, lease_expires_at = NULL,
                              last_error_code = NULL, completed_at = NULL,
                              updated_at = clock_timestamp()
                        WHERE task_id = %s AND status = 'completed'
                          AND last_error_code = 'task_stopped' ''',
                    (task_id,),
                )
                resumed_continuation_count = cursor.rowcount
                resumed_at = datetime.now(timezone.utc).isoformat()
                next_revision = task.revision + 1
                stop_generation = stop_receipt.get("controlGeneration")
                if type(stop_generation) is not int:
                    stop_generation = max(
                        self._latest_lifecycle_control_generation(raw_payload),
                        1,
                    )
                control_generation = stop_generation + 1
                stop_patch = {
                    **stop_receipt,
                    "status": "resumed",
                    "active": False,
                    "resumedAt": resumed_at,
                    "resumeRevision": next_revision,
                }
                receipt = {
                    "receiptId": f"resume:{task_id}:{next_revision}",
                    "taskId": task_id,
                    "status": "resumed",
                    "controlAction": "resume",
                    "controlGeneration": control_generation,
                    "supersedesControlGeneration": stop_generation,
                    "revision": next_revision,
                    "resumed": True,
                    'resumedDispatchCount': resumed_dispatch_count,
                    'resumedContinuationCount': resumed_continuation_count,
                    "committedAt": resumed_at,
                }
                resumed_task = self._reconcile_task_lifecycle_with_frozen_snapshot(
                    task,
                    occurred_at=datetime.fromisoformat(resumed_at),
                    lifecycle_control_fact=receipt,
                )
                receipt["status"] = resumed_task.status.value
                receipt["taskStatus"] = resumed_task.status.value
                self._write_locked_task(
                    cursor,
                    raw_payload,
                    resumed_task,
                    payload_patch={"stopReceipt": stop_patch, "resumeReceipt": receipt},
                )
                return receipt

    def revalidate_recovery_submission(
        self,
        fence: AuthorizedDispatch,
        *,
        owner_id: str,
        workspace_id: str,
    ) -> str:
        """Recheck authority after a conclusive not-submitted query and before transport."""
        self._require_write_enabled()
        self._require_postgres()
        self._require_authorized_fence(fence)
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                task, raw_payload = self._load_locked_task(
                    cursor,
                    task_id=fence.task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                cursor.execute(
                    """SELECT *, lease_expires_at > clock_timestamp() AS lease_live
                         FROM openair_dispatch
                        WHERE dispatch_id = %s AND task_id = %s
                        FOR UPDATE""",
                    (fence.dispatch_id, fence.task_id),
                )
                row = cursor.fetchone()
                if (
                    row is None
                    or not self._matches_authorized_fence(row, fence)
                    or not row["lease_live"]
                ):
                    return "stale"
                try:
                    work_unit = self._validate_authorization(task, row)
                except DispatchAuthorizationError:
                    reason_code = (
                        "stopped_before_submission"
                        if task.status.value == "cancelled"
                        else "authority_lost_before_submission"
                    )
                    closed_task = self._task_with_cancelled_work_unit(
                        task,
                        fence.work_unit_id,
                        reason_code,
                        datetime.now(timezone.utc),
                    )
                    self._write_locked_task(cursor, raw_payload, closed_task)
                    cursor.execute(
                        """UPDATE openair_dispatch
                              SET status = 'failed', lease_owner = NULL, lease_expires_at = NULL,
                                  last_error_code = %(reason_code)s, updated_at = clock_timestamp()
                            WHERE dispatch_id = %(dispatch_id)s
                              AND task_id = %(task_id)s
                              AND execution_decision_id = %(execution_decision_id)s
                              AND work_unit_id = %(work_unit_id)s
                              AND idempotency_key = %(idempotency_key)s
                              AND status = 'submission_authorized'
                              AND lease_owner = %(lease_owner)s
                              AND attempt_count = %(attempt_count)s
                              AND authorization_task_revision = %(authorization_task_revision)s""",
                        {**fence.__dict__, "reason_code": reason_code},
                    )
                    if cursor.rowcount != 1:
                        raise ResearchOrchestrationPersistenceError("dispatch_recovery_close_race")
                    return "closed"
                if work_unit.model_dump(mode="json", by_alias=True) != fence.work_unit_payload:
                    return "stale"
                return "ready"

    def get_reconcile_result(
        self,
        command: ReconcileExternalResultCommand,
    ) -> dict[str, Any] | None:
        """Return a durable replay before any provider capability check or query."""
        self._require_write_enabled()
        self._require_postgres()
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT canonical_payload_fingerprint, owner_id, workspace_id,
                              resolved_external_state, committed_task_revision
                         FROM openair_external_reconciliation
                        WHERE command_request_id = %s""",
                    (command.command_request_id,),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        if (
            row["canonical_payload_fingerprint"] != command.canonical_payload_fingerprint
            or row["owner_id"] != command.owner_id
            or row["workspace_id"] != command.workspace_id
        ):
            raise ExternalRecoveryConflictError("reconciliation_command_payload_conflict")
        return {
            "status": "recorded",
            "externalState": row["resolved_external_state"],
            "taskRevision": int(row["committed_task_revision"]),
            "replayed": True,
        }

    def prepare_system_reconcile_command(
        self,
        fence: DispatchContinuationFence,
    ) -> ReconcileExternalResultCommand | None:
        return _prepare_system_reconcile_command(
            self,
            fence,
            state_error=ExternalRecoveryStateError,
        )

    def prepare_system_explicit_retry_command(
        self,
        fence: AuthorizedDispatch,
        *,
        owner_id: str,
        workspace_id: str,
        error_code: str,
    ) -> ExplicitRetryCommand | None:
        return _prepare_system_explicit_retry_command(
            self,
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
            error_code=error_code,
        )

    def system_external_recovery_state(
        self,
        fence: DispatchContinuationFence,
    ) -> ExternalExecutionState | None:
        return _system_external_recovery_state(self, fence)

    def load_providerless_recovery_authorization(
        self,
        fence: DispatchContinuationFence,
    ) -> AuthorizedDispatch | None:
        """Return a recovery fence for stored submission_unknown providerless settlement."""
        self._require_postgres()
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT d.dispatch_id, d.task_id, d.execution_decision_id,
                              d.work_unit_id, d.idempotency_key, d.lease_owner,
                              d.attempt_count, d.authorization_task_revision,
                              d.submission_mode, d.external_action_started_at,
                              d.result_storage_type, d.result_storage_scope,
                              d.result_storage_ref, x.capability_call_id,
                              x.attempt_number
                         FROM openair_dispatch AS d
                         JOIN openair_tasks AS t ON t.task_id = d.task_id
                         JOIN openair_external_execution AS x
                           ON x.dispatch_id = d.dispatch_id
                        WHERE d.dispatch_id = %s AND d.task_id = %s
                          AND d.execution_decision_id = %s AND d.work_unit_id = %s
                          AND t.owner_id = %s AND t.workspace_id = %s
                          AND t.status <> 'deleted'
                          AND d.status = 'submission_unknown'
                          AND d.submission_mode = 'single_attempt_no_replay'
                          AND d.external_action_started_at IS NOT NULL
                          AND d.result_storage_ref IS NOT NULL
                          AND d.authorization_task_revision IS NOT NULL
                          AND d.last_error_code IS DISTINCT FROM
                              'invalid_research_orchestration_payload'""",
                    (
                        fence.dispatch_id,
                        fence.task_id,
                        fence.execution_decision_id,
                        fence.work_unit_id,
                        fence.owner_id,
                        fence.workspace_id,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                task = self.load(
                    task_id=fence.task_id,
                    owner_id=fence.owner_id,
                    workspace_id=fence.workspace_id,
                )
                if task is None:
                    return None
                work_unit = next(
                    (item for item in task.work_units if item.work_unit_id == fence.work_unit_id),
                    None,
                )
                if work_unit is None:
                    return None
                work_unit_payload = ControllerCommandRepository.restore_canonical_execution_context(
                    cursor,
                    task_id=fence.task_id,
                    owner_id=fence.owner_id,
                    workspace_id=fence.workspace_id,
                    work_unit_payload=work_unit.model_dump(mode="json", by_alias=True),
                )
        return AuthorizedDispatch(
            dispatch_id=str(row["dispatch_id"]),
            task_id=str(row["task_id"]),
            execution_decision_id=str(row["execution_decision_id"]),
            work_unit_id=str(row["work_unit_id"]),
            idempotency_key=str(row["idempotency_key"]),
            lease_owner=str(row["lease_owner"] or fence.lease_owner),
            attempt_count=int(row["attempt_count"]),
            authorization_task_revision=int(row["authorization_task_revision"]),
            work_unit_payload=work_unit_payload,
            external_attempt=ExternalAttemptIdentity(
                capability_call_id=str(row["capability_call_id"]),
                attempt_number=int(row["attempt_number"]),
            ),
            submission_mode=str(row["submission_mode"]),
            external_action_started_at=row["external_action_started_at"],
            result_storage_type=row.get("result_storage_type"),
            result_storage_scope=row.get("result_storage_scope"),
            result_storage_ref=row.get("result_storage_ref"),
            result_recovery_from_unknown=True,
        )

    def prepare_reconcile(self, command: ReconcileExternalResultCommand) -> FrozenExternalQuery:
        """Phase one: lock Task -> Dispatch, freeze facts, and release before provider I/O."""
        return _prepare_reconcile_query(
            self,
            command,
            task_not_found_error=ResearchTaskNotFoundError,
            revision_conflict_error=TaskRevisionConflictError,
            recovery_not_found_error=ResearchTaskNotFoundError,
            state_error=ExternalRecoveryStateError,
        )

    def commit_reconcile(
        self,
        command: ReconcileExternalResultCommand,
        frozen: FrozenExternalQuery,
        observation: ExternalQueryObservation,
    ) -> dict[str, Any]:
        """Phase two: compare every frozen fact and atomically persist the resolution."""
        self._require_write_enabled()
        self._require_postgres()
        now = datetime.now(timezone.utc)
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT canonical_payload_fingerprint, owner_id, workspace_id,
                              resolved_external_state, committed_task_revision
                         FROM openair_external_reconciliation
                        WHERE command_request_id = %s FOR UPDATE""",
                    (command.command_request_id,),
                )
                replay = cursor.fetchone()
                if replay is not None:
                    if (
                        replay["canonical_payload_fingerprint"] != command.canonical_payload_fingerprint
                        or replay["owner_id"] != command.owner_id
                        or replay["workspace_id"] != command.workspace_id
                    ):
                        raise ExternalRecoveryConflictError("reconciliation_command_payload_conflict")
                    return {
                        "status": "recorded",
                        "externalState": replay["resolved_external_state"],
                        "taskRevision": int(replay["committed_task_revision"]),
                        "replayed": True,
                    }
                task, raw_payload = self._load_locked_task(
                    cursor,
                    task_id=command.task_id,
                    owner_id=command.owner_id,
                    workspace_id=command.workspace_id,
                )
                if task.revision != command.expected_task_revision or task.revision != frozen.expected_task_revision:
                    raise TaskRevisionConflictError("task_revision_conflict")
                cursor.execute(
                    """SELECT d.*, x.capability_call_id, x.attempt_number,
                              x.owner_id, x.workspace_id, x.provider_id, x.adapter,
                              x.provider_idempotency_key, x.receipt,
                              x.remote_run_id AS external_remote_run_id,
                              x.external_state, x.canonical_payload_fingerprint
                         FROM openair_dispatch d
                         JOIN openair_external_execution x ON x.dispatch_id = d.dispatch_id
                        WHERE d.task_id = %s AND d.dispatch_id = %s
                        FOR UPDATE OF d, x""",
                    (command.task_id, command.dispatch_id),
                )
                row = cursor.fetchone()
                self._validate_reconcile_row(task, command, row)
                work_unit = next(item for item in task.work_units if item.work_unit_id == command.work_unit_id)
                current_fingerprint = self._canonical_fingerprint(work_unit.model_dump(mode="json", by_alias=True))
                frozen_facts = (
                    row["authorization_task_revision"],
                    row["provider_id"],
                    row["adapter"],
                    row["provider_idempotency_key"],
                    row["external_remote_run_id"],
                    current_fingerprint,
                )
                expected_facts = (
                    frozen.authorization_task_revision,
                    frozen.provider_id,
                    frozen.adapter,
                    frozen.provider_idempotency_key,
                    frozen.remote_run_id,
                    frozen.frozen_work_unit_fingerprint,
                )
                if frozen_facts != expected_facts:
                    raise ExternalRecoveryConflictError("reconciliation_frozen_fact_conflict")
                all_evidence_refs = tuple(dict.fromkeys((*command.evidence_refs, *observation.result_evidence_refs)))
                self._require_evidence_refs(cursor, command, all_evidence_refs)
                if observation.external_state is ExternalExecutionState.COMPLETED and not observation.result_evidence_refs:
                    raise ExternalRecoveryStateError("completed_result_evidence_required")
                updated_task = self._task_with_reconciliation(task, command, observation, now, raw_payload=raw_payload)
                self._write_locked_task(cursor, raw_payload, updated_task)
                next_revision = task.revision + 1
                dispatch_status = {
                    ExternalExecutionState.SUBMITTED: "submitted",
                    ExternalExecutionState.RUNNING: "submitted",
                    ExternalExecutionState.COMPLETED: "submitted",
                    ExternalExecutionState.KNOWN_FAILED: "failed",
                    ExternalExecutionState.UNKNOWN: "submission_unknown",
                    ExternalExecutionState.NEVER_SUBMITTED: "failed",
                }[observation.external_state]
                cursor.execute(
                    """UPDATE openair_dispatch
                          SET status = %s,
                              remote_run_id = COALESCE(%s, remote_run_id),
                              last_error_code = %s,
                              lease_owner = NULL, lease_expires_at = NULL,
                              updated_at = clock_timestamp()
                        WHERE dispatch_id = %s AND task_id = %s
                          AND execution_decision_id = %s AND work_unit_id = %s
                          AND idempotency_key = %s""",
                    (
                        dispatch_status,
                        observation.remote_run_id,
                        observation.error_code,
                        command.dispatch_id,
                        command.task_id,
                        command.execution_decision_id,
                        command.work_unit_id,
                        frozen.provider_idempotency_key,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ExternalRecoveryConflictError("reconciliation_dispatch_conflict")
                cursor.execute(
                    """UPDATE openair_external_execution
                          SET external_state = %s,
                              remote_run_id = COALESCE(%s, remote_run_id),
                              failure_code = %s,
                              result_evidence_refs = %s,
                              updated_at = clock_timestamp()
                        WHERE dispatch_id = %s AND external_state = %s
                          AND canonical_payload_fingerprint = %s""",
                    (
                        observation.external_state.value,
                        observation.remote_run_id,
                        observation.error_code,
                        list(observation.result_evidence_refs),
                        command.dispatch_id,
                        frozen.external_state.value,
                        row["canonical_payload_fingerprint"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise ExternalRecoveryConflictError("reconciliation_external_state_conflict")
                if observation.external_state is ExternalExecutionState.UNKNOWN:
                    _ensure_unknown_recovery_obligation(
                        self,
                        cursor,
                        dispatch_id=command.dispatch_id,
                        error_code=(
                            observation.error_code or "provider_query_result_unknown"
                        ),
                        persistence_error=ResearchOrchestrationPersistenceError,
                    )
                cursor.execute(
                    """INSERT INTO openair_external_reconciliation (
                           command_request_id, canonical_payload_fingerprint, task_id,
                           execution_decision_id, work_unit_id, capability_call_id,
                           dispatch_id, attempt_number, owner_id, workspace_id, actor_id,
                           resolution, resolved_external_state, expected_task_revision,
                           committed_task_revision, error_code, result_evidence_refs)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                               %s, %s, %s, %s, %s, %s)""",
                    (
                        command.command_request_id,
                        command.canonical_payload_fingerprint,
                        command.task_id,
                        command.execution_decision_id,
                        command.work_unit_id,
                        command.capability_call_id,
                        command.dispatch_id,
                        command.attempt_number,
                        command.owner_id,
                        command.workspace_id,
                        command.actor_id,
                        command.resolution.value,
                        observation.external_state.value,
                        task.revision,
                        next_revision,
                        observation.error_code,
                        list(observation.result_evidence_refs),
                    ),
                )
                for evidence_id in all_evidence_refs:
                    cursor.execute(
                        """INSERT INTO openair_external_reconciliation_evidence
                               (command_request_id, evidence_id) VALUES (%s, %s)""",
                        (command.command_request_id, evidence_id),
                    )
                directive_id = None
                if observation.external_state is ExternalExecutionState.NEVER_SUBMITTED:
                    directive_id = "recovery-" + hashlib.sha256(
                        f"{command.task_id}:{command.command_request_id}".encode("utf-8")
                    ).hexdigest()[:24]
                    selection_request_id = "reconcile:" + hashlib.sha256(
                        f"{command.command_request_id}:selection".encode("utf-8")
                    ).hexdigest()[:32]
                    cursor.execute(
                        """INSERT INTO openair_recovery_directive (
                               directive_id, source_command_request_id, task_id,
                               source_execution_decision_id, source_work_unit_id,
                               resolution_evidence_ref, new_selection_request_id,
                               expected_task_revision, canonical_payload_fingerprint)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (
                            directive_id,
                            command.command_request_id,
                            command.task_id,
                            command.execution_decision_id,
                            command.work_unit_id,
                            command.evidence_refs[0],
                            selection_request_id,
                            next_revision,
                            command.canonical_payload_fingerprint,
                        ),
                    )
        if directive_id is not None:
            return {
                "status": "directive_pending",
                "directiveId": directive_id,
                "taskId": command.task_id,
                "sourceExecutionDecisionId": command.execution_decision_id,
                "sourceWorkUnitId": command.work_unit_id,
                "resolutionEvidenceRef": command.evidence_refs[0],
                "newSelectionRequestId": selection_request_id,
                "expectedTaskRevision": next_revision,
                "canonicalPayloadFingerprint": command.canonical_payload_fingerprint,
                "taskRevision": next_revision,
            }
        return {
            "status": "recorded",
            "externalState": observation.external_state.value,
            "taskRevision": next_revision,
            "replayed": False,
        }

    def get_retry_result(self, command: ExplicitRetryCommand) -> dict[str, Any] | None:
        self._require_write_enabled()
        self._require_postgres()
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT canonical_payload_fingerprint, owner_id, workspace_id,
                              capability_call_id, attempt_number, committed_task_revision
                         FROM openair_external_retry_attempt
                        WHERE command_request_id = %s""",
                    (command.command_request_id,),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        self._validate_retry_replay_scope(command, row)
        return {
            "status": "created",
            "capabilityCallId": row["capability_call_id"],
            "attemptNumber": int(row["attempt_number"]),
            "taskRevision": int(row["committed_task_revision"]),
            "replayed": True,
        }

    def prepare_retry(self, command: ExplicitRetryCommand) -> FrozenExternalQuery:
        self._require_write_enabled()
        self._require_postgres()
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                task, _raw = self._load_locked_task(
                    cursor,
                    task_id=command.task_id,
                    owner_id=command.owner_id,
                    workspace_id=command.workspace_id,
                )
                cursor.execute(
                    """SELECT d.*, x.capability_call_id, x.attempt_number,
                              x.owner_id, x.workspace_id, x.provider_id, x.adapter,
                              x.provider_idempotency_key, x.remote_run_id AS external_remote_run_id,
                              x.external_state, x.canonical_payload_fingerprint
                         FROM openair_dispatch d
                         JOIN openair_external_execution x ON x.dispatch_id = d.dispatch_id
                        WHERE d.task_id = %s AND d.dispatch_id = %s
                        FOR UPDATE OF d, x""",
                    (command.task_id, command.dispatch_id),
                )
                row = cursor.fetchone()
                reasoning_recovery = _no_external_action_retry_proven(self, task, row)
                self._validate_retry_row(
                    task, command, row, task_payload=_raw,
                    proven_no_external_action_retry=reasoning_recovery,
                )
                work_unit = next(item for item in task.work_units if item.work_unit_id == command.work_unit_id)
                return FrozenExternalQuery(
                    task_id=command.task_id,
                    work_unit_id=command.work_unit_id,
                    dispatch_id=command.dispatch_id,
                    execution_decision_id=command.execution_decision_id,
                    capability_call_id=command.prior_capability_call_id,
                    attempt_number=command.prior_attempt_number,
                    expected_task_revision=task.revision,
                    authorization_task_revision=row["authorization_task_revision"],
                    owner_id=command.owner_id,
                    workspace_id=command.workspace_id,
                    provider_id=row["provider_id"],
                    adapter=row["adapter"],
                    provider_idempotency_key=row["provider_idempotency_key"],
                    remote_run_id=row["external_remote_run_id"],
                    external_state=ExternalExecutionState(row["external_state"]),
                    proven_no_external_action_retry=reasoning_recovery,
                    frozen_work_unit_fingerprint=self._canonical_fingerprint(
                        work_unit.model_dump(mode="json", by_alias=True)
                    ),
                )

    def create_retry_attempt(self, command: ExplicitRetryCommand) -> dict[str, Any]:
        """Append one call attempt and requeue the same frozen Dispatch in one UoW."""
        self._require_write_enabled()
        self._require_postgres()
        now = datetime.now(timezone.utc)
        with self._uow.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT canonical_payload_fingerprint, owner_id, workspace_id,
                              capability_call_id, attempt_number, committed_task_revision
                         FROM openair_external_retry_attempt
                        WHERE command_request_id = %s FOR UPDATE""",
                    (command.command_request_id,),
                )
                replay = cursor.fetchone()
                if replay is not None:
                    self._validate_retry_replay_scope(command, replay)
                    return {
                        "status": "created",
                        "capabilityCallId": replay["capability_call_id"],
                        "attemptNumber": int(replay["attempt_number"]),
                        "taskRevision": int(replay["committed_task_revision"]),
                        "replayed": True,
                    }
                task, raw_payload = self._load_locked_task(
                    cursor,
                    task_id=command.task_id,
                    owner_id=command.owner_id,
                    workspace_id=command.workspace_id,
                )
                cursor.execute(
                    """SELECT d.*, x.capability_call_id, x.attempt_number,
                              x.owner_id, x.workspace_id, x.provider_id, x.adapter,
                              x.provider_idempotency_key, x.remote_run_id AS external_remote_run_id,
                              x.external_state, x.canonical_payload_fingerprint
                         FROM openair_dispatch d
                         JOIN openair_external_execution x ON x.dispatch_id = d.dispatch_id
                        WHERE d.task_id = %s AND d.dispatch_id = %s
                        FOR UPDATE OF d, x""",
                    (command.task_id, command.dispatch_id),
                )
                row = cursor.fetchone()
                reasoning_recovery = _no_external_action_retry_proven(self, task, row)
                self._validate_retry_row(
                    task, command, row, task_payload=raw_payload,
                    proven_no_external_action_retry=reasoning_recovery,
                )
                payload = task.model_dump(mode="json", by_alias=True)
                prior = next(
                    (item for item in payload["capabilityCalls"] if item["capabilityCallId"] == command.prior_capability_call_id),
                    None,
                )
                if prior is None:
                    raise ExternalRecoveryStateError("prior_capability_call_missing")
                attempts = [
                    int(item["attemptNumber"])
                    for item in payload["capabilityCalls"]
                    if item["workUnitId"] == command.work_unit_id
                ]
                if not attempts or max(attempts) != command.prior_attempt_number:
                    raise ExternalRecoveryConflictError("retry_attempt_lineage_conflict")
                next_attempt = command.prior_attempt_number + 1
                call_id = "call-retry-" + hashlib.sha256(
                    f"{command.task_id}:{command.command_request_id}".encode("utf-8")
                ).hexdigest()[:24]
                call_key = f"call:{command.work_unit_id}:{prior['capabilityId']}:{next_attempt}"
                if call_key == row["provider_idempotency_key"]:
                    raise ExternalRecoveryStateError("call_provider_idempotency_key_collision")
                next_call = deepcopy(prior)
                next_call.update(
                    {
                        "capabilityCallId": call_id,
                        "idempotencyKey": call_key,
                        "attemptNumber": next_attempt,
                        "status": "queued",
                        "outputArtifactRefs": [],
                        "startedAt": now.isoformat(),
                        "finishedAt": None,
                    }
                )
                payload["capabilityCalls"].append(next_call)
                work_unit = next(item for item in payload["workUnits"] if item["workUnitId"] == command.work_unit_id)
                work_unit.update({"status": "queued", "terminalReasonCode": None, "finishedAt": None})
                self._reopen_closed_blocked_decision_for_known_failed_safe_retry(
                    payload,
                    command=command,
                    occurred_at=now,
                    proven_no_external_action_retry=reasoning_recovery,
                )
                updated_task = ResearchTaskV12.model_validate(payload)
                # Reopening a closed blocked decision already restores the
                # dispatchable runtime slice. A full lifecycle reconcile here
                # can downgrade the queued retry work unit back to blocked.
                self._write_locked_task(cursor, raw_payload, updated_task)
                next_revision = task.revision + 1
                cursor.execute(
                    """UPDATE openair_dispatch
                          SET status = 'pending', authorization_task_revision = NULL,
                              lease_owner = NULL, lease_expires_at = NULL, remote_run_id = NULL,
                              external_action_started_at = NULL,
                              next_attempt_at = NULL, last_error_code = NULL,
                              updated_at = clock_timestamp()
                        WHERE dispatch_id = %s AND task_id = %s AND status = %s
                          AND execution_decision_id = %s AND work_unit_id = %s
                          AND idempotency_key = %s AND remote_run_id IS NULL""",
                    (
                        command.dispatch_id,
                        command.task_id,
                        row["status"],
                        command.execution_decision_id,
                        command.work_unit_id,
                        row["provider_idempotency_key"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise ExternalRecoveryConflictError("retry_dispatch_conflict")
                retry_fingerprint = self._canonical_fingerprint(work_unit)
                self._write_external_attempt(
                    cursor,
                    operation="retry",
                    values={
                        "dispatch_id": command.dispatch_id,
                        "capability_call_id": call_id,
                        "attempt_number": next_attempt,
                        "canonical_payload_fingerprint": retry_fingerprint,
                        "proven_no_external_action_retry": reasoning_recovery,
                    },
                    prior_attempt=ExternalAttemptIdentity(
                        capability_call_id=command.prior_capability_call_id,
                        attempt_number=command.prior_attempt_number,
                    ),
                )
                cursor.execute(
                    """INSERT INTO openair_external_retry_attempt (
                           command_request_id, canonical_payload_fingerprint, task_id,
                           execution_decision_id, work_unit_id, dispatch_id,
                           prior_capability_call_id, capability_call_id, attempt_number,
                           call_idempotency_key, provider_idempotency_key, owner_id,
                           workspace_id, actor_id, reason_code, expected_task_revision,
                           committed_task_revision)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                               %s, %s, %s, %s, %s, %s)""",
                    (
                        command.command_request_id,
                        command.canonical_payload_fingerprint,
                        command.task_id,
                        command.execution_decision_id,
                        command.work_unit_id,
                        command.dispatch_id,
                        command.prior_capability_call_id,
                        call_id,
                        next_attempt,
                        call_key,
                        row["provider_idempotency_key"],
                        command.owner_id,
                        command.workspace_id,
                        command.actor_id,
                        command.reason_code,
                        task.revision,
                        next_revision,
                    ),
                )
        return {
            "status": "created",
            "capabilityCallId": call_id,
            "attemptNumber": next_attempt,
            "taskRevision": next_revision,
            "replayed": False,
        }

    @staticmethod
    def _canonical_fingerprint(value: dict[str, Any]) -> str:
        encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _validate_reconcile_row(
        task: ResearchTaskV12,
        command: ReconcileExternalResultCommand,
        row: dict[str, Any] | None,
    ) -> None:
        _validate_reconcile_row(
            task,
            command,
            row,
            not_found_error=ResearchTaskNotFoundError,
            conflict_error=ExternalRecoveryConflictError,
            state_error=ExternalRecoveryStateError,
        )

    @staticmethod
    def _validate_retry_row(
        task: ResearchTaskV12,
        command: ExplicitRetryCommand,
        row: dict[str, Any] | None,
        *,
        task_payload: dict[str, Any] | None = None,
        proven_no_external_action_retry: bool = False,
    ) -> None:
        if row is None:
            raise ResearchTaskNotFoundError("external_execution_not_found")
        if command.expected_task_revision != task.revision:
            raise TaskRevisionConflictError("task_revision_conflict")
        if (
            row["execution_decision_id"] != command.execution_decision_id
            or row["work_unit_id"] != command.work_unit_id
            or row["capability_call_id"] != command.prior_capability_call_id
            or int(row["attempt_number"]) != command.prior_attempt_number
            or row["owner_id"] != command.owner_id
            or row["workspace_id"] != command.workspace_id
            or row["provider_idempotency_key"] != row["idempotency_key"]
        ):
            raise ExternalRecoveryConflictError("retry_lineage_mismatch")
        from app.modules.research_blocker_semantics import active_user_stop_receipt
        from app.modules.workbench_user_actions import explicit_retry_reason

        stopped = active_user_stop_receipt(
            task.task_id, (task_payload or {}).get("stopReceipt")
        ) is not None
        reason = explicit_retry_reason(
            canonical=task,
            work_unit_id=command.work_unit_id,
            execution_decision_id=command.execution_decision_id,
            prior_capability_call_id=command.prior_capability_call_id,
            prior_attempt_number=command.prior_attempt_number,
            dispatch_status=str(row["status"]),
            external_state=str(row["external_state"]),
            dispatch_remote_run_id=row["remote_run_id"],
            external_remote_run_id=row["external_remote_run_id"],
            last_error_code=str(row.get("last_error_code") or ""),
            execution_stopped=stopped,
            proven_no_external_action_retry=proven_no_external_action_retry,
        )
        if reason is None or command.reason_code != reason:
            raise ExternalRecoveryStateError("retry_requires_proven_safe_outcome")

    @staticmethod
    def _validate_retry_replay_scope(
        command: ExplicitRetryCommand,
        replay: dict[str, Any],
    ) -> None:
        if (
            replay["canonical_payload_fingerprint"] != command.canonical_payload_fingerprint
            or replay["owner_id"] != command.owner_id
            or replay["workspace_id"] != command.workspace_id
        ):
            raise ExternalRecoveryConflictError("retry_command_payload_conflict")

    @staticmethod
    def _require_evidence_refs(
        cursor: psycopg.Cursor[Any],
        command: ReconcileExternalResultCommand,
        evidence_refs: tuple[str, ...],
    ) -> None:
        if not evidence_refs:
            raise ExternalRecoveryStateError("reconciliation_evidence_required")
        cursor.execute(
            """SELECT evidence_id, task_id, execution_decision_id, work_unit_id,
                      capability_call_id, dispatch_id, attempt_number, owner_id, workspace_id
                 FROM openair_external_evidence
                WHERE evidence_id = ANY(%s) FOR SHARE""",
            (list(evidence_refs),),
        )
        rows = cursor.fetchall()
        if len(rows) != len(set(evidence_refs)):
            raise ExternalRecoveryStateError("reconciliation_evidence_missing")
        for row in rows:
            if (
                row["task_id"] != command.task_id
                or row["execution_decision_id"] != command.execution_decision_id
                or row["work_unit_id"] != command.work_unit_id
                or row["capability_call_id"] != command.capability_call_id
                or row["dispatch_id"] != command.dispatch_id
                or int(row["attempt_number"]) != command.attempt_number
                or row["owner_id"] != command.owner_id
                or row["workspace_id"] != command.workspace_id
            ):
                raise ExternalRecoveryConflictError("reconciliation_evidence_lineage_mismatch")

    def _task_with_reconciliation(
        self,
        task: ResearchTaskV12,
        command: ReconcileExternalResultCommand,
        observation: ExternalQueryObservation,
        now: datetime,
        *,
        raw_payload: Mapping[str, Any] | None = None,
    ) -> ResearchTaskV12:
        payload = task.model_dump(mode="json", by_alias=True)
        work_unit = next(item for item in payload["workUnits"] if item["workUnitId"] == command.work_unit_id)
        capability_call = next(
            item for item in payload["capabilityCalls"] if item["capabilityCallId"] == command.capability_call_id
        )
        from app.modules.research_execution_decision_service import resolve_execution_outcome

        known_failed_resolution = (
            resolve_execution_outcome("known_failed")
            if observation.external_state is ExternalExecutionState.KNOWN_FAILED
            else None
        )
        work_status = {
            ExternalExecutionState.SUBMITTED: "running",
            ExternalExecutionState.RUNNING: "running",
            ExternalExecutionState.COMPLETED: "reviewing",
            ExternalExecutionState.KNOWN_FAILED: (
                known_failed_resolution.work_unit_status.value
                if known_failed_resolution is not None
                else "blocked"
            ),
            ExternalExecutionState.UNKNOWN: "external_result_unknown",
            ExternalExecutionState.NEVER_SUBMITTED: "blocked",
        }[observation.external_state]
        call_status = {
            ExternalExecutionState.SUBMITTED: "running",
            ExternalExecutionState.RUNNING: "running",
            ExternalExecutionState.COMPLETED: "succeeded",
            ExternalExecutionState.KNOWN_FAILED: "failed",
            ExternalExecutionState.UNKNOWN: "external_result_unknown",
            ExternalExecutionState.NEVER_SUBMITTED: "failed",
        }[observation.external_state]
        terminal = observation.external_state in {
            ExternalExecutionState.KNOWN_FAILED,
            ExternalExecutionState.UNKNOWN,
            ExternalExecutionState.NEVER_SUBMITTED,
        }
        work_unit.update(
            {
                "status": work_status,
                "terminalReasonCode": observation.error_code,
                "finishedAt": now.isoformat() if terminal else None,
            }
        )
        capability_call.update(
            {
                "status": call_status,
                "finishedAt": None if call_status == "running" else now.isoformat(),
                "outputArtifactRefs": [],
            }
        )
        updated = ResearchTaskV12.model_validate(payload)
        if observation.external_state is ExternalExecutionState.KNOWN_FAILED:
            from app.modules.research_execution_decision_service import (
                DecisionPolicyViolation,
                close_decision_from_durable_facts,
            )

            try:
                updated = close_decision_from_durable_facts(
                    updated,
                    execution_decision_id=command.execution_decision_id,
                    closed_at=now,
                    recoverable_terminal_outcome=None,
                )
            except DecisionPolicyViolation as exc:
                if exc.code != "required_assignment_not_terminal":
                    raise
            else:
                updated_payload = updated.model_dump(mode="json", by_alias=True)
                runtime = next(
                    (
                        row
                        for row in updated_payload["substageRuntimeStates"]
                        if row["substageInstanceId"] == work_unit["substageInstanceId"]
                        and row["instanceRevision"] == work_unit["substageInstanceRevision"]
                    ),
                    None,
                )
                if runtime is not None:
                    runtime["statusReasonCode"] = observation.error_code
                    runtime["updatedAt"] = now.isoformat()
                updated = _project_optional_enhancement_failure_from_raw_payload(
                    ResearchTaskV12.model_validate(updated_payload),
                    execution_decision_id=command.execution_decision_id,
                    occurred_at=now,
                    raw_payload=raw_payload,
                )
        return self._reconcile_task_lifecycle_with_frozen_snapshot(
            updated,
            occurred_at=now,
        )

    def _load_locked_task(
        self,
        cursor: psycopg.Cursor[Any],
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> tuple[ResearchTaskV12, dict[str, Any]]:
        cursor.execute(
            """SELECT payload, revision FROM openair_tasks
                WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                  AND status <> 'deleted'
                FOR UPDATE""",
            (task_id, owner_id, workspace_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise ResearchTaskNotFoundError("research_task_not_found")
        return self._task_from_database_payload(row["payload"], int(row["revision"]))

    @staticmethod
    def _task_from_database_payload(
        payload: dict[str, Any] | str,
        database_revision: int,
    ) -> tuple[ResearchTaskV12, dict[str, Any]]:
        raw_payload = payload if isinstance(payload, dict) else json.loads(payload)
        if raw_payload.get("revision") != database_revision:
            raise PayloadRevisionMismatchError("task_payload_revision_mismatch")
        try:
            task = ResearchTaskV12.model_validate(_extract_contract_payload(raw_payload))
        except (KeyError, ValidationError) as exc:
            raise ResearchOrchestrationPersistenceError("invalid_research_orchestration_payload") from exc
        return task, raw_payload

    @staticmethod
    def _require_expected_revision(expected_task_revision: int) -> None:
        if type(expected_task_revision) is not int or expected_task_revision < 0:
            raise ValueError("expected_task_revision must be a non-negative integer")

    @classmethod
    def _require_authorized_fence(cls, fence: AuthorizedDispatch) -> None:
        if not isinstance(fence, AuthorizedDispatch):
            raise TypeError("recording an outcome requires AuthorizedDispatch")
        cls._require_expected_revision(fence.authorization_task_revision)
        if (
            not fence.external_attempt.capability_call_id.strip()
            or fence.external_attempt.attempt_number < 1
        ):
            raise ValueError("authorized external attempt identity is invalid")

    @staticmethod
    def _persisted_dispatch_work_unit(task: ResearchTaskV12, dispatch: dict[str, Any]):
        work_unit = next(
            (row for row in task.work_units if row.work_unit_id == dispatch["work_unit_id"]),
            None,
        )
        if (
            work_unit is None
            or work_unit.task_id != dispatch["task_id"]
            or work_unit.execution_decision_id != dispatch["execution_decision_id"]
            or work_unit.idempotency_key != dispatch["idempotency_key"]
        ):
            raise ResearchOrchestrationPersistenceError("dispatch_work_unit_identity_mismatch")
        return work_unit

    @staticmethod
    def _matches_fence(row: dict[str, Any], fence: DispatchFence, *, status: DispatchStatus) -> bool:
        return (
            row["status"] == status.value
            and row["lease_owner"] == fence.lease_owner
            and int(row["attempt_count"]) == fence.attempt_count
            and row["task_id"] == fence.task_id
            and row["execution_decision_id"] == fence.execution_decision_id
            and row["work_unit_id"] == fence.work_unit_id
            and row["idempotency_key"] == fence.idempotency_key
        )

    @classmethod
    def _matches_authorized_fence(
        cls,
        row: dict[str, Any],
        fence: AuthorizedDispatch,
    ) -> bool:
        return (
            cls._matches_fence(row, fence, status=DispatchStatus.SUBMISSION_AUTHORIZED)
            and row["authorization_task_revision"] is not None
            and int(row["authorization_task_revision"]) == fence.authorization_task_revision
        )

    @staticmethod
    def _validate_authorization(task: ResearchTaskV12, dispatch: dict[str, Any]):
        if task.status.value != "running":
            raise DispatchAuthorizationError("task_not_running")
        decision = next(
            (row for row in task.execution_decisions if row.execution_decision_id == dispatch["execution_decision_id"]),
            None,
        )
        work_unit = next((row for row in task.work_units if row.work_unit_id == dispatch["work_unit_id"]), None)
        if decision is None or work_unit is None:
            raise DispatchAuthorizationError("dispatch_lineage_mismatch")
        active_ref = task.active_research_design_ref
        design = next(
            (
                row for row in task.research_design_versions
                if active_ref is not None
                and row.research_design_id == active_ref.research_design_id
                and row.version == active_ref.version
            ),
            None,
        )
        if (
            design is None
            or design.status != ResearchDesignStatus.CONFIRMED
            or decision.research_design_version != design.version
        ):
            raise DispatchAuthorizationError("design_not_active_confirmed")
        if not any(
            row.substage_instance_id == decision.substage_instance_id
            and row.instance_revision == decision.substage_instance_revision
            for row in design.instances
        ):
            raise DispatchAuthorizationError("instance_lineage_mismatch")
        active_same_selection = [
            row for row in task.execution_decisions
            if row.selection_request_id == decision.selection_request_id
            and row.status in {ExecutionDecisionStatus.SELECTED, ExecutionDecisionStatus.DISPATCHED}
        ]
        if (
            decision.status != ExecutionDecisionStatus.DISPATCHED
            or len(active_same_selection) != 1
            or active_same_selection[0].execution_decision_id != decision.execution_decision_id
        ):
            raise DispatchAuthorizationError("decision_not_unique_active")
        if (
            work_unit.source_mode != WorkUnitSourceMode.LIVE
            or not work_unit.dispatchable
            or work_unit.status != WorkUnitStatus.QUEUED
            or work_unit.execution_decision_id != decision.execution_decision_id
            or work_unit.research_design_version != decision.research_design_version
            or work_unit.substage_instance_id != decision.substage_instance_id
            or work_unit.substage_instance_revision != decision.substage_instance_revision
            or work_unit.idempotency_key != dispatch["idempotency_key"]
        ):
            raise DispatchAuthorizationError("work_unit_not_dispatchable")
        return work_unit

    @staticmethod
    def _validate_known_failed_safe_retry_authorization(
        task, dispatch, *, proven_no_external_action_retry=False,
    ):
        return _validate_known_failed_safe_retry_authorization(
            task, dispatch,
            authorization_error=DispatchAuthorizationError,
            proven_no_external_action_retry=proven_no_external_action_retry,
        )

    @classmethod
    def _reopen_closed_blocked_decision_for_known_failed_safe_retry(
        cls,
        payload: dict[str, Any],
        *,
        command: ExplicitRetryCommand,
        occurred_at: datetime,
        proven_no_external_action_retry: bool = False,
    ) -> bool:
        decision = next(
            (
                row
                for row in payload["executionDecisions"]
                if row["executionDecisionId"] == command.execution_decision_id
            ),
            None,
        )
        if decision is None:
            raise ExternalRecoveryStateError("retry_execution_decision_missing")
        if decision.get("status") != ExecutionDecisionStatus.CLOSED.value:
            return False
        if decision.get("outcome") != ExecutionDecisionOutcome.BLOCKED.value and not (
            proven_no_external_action_retry
            and decision.get("outcome") == ExecutionDecisionOutcome.SUCCEEDED.value
        ):
            raise ExternalRecoveryStateError("retry_decision_not_closed_blocked")
        work_unit = next(
            (row for row in payload["workUnits"] if row["workUnitId"] == command.work_unit_id),
            None,
        )
        if work_unit is None:
            raise ExternalRecoveryStateError("retry_work_unit_missing")
        decision.update(
            {
                "status": ExecutionDecisionStatus.DISPATCHED.value,
                "outcome": None,
                "closedAt": None,
            }
        )
        runtime = next(
            (
                row
                for row in payload["substageRuntimeStates"]
                if row["substageInstanceId"] == work_unit["substageInstanceId"]
                and row["instanceRevision"] == work_unit["substageInstanceRevision"]
            ),
            None,
        )
        if runtime is None:
            raise ExternalRecoveryStateError("retry_runtime_missing")
        active_work_unit_ids = sorted(
            {
                row["workUnitId"]
                for row in payload["workUnits"]
                if row["executionDecisionId"] == command.execution_decision_id
                and row["status"] in {
                    WorkUnitStatus.QUEUED.value,
                    WorkUnitStatus.RUNNING.value,
                    WorkUnitStatus.REVIEWING.value,
                }
            }
        )
        runtime.update(
            {
                "status": "queued",
                "activeExecutionDecisionId": command.execution_decision_id,
                "workUnitIds": active_work_unit_ids or [command.work_unit_id],
                "statusReasonCode": None,
                "updatedAt": occurred_at.astimezone(timezone.utc).isoformat(),
            }
        )
        return True

    @staticmethod
    def _task_with_running_dispatch_call(
        task: ResearchTaskV12,
        fence: AuthorizedDispatch,
        now: datetime,
    ) -> ResearchTaskV12:
        payload = task.model_dump(mode="json", by_alias=True)
        work = next((row for row in payload["workUnits"] if row["workUnitId"] == fence.work_unit_id), None)
        if work is None:
            raise ResearchOrchestrationPersistenceError("dispatch_work_unit_missing")
        decision = next(
            (row for row in payload["executionDecisions"] if row["executionDecisionId"] == fence.execution_decision_id),
            None,
        )
        if decision is None:
            raise ResearchOrchestrationPersistenceError("dispatch_execution_decision_missing")
        assignment = next(
            (row for row in decision["assignments"] if row["assignmentId"] == work["assignmentId"]),
            None,
        )
        if assignment is None:
            raise ResearchOrchestrationPersistenceError("dispatch_assignment_missing")
        call = next(
            (
                row
                for row in payload["capabilityCalls"]
                if row.get("capabilityCallId")
                == fence.external_attempt.capability_call_id
            ),
            None,
        )
        if call is not None:
            if (
                call.get("workUnitId") != fence.work_unit_id
                or int(call.get("attemptNumber", 0))
                != fence.external_attempt.attempt_number
            ):
                raise ResearchOrchestrationPersistenceError(
                    "dispatch_capability_call_mismatch"
                )
            call.update(
                {
                    "status": "running",
                    "startedAt": now.isoformat(),
                    "finishedAt": None,
                }
            )
            return ResearchTaskV12.model_validate(payload)
        if fence.external_attempt.attempt_number != 1:
            raise ResearchOrchestrationPersistenceError(
                "authorized_retry_capability_call_missing"
            )
        executor_type = work["executorRef"]["executorType"]
        executor_id = work["executorRef"]["executorId"]
        executor_version = work["executorRef"].get("definitionVersion")
        readiness = next(
            (
                row for row in decision["runtimeReadiness"]
                if row["capabilityRef"].get("agentId" if executor_type == "agent" else "skillId") == executor_id
                and row["capabilityRef"].get("definitionVersion") == executor_version
            ),
            None,
        )
        payload["capabilityCalls"].append(
            {
                "capabilityCallId": fence.external_attempt.capability_call_id,
                "idempotencyKey": f"{fence.idempotency_key}:call",
                "attemptNumber": fence.external_attempt.attempt_number,
                "workUnitId": work["workUnitId"],
                "substageInstanceId": work["substageInstanceId"],
                "requestingActorRef": work["responsibleActorRef"],
                "capabilityType": CapabilityType.AGENT_RUNTIME.value if executor_type == "agent" else CapabilityType.SKILL.value,
                "capabilityId": executor_id,
                "runtimeBindingId": None if readiness is None else readiness["runtimeBindingId"],
                "status": "running",
                "inputRefs": [*work["inputArtifactRefs"], *work["inputMemoryRefs"]],
                "outputArtifactRefs": [],
                "startedAt": now.isoformat(),
                "finishedAt": None,
            }
        )
        return ResearchTaskV12.model_validate(payload)

    def _task_with_outcome(
        self,
        task: ResearchTaskV12,
        fence: AuthorizedDispatch | DispatchFence,
        outcome: str,
        error_code: str | None,
        now: datetime,
        *,
        external_action_started: bool,
        raw_payload: Mapping[str, Any] | None = None,
    ) -> ResearchTaskV12:
        payload = task.model_dump(mode="json", by_alias=True)
        work_unit = next((row for row in payload["workUnits"] if row["workUnitId"] == fence.work_unit_id), None)
        if work_unit is None:
            raise ResearchOrchestrationPersistenceError("dispatch_work_unit_missing")
        expected_attempt = (
            fence.external_attempt
            if isinstance(fence, AuthorizedDispatch)
            else initial_external_attempt_identity(fence)
        )
        call = next(
            (
                row
                for row in payload["capabilityCalls"]
                if row.get("capabilityCallId") == expected_attempt.capability_call_id
            ),
            None,
        )
        if call is None and external_action_started:
            raise ResearchOrchestrationPersistenceError("dispatch_capability_call_missing")
        if call is not None and (
            call["workUnitId"] != fence.work_unit_id
            or int(call["attemptNumber"]) != expected_attempt.attempt_number
        ):
            raise ResearchOrchestrationPersistenceError(
                "dispatch_capability_call_mismatch"
            )
        from app.modules.research_execution_decision_service import (
            DecisionPolicyViolation,
            close_decision_from_durable_facts,
            reconcile_task_lifecycle_from_runtime,
            resolve_execution_outcome,
        )

        if outcome in {"running", "completed"}:
            work_unit.update(
                {
                    "status": (
                        WorkUnitStatus.RUNNING.value
                        if outcome == "running"
                        else WorkUnitStatus.REVIEWING.value
                    ),
                    "terminalReasonCode": error_code,
                    "finishedAt": None,
                }
            )
            if call is not None:
                call.update(
                    {
                        "status": "running" if outcome == "running" else "succeeded",
                        "finishedAt": None if outcome == "running" else now.isoformat(),
                        "outputArtifactRefs": [],
                    }
                )
            return self._reconcile_task_lifecycle_with_frozen_snapshot(
                ResearchTaskV12.model_validate(payload),
                occurred_at=now,
            )

        resolution = resolve_execution_outcome(outcome)
        if resolution.system_action == "continue":
            work_unit.update(
                {
                    "status": resolution.work_unit_status.value,
                    "terminalReasonCode": None,
                    "finishedAt": None,
                }
            )
            if call is not None:
                call.update({"status": "running", "finishedAt": None, "outputArtifactRefs": []})
            return self._reconcile_task_lifecycle_with_frozen_snapshot(
                ResearchTaskV12.model_validate(payload),
                occurred_at=now,
            )

        if resolution.system_action == "recover":
            # A known failed execution is a physical fact, not terminal research
            # authority. Error codes remain diagnostics only; they cannot choose
            # failed vs recoverable semantics.
            work_unit.update(
                {
                    "status": resolution.work_unit_status.value,
                    "terminalReasonCode": error_code,
                    "finishedAt": now.isoformat(),
                }
            )
            if call is not None:
                call.update({"status": "failed", "finishedAt": now.isoformat(), "outputArtifactRefs": []})
            candidate = ResearchTaskV12.model_validate(payload)
            try:
                closed = close_decision_from_durable_facts(
                    candidate,
                    execution_decision_id=fence.execution_decision_id,
                    closed_at=now,
                    recoverable_terminal_outcome=resolution.decision_outcome,
                )
            except DecisionPolicyViolation as exc:
                if exc.code == "required_assignment_not_terminal":
                    return candidate
                raise
            recoverable_payload = closed.model_dump(mode="json", by_alias=True)
            runtime = next(
                (
                    row
                    for row in recoverable_payload["substageRuntimeStates"]
                    if row["substageInstanceId"] == work_unit["substageInstanceId"]
                    and row["instanceRevision"] == work_unit["substageInstanceRevision"]
                ),
                None,
            )
            if runtime is not None:
                runtime["statusReasonCode"] = error_code
                runtime["updatedAt"] = now.isoformat()
            projected = _project_optional_enhancement_failure_from_raw_payload(
                ResearchTaskV12.model_validate(recoverable_payload),
                execution_decision_id=fence.execution_decision_id,
                occurred_at=now,
                raw_payload=raw_payload,
            )
            return self._reconcile_task_lifecycle_with_frozen_snapshot(
                projected,
                occurred_at=now,
            )

        work_unit.update(
            {
                "status": resolution.work_unit_status.value,
                "terminalReasonCode": error_code,
                "finishedAt": now.isoformat(),
            }
        )
        if call is not None:
            call.update(
                {
                    "status": "external_result_unknown",
                    "finishedAt": now.isoformat(),
                    "outputArtifactRefs": [],
                }
            )
        return self._reconcile_task_lifecycle_with_frozen_snapshot(
            ResearchTaskV12.model_validate(payload),
            occurred_at=now,
        )

    @staticmethod
    def _dispatch_integrity_blocker_patch(
        raw_payload: Mapping[str, Any],
        *,
        dispatch_id: str,
        execution_decision_id: str,
        work_unit_id: str,
        reason_code: str,
        action_started: bool,
        recorded_at: datetime,
    ) -> dict[str, Any]:
        """Build an idempotent Task-level blocker without dereferencing WorkUnit JSON."""
        blocker_id = f"dispatch-integrity:{dispatch_id}:missing-work-unit"
        existing = raw_payload.get("executionIntegrityBlockers")
        blockers = []
        if isinstance(existing, list):
            blockers = [
                deepcopy(item)
                for item in existing
                if isinstance(item, dict) and item.get("blockerId") != blocker_id
            ]
        blockers.append(
            {
                "blockerId": blocker_id,
                "kind": "dispatch_integrity",
                "status": runtime_blocker_status(reason_code),
                "reasonCode": reason_code,
                "dispatchId": dispatch_id,
                "executionDecisionId": execution_decision_id,
                "workUnitId": work_unit_id,
                "allowedActions": (
                    [] if action_started else ["rebuild_execution_assignment"]
                ),
                "recordedAt": recorded_at.isoformat(),
            }
        )
        blockers.sort(key=lambda item: str(item.get("blockerId") or ""))
        return {"executionIntegrityBlockers": blockers}

    @staticmethod
    def _task_with_cancelled_work_unit(
        task: ResearchTaskV12,
        work_unit_id: str,
        reason_code: str,
        now: datetime,
    ) -> ResearchTaskV12:
        payload = task.model_dump(mode="json", by_alias=True)
        work_unit = next((row for row in payload["workUnits"] if row["workUnitId"] == work_unit_id), None)
        if work_unit is None:
            raise ResearchOrchestrationPersistenceError("dispatch_work_unit_missing")
        if work_unit["status"] in {"queued", "running", "reviewing"}:
            work_unit.update(
                {
                    "status": "cancelled",
                    "terminalReasonCode": reason_code,
                    "finishedAt": now.isoformat(),
                }
            )
        return ResearchTaskV12.model_validate(payload)

    def _write_locked_task(
        self,
        cursor: psycopg.Cursor[Any],
        raw_payload: dict[str, Any],
        task: ResearchTaskV12,
        *,
        payload_patch: Mapping[str, Any] | None = None,
    ) -> None:
        """Persist one runtime-reconciled Task only through the lifecycle boundary."""
        expected_revision = int(raw_payload.get("revision") or 0)
        if task.revision not in {expected_revision, expected_revision + 1}:
            raise TaskRevisionConflictError("task_revision_conflict")
        next_revision = expected_revision + 1
        candidate = project_task_progress(task, revision=next_revision)
        domain_payload = candidate.model_dump(mode="json", by_alias=True)
        if payload_patch:
            domain_payload.update(payload_patch)
        payload = {**raw_payload, **domain_payload}
        task_progress = payload.get("progressProjection")
        progress_status = (
            str(task_progress.get("taskProgress", {}).get("status") or candidate.status.value)
            if isinstance(task_progress, Mapping)
            and isinstance(task_progress.get("taskProgress"), Mapping)
            else candidate.status.value
        )
        owner_id = str(raw_payload.get("ownerId") or raw_payload.get("owner_id") or "").strip()
        workspace_id = str(raw_payload.get("workspaceId") or raw_payload.get("workspace_id") or "").strip()
        if not owner_id or not workspace_id:
            raise ResearchOrchestrationPersistenceError("task_scope_missing")
        try:
            commit_reconciled_task_projection_locked(
                cursor,
                task_row={"revision": expected_revision, "status": raw_payload.get("status")},
                payload=payload,
                task_id=candidate.task_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
                reconciled_status=candidate.status.value,
                reconciled_progress_status=progress_status,
            )
        except TaskLifecycleWriteConflictError as exc:
            raise TaskRevisionConflictError("task_revision_conflict") from exc
        self._sync_artifact_lineage(cursor, candidate)
        if candidate.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            owner_id = str(raw_payload.get("ownerId") or "").strip()
            workspace_id = str(raw_payload.get("workspaceId") or "").strip()
            if owner_id and workspace_id:
                ControllerCommandRepository.reject_canonical_for_terminal_task(
                    cursor,
                    task_id=candidate.task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )

    @staticmethod
    def _sync_artifact_lineage(cursor: psycopg.Cursor[Any], task: ResearchTaskV12) -> None:
        for work_unit in task.work_units:
            cursor.execute(
                """INSERT INTO openair_work_unit_lineage (
                       task_id, work_unit_id, execution_decision_id, substage_instance_id,
                       source_mode, dispatchable, migration_key
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (
                    task.task_id,
                    work_unit.work_unit_id,
                    work_unit.execution_decision_id,
                    work_unit.substage_instance_id,
                    work_unit.source_mode.value,
                    work_unit.dispatchable,
                    f"20260730_05:work_unit:{task.task_id}:{work_unit.work_unit_id}",
                ),
            )
        for call in task.capability_calls:
            cursor.execute(
                """INSERT INTO openair_capability_call_lineage (
                       task_id, capability_call_id, work_unit_id, substage_instance_id,
                       source_mode, migration_key
                   ) VALUES (%s, %s, %s, %s, 'live', %s)
                   ON CONFLICT DO NOTHING""",
                (
                    task.task_id,
                    call.capability_call_id,
                    call.work_unit_id,
                    call.substage_instance_id,
                    f"20260730_05:call:{task.task_id}:{call.capability_call_id}",
                ),
            )
        work_units = {row.work_unit_id: row for row in task.work_units}
        for artifact in task.artifacts:
            work_unit = work_units[artifact.work_unit_id]
            cursor.execute(
                """INSERT INTO openair_artifact_lineage (
                       task_id, artifact_id, work_unit_id, execution_decision_id,
                       substage_instance_id, source_capability_call_id,
                       direct_work_unit_output, source_mode, migration_key
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'live', %s)
                   ON CONFLICT DO NOTHING""",
                (
                    task.task_id,
                    artifact.artifact_id,
                    artifact.work_unit_id,
                    work_unit.execution_decision_id,
                    artifact.substage_instance_id,
                    artifact.source_capability_call_id,
                    artifact.source_capability_call_id is None,
                    f"20260730_05:artifact:{task.task_id}:{artifact.artifact_id}",
                ),
            )
            cursor.execute(
                """INSERT INTO openair_artifact_lineage_audit (
                       audit_key, task_id, artifact_id, migration_key
                   ) VALUES (%s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (
                    f"20260730_05:artifact:{task.task_id}:{artifact.artifact_id}:backfill",
                    task.task_id,
                    artifact.artifact_id,
                    f"20260730_05:artifact:{task.task_id}:{artifact.artifact_id}",
                ),
            )

    def _require_write_enabled(self) -> None:
        if not bool(getattr(self._settings, "research_orchestration_v1", False)):
            raise FeatureDisabledError("OPENAIR_RESEARCH_ORCHESTRATION_V1 is disabled")

    def _require_postgres(self) -> None:
        if str(self._settings.persistence_backend).strip().lower() != "postgres":
            raise PersistenceBackendUnsupportedError(
                "research orchestration durable persistence requires PostgreSQL"
            )

    @staticmethod
    def _validate_pending_dispatches(task: ResearchTaskV12, records: list[DispatchRecord]) -> None:
        work_units = {row.work_unit_id: row for row in task.work_units}
        decisions = {row.execution_decision_id: row for row in task.execution_decisions}
        identities: set[str] = set()
        work_unit_ids: set[str] = set()
        idempotency_keys: set[str] = set()
        for record in records:
            record.assert_pending_insert()
            if record.task_id != task.task_id:
                raise ValueError("DispatchRecord.taskId must match ResearchTaskV12.taskId")
            decision = decisions.get(record.execution_decision_id)
            if decision is None:
                raise ValueError("DispatchRecord references an unknown ExecutionDecision")
            if decision.status != ExecutionDecisionStatus.DISPATCHED:
                raise ValueError("pending DispatchRecord requires a dispatched ExecutionDecision")
            active_same_selection = [
                row
                for row in task.execution_decisions
                if row.selection_request_id == decision.selection_request_id
                and row.status in {ExecutionDecisionStatus.SELECTED, ExecutionDecisionStatus.DISPATCHED}
            ]
            if len(active_same_selection) != 1:
                raise ValueError("pending DispatchRecord requires one active Decision per selection identity")
            work_unit = work_units.get(record.work_unit_id)
            if work_unit is None or not work_unit.dispatchable:
                raise ValueError("DispatchRecord references an unknown or non-dispatchable WorkUnit")
            if work_unit.source_mode != WorkUnitSourceMode.LIVE or work_unit.status != WorkUnitStatus.QUEUED:
                raise ValueError("pending DispatchRecord requires a live queued WorkUnit")
            if work_unit.execution_decision_id != record.execution_decision_id:
                raise ValueError("DispatchRecord and WorkUnit Decision identities disagree")
            if work_unit.idempotency_key != record.idempotency_key:
                raise ValueError("DispatchRecord and WorkUnit idempotency keys disagree")
            if record.dispatch_id in identities:
                raise ValueError("duplicate dispatchId")
            if record.work_unit_id in work_unit_ids:
                raise ValueError("duplicate DispatchRecord.workUnitId")
            if record.idempotency_key in idempotency_keys:
                raise ValueError("duplicate DispatchRecord.idempotencyKey")
            identities.add(record.dispatch_id)
            work_unit_ids.add(record.work_unit_id)
            idempotency_keys.add(record.idempotency_key)


def _extract_contract_payload(payload: dict[str, Any]) -> dict[str, Any]:
    aliases = {
        field.alias or name
        for name, field in ResearchTaskV12.model_fields.items()
    }
    # Skip aliases absent from the payload instead of raising KeyError. Fields added
    # to ResearchTaskV12 after a task was persisted (e.g. finalResultAuthority) are
    # Optional with defaults; legacy payloads simply lack them and must still load.
    contract = {alias: payload[alias] for alias in aliases if alias in payload}
    confirmations = contract.get("confirmations")
    if not isinstance(confirmations, list) or not confirmations:
        return contract
    if all(isinstance(row, dict) and "confirmationId" in row for row in confirmations):
        return contract

    actor_id = str(payload.get("createdBy") or payload.get("ownerId") or "").strip()
    recovered: list[dict[str, Any]] = []
    for row in confirmations:
        if not isinstance(row, dict) or set(row) != {"id", "stage", "decision", "note", "createdAt"}:
            return contract
        match = re.fullmatch(r"(research_design):(.+):v([1-9][0-9]*)", str(row.get("note") or ""))
        decision = "request_revision" if row.get("decision") == "needs_revision" else row.get("decision")
        if match is None or decision not in {"approved", "rejected", "request_revision"} or not actor_id:
            return contract
        recovered.append({
            "confirmationId": row.get("id"),
            "targetType": match.group(1),
            "targetId": match.group(2),
            "targetVersion": int(match.group(3)),
            "decision": decision,
            "actorId": actor_id,
            "createdAt": row.get("createdAt"),
        })
    contract["confirmations"] = recovered
    return contract


def _persisted_domain_payload(task: ResearchTaskV12) -> dict[str, Any]:
    """Keep canonical aliases authoritative in storage while retaining legacy reads."""
    canonical = task.model_dump(mode="json", by_alias=True)
    compatibility = _research_design_compatibility_projection(task)
    canonical.update({
        key: value
        for key, value in compatibility.items()
        if key not in canonical
    })
    return canonical


def _research_design_skill_pack(design: Any) -> str:
    """Derive compatibility skillPack from frozen ResearchDesign definition identity."""
    namespaces = {
        definition_id.partition(":")[0]
        for row in design.instances
        if (definition_id := str(row.substage_definition_id).strip())
        and ":" in definition_id
        and definition_id.partition(":")[0]
    }
    if len(namespaces) == 1:
        return next(iter(namespaces))
    return "sdr"


def _research_design_compatibility_projection(task: ResearchTaskV12) -> dict[str, Any]:
    """Project draft design facts for legacy Workbench rendering only."""
    if not task.research_design_versions:
        return {}
    design = max(task.research_design_versions, key=lambda row: row.version)
    from app.modules import pack_config

    skill_pack = _research_design_skill_pack(design)
    display_overrides = {
        "reproducibility": "可复现性设计",
        "report_generation": "报告生成",
    }
    final_objective_en = "Assemble the completed research into the canonical final result."
    final_objective_zh = "汇总已完成的研究结果，形成最终研究报告"

    def _label(substage_key: str) -> str:
        return display_overrides.get(substage_key) or pack_config.get_substage_label(substage_key, pack=skill_pack) or substage_key

    def _objective(substage_key: str, objective: str | None) -> str:
        text = str(objective or "").strip()
        if text == final_objective_en or (
            substage_key == "report_generation" and "canonical final result" in text.lower()
        ):
            return final_objective_zh
        return text

    stages: list[dict[str, Any]] = []
    for stage_key in (
        "problem_modeling",
        "solution_design",
        "execution_validation",
        "insight_generation",
    ):
        instances = sorted(
            (row for row in design.instances if row.stage_key.value == stage_key),
            key=lambda row: (
                row.sequence_hint if row.sequence_hint is not None else 1_000_000,
                row.substage_instance_id,
            ),
        )
        substages = [
            {
                "instanceId": row.substage_instance_id,
                "substage": (substage_key := row.substage_definition_id.split(":", 1)[-1]),
                "label": _label(substage_key),
                "action": _objective(substage_key, row.objective),
                "expectedArtifactTypes": list(row.expected_artifact_types),
                "expectedArtifact": row.expected_artifact_types[0],
            }
            for row in instances
        ]
        stages.append(
            {
                "stageKey": stage_key,
                "description": None,
                "subStages": [row["substage"] for row in substages],
                "substages": substages,
            }
        )
    coverage = design.coverage_report
    from app.modules.research_design_coverage_confirmation import (
        coverage_allows_confirmation,
    )

    ready = coverage_allows_confirmation(coverage)
    creation_status = {
        "draft": "awaiting_confirmation" if ready else "revision_requested",
        "confirmed": "confirmed",
        "cancelled": "cancelled",
        "superseded": "confirmed",
    }[design.status.value]
    stage_order = {
        stage: index
        for index, stage in enumerate(
            (
                "problem_modeling",
                "solution_design",
                "execution_validation",
                "insight_generation",
            )
        )
    }
    designs_by_ref = {
        (row.research_design_id, row.version): row
        for row in task.research_design_versions
    }
    confirmations = []
    for row in task.confirmations:
        target_design = designs_by_ref.get((row.target_id, row.target_version))
        stage = min(
            (instance.stage_key.value for instance in target_design.instances),
            key=stage_order.__getitem__,
            default="problem_modeling",
        ) if target_design is not None else "problem_modeling"
        confirmations.append(
            {
                "id": row.confirmation_id,
                "stage": stage,
                "decision": (
                    "needs_revision" if row.decision == "request_revision" else row.decision
                ),
                "note": f"{row.target_type}:{row.target_id}:v{row.target_version}",
                "createdAt": row.created_at.isoformat(),
            }
        )
    return {
        "creationStatus": creation_status,
        "confirmations": confirmations,
        "creationPlan": {
            "goalSummary": design.goal,
            "domain": "research_task",
            "skillPack": _research_design_skill_pack(design),
            "researchType": design.understanding.research_type,
            "planningSource": "research_design_v1_2",
            "understanding": design.understanding.model_dump(mode="json", by_alias=True),
            "clarifications": list(design.understanding.missing_inputs),
            "stages": stages,
            "readyToConfirm": ready,
        },
    }


def adapt_legacy_task(task: dict[str, Any]) -> LegacyTaskProjection:
    """Build a deterministic, read-only and permanently non-executable projection."""
    source = deepcopy(task)
    task_id = str(source.get("taskId") or "").strip()
    if not task_id:
        raise ValueError("legacy taskId is required")

    substages: list[LegacySubstageProjection] = []
    known_instance_ids: set[str] = set()
    for stage in source.get("stageDetails") or []:
        if not isinstance(stage, dict):
            continue
        stage_key = str(stage.get("stageKey") or "").strip()
        for index, substage in enumerate(stage.get("subStages") or []):
            if not isinstance(substage, dict):
                continue
            substage_key = str(
                substage.get("substage") or substage.get("subStageKey") or substage.get("key") or ""
            ).strip()
            if not stage_key or not substage_key:
                continue
            sequence = substage.get("sequence")
            sequence = index if sequence is None else int(sequence)
            instance_id = str(substage.get("subStageInstanceId") or "").strip()
            if not instance_id:
                digest_input = f"{task_id}:{stage_key}:{sequence}:{substage_key}".encode("utf-8")
                instance_id = f"sub_legacy:{hashlib.sha256(digest_input).hexdigest()[:16]}"
            if instance_id in known_instance_ids:
                raise ValueError("duplicate legacy substageInstanceId")
            known_instance_ids.add(instance_id)
            substages.append(
                LegacySubstageProjection(
                    substageInstanceId=instance_id,
                    stageKey=stage_key,
                    substageKey=substage_key,
                    sequence=sequence,
                )
            )

    work_units: dict[str, LegacyWorkUnitProjection] = {}
    for call in source.get("capabilityCalls") or []:
        if not isinstance(call, dict):
            continue
        call_id = str(call.get("capabilityCallId") or call.get("id") or "").strip()
        if not call_id:
            continue
        work_unit_id = f"wu_legacy:{call_id}"
        work_units[work_unit_id] = _legacy_work_unit_projection(
            work_unit_id=work_unit_id,
            source_capability_call_id=call_id,
            artifact_id=None,
            source=call,
        )

    for artifact in source.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        artifact_id = str(artifact.get("artifactId") or artifact.get("id") or "").strip()
        source_call_id = str(artifact.get("sourceCapabilityCallId") or "").strip() or None
        if not artifact_id and source_call_id:
            artifact_id = _legacy_artifact_id(task_id, source_call_id)
        identity = source_call_id or artifact_id
        if not identity:
            continue
        work_unit_id = f"wu_legacy:{identity}"
        if work_unit_id in work_units:
            existing = work_units[work_unit_id]
            if existing.artifact_id is None:
                work_units[work_unit_id] = LegacyWorkUnitProjection(
                    workUnitId=existing.work_unit_id,
                    sourceCapabilityCallId=existing.source_capability_call_id,
                    artifactId=artifact_id,
                    substageInstanceId=existing.substage_instance_id,
                    responsibleActorRef=existing.responsible_actor_ref,
                    executorRef=existing.executor_ref,
                    sourceMode=existing.source_mode,
                    dispatchable=existing.dispatchable,
                )
            continue
        work_units[work_unit_id] = _legacy_work_unit_projection(
            work_unit_id=work_unit_id,
            source_capability_call_id=source_call_id,
            artifact_id=artifact_id or None,
            source=artifact,
        )

    return LegacyTaskProjection(
        taskId=task_id,
        researchDesignRef=ResearchDesignRef(researchDesignId=f"rd_legacy:{task_id}", version=1),
        substageInstances=substages,
        workUnits=list(work_units.values()),
        collaborationNodes=[],
    )


def _legacy_artifact_id(task_id: str, source_capability_call_id: str) -> str:
    digest_input = f"{task_id}:{source_capability_call_id}".encode("utf-8")
    return f"artifact_legacy:{hashlib.sha256(digest_input).hexdigest()[:16]}"


def _legacy_work_unit_projection(
    *,
    work_unit_id: str,
    source_capability_call_id: str | None,
    artifact_id: str | None,
    source: dict[str, Any],
) -> LegacyWorkUnitProjection:
    actor = _explicit_actor(source.get("responsibleActorRef"))
    executor = _explicit_executor(source.get("executorRef"))
    substage_instance_id = str(source.get("subStageInstanceId") or "").strip() or None
    return LegacyWorkUnitProjection(
        workUnitId=work_unit_id,
        sourceCapabilityCallId=source_capability_call_id,
        artifactId=artifact_id,
        substageInstanceId=substage_instance_id,
        responsibleActorRef=actor,
        executorRef=executor,
        sourceMode="legacy_projection",
        dispatchable=False,
    )


def _explicit_actor(value: object) -> ActorRef:
    if isinstance(value, dict):
        try:
            return ActorRef.model_validate(value)
        except ValidationError:
            pass
    return ActorRef(actorType="orchestrator", actorId="legacy_unknown", definitionVersion=None)


def _explicit_executor(value: object) -> ExecutorRef:
    if isinstance(value, dict):
        try:
            return ExecutorRef.model_validate(value)
        except ValidationError:
            pass
    return ExecutorRef(executorType="legacy_unknown", executorId="legacy_unknown", definitionVersion=None)


__all__ = [
    "AuthorizedDispatch",
    "DispatchAuthorizationError",
    "DispatchFence",
    "ExternalAttemptIdentity",
    "DispatchIdentityConflictError",
    "ExecutionContinuationDirective",
    "FeatureDisabledError",
    "PayloadRevisionMismatchError",
    "PersistenceBackendUnsupportedError",
    "ResearchOrchestrationPersistenceError",
    "ResearchOrchestrationRepository",
    "ResearchOrchestrationUnitOfWork",
    "ResearchTaskNotFoundError",
    "SelectionRequestPayloadConflictError",
    "TaskRevisionConflictError",
    "adapt_legacy_task",
]
