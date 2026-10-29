"""Minimal independently launched durable worker.

The worker owns provider, Skill, and multi-agent execution. HTTP remains only
for fenced lease/callback and artifact-transfer protocol operations.
"""

from __future__ import annotations

import base64
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import hashlib
import json
import logging
import math
import os
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from fastapi import HTTPException
import psycopg
from psycopg import OperationalError
from psycopg.rows import dict_row

from app.config import settings
from app.execution.research_dispatch_provider import (
    DispatchProviderPermanentPreflightError,
    DispatchProviderRetryablePreflightError,
    RegistryDispatchProviderFactory,
    ResearchDispatchProvider,
)
from app.execution.research_dispatch_skill_provider import DirectSkillResearchDispatchProvider
from app.execution.research_dispatch_huiyan_provider import HuiyanResearchDispatchProvider
from app.execution.durable_skill_runtime import resolve_registered_durable_skill_runtime
from app.execution.research_dispatch_worker import process_authorized_recovery, process_dispatch_claim
from app.execution.research_start_worker_runtime import (
    ResearchStartDeterministicTestProvider,
    ResearchStartOneShotProvider,
    process_research_start_claim,
)
from app.execution.model_job_worker import (
    process_model_job_once,
)
from app.execution.durable_worker_runtime import process_durable_worker_run
from app.modules.research_start_repository import ResearchStartRepository
from app.modules.registry_snapshot_repository import (
    RegistrySnapshotIntegrityError,
    execution_snapshot_matches_planning,
)
from app.modules.research_dispatch_service import ResearchDispatchService
from app.modules.research_orchestration_repository import (
    ResearchOrchestrationPersistenceError,
    ResearchOrchestrationRepository,
)


API_BASE = os.environ.get("OPENAIR_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.environ.get("OPENAIR_WORKER_AUTH_TOKEN", "")
WORKER_ID = os.environ.get("OPENAIR_DURABLE_WORKER_ID", f"{socket.gethostname()}-{os.getpid()}")
POLL_SECONDS = float(os.environ.get("OPENAIR_DURABLE_WORKER_POLL_SECONDS", "1"))
PAUSE_AFTER_CLAIM_SECONDS = float(os.environ.get("OPENAIR_DURABLE_WORKER_PAUSE_AFTER_CLAIM_SECONDS", "0"))
PAUSE_AFTER_EXECUTION_STARTED_SECONDS = float(os.environ.get("OPENAIR_DURABLE_WORKER_PAUSE_AFTER_EXECUTION_STARTED_SECONDS", "0"))
EXIT_ON_HTTP_ERROR = os.environ.get("OPENAIR_DURABLE_WORKER_EXIT_ON_HTTP_ERROR", "").strip().lower() in {"1", "true", "yes"}
RENEW_SECONDS = float(os.environ.get("OPENAIR_DURABLE_WORKER_RENEW_SECONDS", "10"))
ORCHESTRATE_TIMEOUT_SECONDS = float(os.environ.get("OPENAIR_DURABLE_ORCHESTRATE_TIMEOUT_SECONDS", "300"))
EXECUTION_CAPACITY = int(
    os.environ.get('OPENAIR_DURABLE_WORKER_EXECUTION_CAPACITY', '8')
)
MAX_INFLIGHT_PER_TASK = int(
    os.environ.get(
        'OPENAIR_DURABLE_WORKER_MAX_INFLIGHT_PER_TASK',
        str(max(1, EXECUTION_CAPACITY - 1)),
    )
)
logger = logging.getLogger(__name__)


def _dispatch_review_contract_is_valid(
    decision: Any,
    *,
    assignment: Any,
    reviewers: list[Any],
) -> bool:
    """Validate the frozen completion review topology used by dispatch.

    Schema-validated canonical OT structured Skills explicitly opt out of an
    independent Reviewer.  That is distinct from a malformed review contract:
    review-required decisions still need exactly one valid Reviewer.
    """
    policy = decision.completion_policy
    if policy is None:
        return False
    reviewer_ids = list(policy.reviewer_assignment_ids or [])
    if policy.review_requirement.value == "none":
        return not reviewers and not reviewer_ids
    if len(reviewers) != 1 or reviewer_ids != [reviewers[0].assignment_id]:
        return False
    primaries = [item for item in decision.assignments if item.role.value == "primary"]
    reviewed_assignment_id = (
        primaries[-1].assignment_id
        if decision.strategy.value == "skill_composition" and primaries
        else assignment.assignment_id
    )
    return reviewers[0].dependency_assignment_ids == [reviewed_assignment_id]


def _execution_method_assignment_is_valid(
    decision: Any, assignment: Any, work_unit: Any, snapshot: Mapping[str, Any]
) -> bool:
    if decision.strategy.value != "execution_method":
        return (
            getattr(decision, "execution_method_ref", None) is None
            and getattr(assignment, "execution_method_role_id", None) is None
            and getattr(assignment, "execution_method_role_control", None) is None
            and getattr(work_unit, "execution_method_ref", None) is None
            and getattr(work_unit, "execution_method_role_id", None) is None
            and getattr(work_unit, "execution_method_role_control", None) is None
        )
    method_ref = getattr(decision, "execution_method_ref", None)
    if not isinstance(method_ref, Mapping):
        return False
    definitions = [
        item
        for item in snapshot.get("definitions", {}).get("substages", [])
        if item.get("definitionId") == work_unit.substage_definition_id
        and item.get("definitionVersion") == work_unit.substage_definition_version
    ]
    method = definitions[0].get("executionMethod") if len(definitions) == 1 else None
    if not isinstance(method, Mapping) or method_ref != {
        "methodId": method.get("methodId"),
        "methodVersion": method.get("methodVersion"),
    }:
        return False
    role_id = getattr(assignment, "execution_method_role_id", None)
    roles = [item for item in method.get("roles") or [] if item.get("roleId") == role_id]
    if len(roles) != 1:
        return False
    role = roles[0]
    capability_kind = assignment.executor_ref.executor_type
    if capability_kind not in (role.get("capabilityKinds") or []):
        return False
    role_ref = f"{method.get('methodId')}:{role_id}"
    capability_rows = [
        item
        for item in snapshot.get("definitions", {}).get(
            "skills" if capability_kind == "skill" else "agents", []
        )
        if item.get("skillId" if capability_kind == "skill" else "agentId")
        == assignment.executor_ref.executor_id
        and item.get("definitionVersion") == assignment.executor_ref.definition_version
    ]
    if len(capability_rows) != 1 or role_ref not in (capability_rows[0].get("executionMethodRoles") or []):
        return False
    iteration = getattr(assignment, "execution_method_iteration", None) or 1
    assignments_by_role: dict[str, list[Any]] = {}
    for candidate_assignment in decision.assignments:
        candidate_role = getattr(candidate_assignment, "execution_method_role_id", None)
        if candidate_role is not None:
            assignments_by_role.setdefault(candidate_role, []).append(candidate_assignment)
    for rows in assignments_by_role.values():
        rows.sort(key=lambda row: getattr(row, "execution_method_iteration", None) or 1)
    transitions = [
        row
        for row in method.get("reentryTransitions") or []
        if role_id in (row.get("repeatRoleIds") or [])
    ]
    if len(transitions) > 1:
        return False
    transition = transitions[0] if transitions else None
    repeat_roles = set(transition.get("repeatRoleIds") or []) if transition else set()
    expected_dependencies: list[str] = []
    for dependency in role.get("dependsOn") or []:
        rows = assignments_by_role.get(dependency, [])
        if dependency in repeat_roles:
            resolved = next(
                (
                    row
                    for row in rows
                    if (getattr(row, "execution_method_iteration", None) or 1) == iteration
                ),
                None,
            )
        else:
            eligible = [
                row
                for row in rows
                if (getattr(row, "execution_method_iteration", None) or 1) <= iteration
            ]
            resolved = eligible[-1] if eligible else None
        if resolved is not None:
            expected_dependencies.append(resolved.assignment_id)
    if (
        transition is not None
        and iteration > 1
        and transition.get("restartRoleId") == role_id
    ):
        prior = next(
            (
                row
                for row in assignments_by_role.get(
                    str(transition.get("afterRoleId") or ""), []
                )
                if (getattr(row, "execution_method_iteration", None) or 1)
                == iteration - 1
            ),
            None,
        )
        if prior is None:
            return False
        if prior.assignment_id not in expected_dependencies:
            expected_dependencies.append(prior.assignment_id)
    stop_conditions = [
        dict(condition)
        for condition in method.get("stopConditions") or []
        if condition.get("afterRoleId") == role_id
    ]
    expected_control = {
        "requirement": role.get("requirement"),
        "releasePolicy": dict(role.get("releasePolicy") or {}),
        "failurePolicy": role.get("failurePolicy"),
        "parallelism": role.get("parallelism"),
        "stopConditionIds": [str(item.get("conditionId") or "") for item in stop_conditions],
        "stopConditions": stop_conditions,
        "qualityConstraints": list(method.get("qualityConstraints") or []),
    }
    outgoing_reentry = [
        dict(item)
        for item in method.get("reentryTransitions") or []
        if item.get("afterRoleId") == role_id
    ]
    if outgoing_reentry:
        expected_control["reentryTransitions"] = outgoing_reentry
    work_iteration = getattr(work_unit, "execution_method_iteration", None) or 1
    return bool(
        assignment.dependency_assignment_ids == expected_dependencies
        and getattr(assignment, "execution_method_role_control", None) == expected_control
        and getattr(work_unit, "execution_method_ref", None) == method_ref
        and getattr(work_unit, "execution_method_role_id", None) == role_id
        and work_iteration == iteration
        and getattr(work_unit, "execution_method_role_control", None) == expected_control
    )


def _setup_worker_logging() -> None:
    """Ensure worker module loggers reach stdout (docker compose logs).

    The worker historically relied on ``print(json.dumps(...))`` for visibility.
    Module loggers (dispatch / provider / orchestrator) were effectively silent
    because no handler was configured for the process.
    """
    level_name = (os.environ.get("OPENAIR_LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("openair").setLevel(level)
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@dataclass(frozen=True)
class DispatchProviderBinding:
    provider_id: str | None
    dispatch_id: str
    task_id: str
    execution_decision_id: str
    work_unit_id: str
    registry_snapshot_ref: Mapping[str, Any]
    executor_ref: Mapping[str, Any]
    responsible_actor_ref: Mapping[str, Any]
    owner_id: str
    workspace_id: str
    expected_task_revision: int
    executor_type: str = "agent"
    source_binding_refs: tuple[tuple[str, int], ...] = ()
    skill_definition: Mapping[str, Any] | None = None
    providerless_recovery: bool = False


class DispatchProviderBindingResolver(Protocol):
    def resolve(self) -> DispatchProviderBinding | None: ...


class _DispatchResolutionSettlementFailure(RuntimeError):
    """Marks a failed settlement so resolve() never invokes it a second time."""


class UnresolvedDispatchProviderBindingResolver:
    """T04 fail-closed implementation until T03/T05 provide durable binding authority."""

    def resolve(self) -> DispatchProviderBinding | None:
        return None


class PostgresDispatchProviderBindingResolver:
    """Cold-start resolver for one exact frozen Agent dispatch binding.

    The resolver is a read-side scheduler only.  T04 claim/fence remains the
    final execution uniqueness authority.  Invalid or incomplete rows are
    skipped and never cause provider construction or transport.
    """

    def __init__(
        self,
        *,
        repository: ResearchOrchestrationRepository | None = None,
        now: Callable[[], datetime] | None = None,
        min_pending_age_seconds: int = 0,
        max_inflight_per_task: int = MAX_INFLIGHT_PER_TASK,
    ) -> None:
        if type(min_pending_age_seconds) is not int or min_pending_age_seconds < 0:
            raise ValueError('min_pending_age_seconds must be a non-negative integer')
        if type(max_inflight_per_task) is not int or max_inflight_per_task < 1:
            raise ValueError('max_inflight_per_task must be a positive integer')
        self._repository = repository or ResearchOrchestrationRepository()
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._min_pending_age_seconds = min_pending_age_seconds
        self._max_inflight_per_task = max_inflight_per_task

    @staticmethod
    def _retry_delay_seconds(attempt_count: int) -> int:
        base = max(1, settings.research_dispatch_retry_base_seconds)
        cap = max(base, settings.research_dispatch_retry_max_seconds)
        exponent = max(0, min(int(attempt_count), 10))
        return min(cap, base * (2**exponent))

    def _record_resolution_failure(
        self,
        row: Mapping[str, Any],
        error_code: str,
        *,
        retryable: bool,
    ) -> str | None:
        recorder = getattr(self._repository, "record_dispatch_resolution_failure", None)
        if recorder is None:
            return
        try:
            return recorder(
                dispatch_id=str(row["dispatch_id"]),
                task_id=str(row["task_id"]),
                execution_decision_id=str(row["execution_decision_id"]),
                work_unit_id=str(row["work_unit_id"]),
                idempotency_key=str(row["idempotency_key"]),
                owner_id=str(row["owner_id"]),
                workspace_id=str(row["workspace_id"]),
                expected_task_revision=int(row["revision"]),
                error_code=error_code,
                retryable=retryable,
                max_attempts=max(1, min(2, settings.research_dispatch_reresolve_max_attempts)),
                retry_delay_seconds=self._retry_delay_seconds(int(row.get("attempt_count") or 0)),
            )
        except OperationalError:
            raise
        except ResearchOrchestrationPersistenceError as exc:
            if str(exc) == "dispatch_work_unit_missing":
                logger.error(
                    "dispatch integrity quarantine reported a missing WorkUnit",
                    extra={
                        "dispatch_id": str(row["dispatch_id"]),
                        "task_id": str(row["task_id"]),
                    },
                )
                return "task_integrity_quarantine_failed"
            raise _DispatchResolutionSettlementFailure(
                "dispatch_resolution_settlement_failed"
            ) from exc
        except Exception as exc:
            raise _DispatchResolutionSettlementFailure(
                "dispatch_resolution_settlement_failed"
            ) from exc

    def _candidate_rows(self) -> list[Mapping[str, Any]]:
        conninfo = (
            f"host={settings.postgres_host} port={settings.postgres_port} "
            f"dbname={settings.postgres_database} user={settings.postgres_user} connect_timeout=2"
        )
        with psycopg.connect(
            conninfo,
            password=settings.postgres_password,
            row_factory=dict_row,
        ) as connection:
            return list(
                connection.execute(
                    """SELECT d.dispatch_id, d.task_id, d.execution_decision_id,
                              d.work_unit_id, d.idempotency_key, d.status,
                              d.submission_mode, d.external_action_started_at,
                              d.result_storage_ref,
                              d.attempt_count, d.next_attempt_at, d.updated_at,
                              t.owner_id, t.workspace_id, t.revision
                         FROM openair_dispatch AS d
                         JOIN openair_tasks AS t ON t.task_id = d.task_id
                         WHERE d.execution_namespace = %(execution_namespace)s::text
                           AND (
                               SELECT count(*) FROM openair_dispatch AS active
                                WHERE active.task_id = d.task_id
                                  AND active.execution_namespace = d.execution_namespace
                                  AND active.status IN ('claimed', 'submission_authorized')
                                  AND active.lease_expires_at > clock_timestamp()
                           ) < %(max_inflight_per_task)s
                          AND (
                               (d.status = 'pending'
                                AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= clock_timestamp())
                                AND d.created_at <= clock_timestamp()
                                    - make_interval(secs => %(min_pending_age_seconds)s))
                            OR (d.status = 'claimed' AND d.lease_expires_at <= clock_timestamp())
                            OR (d.status = 'submission_authorized'
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
                        ORDER BY CASE
                                     WHEN d.status = 'pending' AND d.attempt_count = 0 THEN 0
                                     WHEN COALESCE(d.next_attempt_at, d.updated_at)
                                          <= clock_timestamp() - make_interval(
                                              secs => %(max_recovery_wait_seconds)s
                                          ) THEN 0
                                     WHEN d.status = 'pending' THEN 1
                                     ELSE 2
                                 END,
                                 d.next_attempt_at ASC NULLS FIRST, d.updated_at,
                                 d.created_at, d.dispatch_id
                        LIMIT 64""",
                    {
                        "min_pending_age_seconds": self._min_pending_age_seconds,
                        'execution_namespace': settings.execution_namespace,
                        'max_inflight_per_task': self._max_inflight_per_task,
                        'max_recovery_wait_seconds': max(
                            1, settings.research_dispatch_retry_max_seconds
                        ),
                    },
                ).fetchall()
            )

    def resolve(self) -> DispatchProviderBinding | None:
        if not settings.research_orchestration_v1 or settings.persistence_backend != "postgres":
            return None
        candidates = self._candidate_rows()
        for row in candidates:
            try:
                task = self._repository.load(
                    task_id=str(row["task_id"]),
                    owner_id=str(row["owner_id"]),
                    workspace_id=str(row["workspace_id"]),
                )
                if task.revision != int(row["revision"]):
                    self._record_resolution_failure(
                        row, "dispatch_task_revision_changed", retryable=True
                    )
                    continue
                if task.active_research_design_ref is None:
                    self._record_resolution_failure(
                        row, "dispatch_design_authority_missing", retryable=False
                    )
                    continue
                design = next(
                    (
                        item
                        for item in task.research_design_versions
                        if item.research_design_id == task.active_research_design_ref.research_design_id
                        and item.version == task.active_research_design_ref.version
                    ),
                    None,
                )
                if design is None or design.status.value != "confirmed":
                    self._record_resolution_failure(
                        row, "dispatch_design_authority_changed", retryable=False
                    )
                    continue
                decision = next(
                    (
                        item
                        for item in task.execution_decisions
                        if item.execution_decision_id == row["execution_decision_id"]
                    ),
                    None,
                )
                work_unit = next(
                    (item for item in task.work_units if item.work_unit_id == row["work_unit_id"]),
                    None,
                )
                status = str(row["status"])
                single_attempt_started = (
                    str(row.get("submission_mode") or "") == "single_attempt_no_replay"
                    and row.get("external_action_started_at") is not None
                )
                providerless_recovery = bool(
                    single_attempt_started
                    and (
                        status == "submission_authorized"
                        or (
                            status == "submission_unknown"
                            and row.get("result_storage_ref") is not None
                        )
                    )
                )
                settled_result_recovery = bool(
                    providerless_recovery
                    and decision is not None
                    and decision.status.value == "closed"
                    and getattr(getattr(decision, "outcome", None), "value", None)
                    in {"succeeded", "partial"}
                )
                if providerless_recovery:
                    # A durable result belongs to the frozen WorkUnit, not to the
                    # transient open state of its Decision. Continuation can close
                    # the Decision before the worker returns to settle that result.
                    allowed_decision_statuses = {"dispatched"}
                    if settled_result_recovery:
                        allowed_decision_statuses.add("closed")
                    allowed_work_statuses = {"queued", "completed"}
                    if status == "submission_unknown":
                        allowed_work_statuses.add("external_result_unknown")
                else:
                    allowed_decision_statuses = {"dispatched"}
                    allowed_work_statuses = {"queued"}
                if (
                    decision is None
                    or work_unit is None
                    or decision.status.value not in allowed_decision_statuses
                    or work_unit.status.value not in allowed_work_statuses
                    or work_unit.execution_decision_id != decision.execution_decision_id
                    or work_unit.idempotency_key != row["idempotency_key"]
                    or work_unit.registry_snapshot_ref != decision.registry_snapshot_ref
                ):
                    self._record_resolution_failure(
                        row, "dispatch_contract_mismatch", retryable=False
                    )
                    continue
                if not providerless_recovery:
                    dispatch_snapshot = self._repository.load_registry_snapshot(
                        decision.registry_snapshot_ref.content_hash
                    )
                    if not execution_snapshot_matches_planning(
                        task.registry_snapshot_ref.model_dump(mode="json", by_alias=True),
                        decision.registry_snapshot_ref.model_dump(mode="json", by_alias=True),
                        dispatch_snapshot,
                    ):
                        self._record_resolution_failure(
                            row, "dispatch_contract_mismatch", retryable=False
                        )
                        continue
                assignment = next(
                    (
                        item
                        for item in decision.assignments
                        if item.assignment_id == work_unit.assignment_id
                    ),
                    None,
                )
                if (
                    assignment is None
                    or assignment.executor_ref != work_unit.executor_ref
                    or assignment.responsible_actor_ref != work_unit.responsible_actor_ref
                    or assignment.allowed_skill_refs != work_unit.allowed_skill_refs
                    or getattr(decision, "execution_method_ref", None)
                    != getattr(work_unit, "execution_method_ref", None)
                    or getattr(assignment, "execution_method_role_id", None)
                    != getattr(work_unit, "execution_method_role_id", None)
                    or (getattr(assignment, "execution_method_iteration", None) or 1)
                    != (getattr(work_unit, "execution_method_iteration", None) or 1)
                    or getattr(assignment, "execution_method_role_control", None)
                    != getattr(work_unit, "execution_method_role_control", None)
                    or assignment.executor_ref.executor_type not in {"agent", "skill"}
                ):
                    self._record_resolution_failure(
                        row, "dispatch_assignment_drift", retryable=False
                    )
                    continue
                if providerless_recovery:
                    return DispatchProviderBinding(
                        provider_id=None,
                        dispatch_id=str(row["dispatch_id"]),
                        task_id=task.task_id,
                        execution_decision_id=decision.execution_decision_id,
                        work_unit_id=work_unit.work_unit_id,
                        registry_snapshot_ref=decision.registry_snapshot_ref.model_dump(
                            mode="json", by_alias=True
                        ),
                        executor_ref=assignment.executor_ref.model_dump(mode="json", by_alias=True),
                        responsible_actor_ref=assignment.responsible_actor_ref.model_dump(
                            mode="json", by_alias=True
                        ),
                        owner_id=str(row["owner_id"]),
                        workspace_id=str(row["workspace_id"]),
                        expected_task_revision=task.revision,
                        executor_type=assignment.executor_ref.executor_type,
                        source_binding_refs=tuple(
                            (item.binding_id, item.binding_version)
                            for item in assignment.source_binding_refs
                        ),
                        providerless_recovery=True,
                    )
                if assignment.executor_ref.executor_type == "skill":
                    if (
                        decision.strategy.value not in {"direct_skill", "skill_composition", "execution_method"}
                        or assignment.role.value != "primary"
                        or assignment.responsible_actor_ref.actor_type != "orchestrator"
                        or assignment.allowed_skill_refs
                    ):
                        self._record_resolution_failure(
                            row, "dispatch_skill_contract_mismatch", retryable=False
                        )
                        continue
                    reviewer = None
                    if decision.purpose.value == "completion":
                        reviewers = [
                            item for item in decision.assignments if item.role.value == "reviewer"
                        ]
                        if not _dispatch_review_contract_is_valid(
                            decision, assignment=assignment, reviewers=reviewers
                        ):
                            self._record_resolution_failure(
                                row, "dispatch_review_contract_mismatch", retryable=False
                            )
                            continue
                        reviewer = reviewers[0] if reviewers else None
                        if reviewer is not None and (
                            reviewer.executor_ref.executor_type != "agent"
                            or reviewer.executor_ref.executor_id != "result_reliability_audit"
                            or reviewer.executor_ref.definition_version != 1
                            or reviewer.responsible_actor_ref.actor_type != "agent"
                            or reviewer.responsible_actor_ref.actor_id
                            != reviewer.executor_ref.executor_id
                            or reviewer.responsible_actor_ref.definition_version
                            != reviewer.executor_ref.definition_version
                        ):
                            self._record_resolution_failure(
                                row, "dispatch_reviewer_binding_drift", retryable=False
                            )
                            continue
                        if reviewer is not None:
                            reviewer_readiness = [
                                item
                                for item in decision.runtime_readiness
                                if getattr(item.capability_ref, "agent_id", None)
                                == reviewer.executor_ref.executor_id
                                and getattr(item.capability_ref, "definition_version", None)
                                == reviewer.executor_ref.definition_version
                            ]
                            if (
                                len(reviewer_readiness) != 1
                                or not reviewer_readiness[0].ready
                                or reviewer_readiness[0].runtime_binding_id is None
                            ):
                                self._record_resolution_failure(
                                    row, "dispatch_readiness_invalid", retryable=False
                                )
                                continue
                    readiness = [
                        item
                        for item in decision.runtime_readiness
                        if getattr(item.capability_ref, "skill_id", None)
                        == assignment.executor_ref.executor_id
                        and getattr(item.capability_ref, "definition_version", None)
                        == assignment.executor_ref.definition_version
                    ]
                    if (
                        len(readiness) != 1
                        or not readiness[0].ready
                        or readiness[0].runtime_binding_id is None
                    ):
                        self._record_resolution_failure(
                            row, "dispatch_readiness_invalid", retryable=False
                        )
                        continue
                    snapshot = self._repository.load_registry_snapshot(
                        decision.registry_snapshot_ref.content_hash
                    )
                    if not _execution_method_assignment_is_valid(
                        decision, assignment, work_unit, snapshot
                    ):
                        self._record_resolution_failure(
                            row, "dispatch_execution_method_drift", retryable=False
                        )
                        continue
                    definitions = [
                        item
                        for item in snapshot.get("definitions", {}).get("skills", [])
                        if item.get("skillId") == assignment.executor_ref.executor_id
                        and item.get("definitionVersion")
                        == assignment.executor_ref.definition_version
                        and item.get("enabled")
                        and item.get("status") == "ready"
                    ]
                    if len(definitions) != 1:
                        self._record_resolution_failure(
                            row, "dispatch_definition_unavailable", retryable=False
                        )
                        continue
                    if reviewer is not None:
                        reviewer_definitions = [
                            item
                            for item in snapshot.get("definitions", {}).get("agents", [])
                            if item.get("agentId") == reviewer.executor_ref.executor_id
                            and item.get("definitionVersion")
                            == reviewer.executor_ref.definition_version
                            and item.get("enabled")
                        ]
                        if (
                            len(reviewer_definitions) != 1
                            or not isinstance(
                                reviewer_definitions[0].get("reviewContract"), Mapping
                            )
                            or not reviewer_definitions[0]["reviewContract"].get(
                                "frozenArtifactContract"
                            )
                            or reviewer_definitions[0].get("defaultProvider")
                            not in reviewer_definitions[0].get("allowedProviders", [])
                        ):
                            self._record_resolution_failure(
                                row, "dispatch_review_contract_mismatch", retryable=False
                            )
                            continue
                    binding_refs = {
                        (item.binding_id, item.binding_version)
                        for item in assignment.source_binding_refs
                    }
                    bindings = [
                        item
                        for item in snapshot.get("bindings", {}).get("skills", [])
                        if (item.get("bindingId"), item.get("bindingVersion")) in binding_refs
                        and item.get("skillId") == assignment.executor_ref.executor_id
                        and item.get("skillDefinitionVersion")
                        == assignment.executor_ref.definition_version
                        and item.get("definitionId") == work_unit.substage_definition_id
                        and item.get("definitionVersion") == work_unit.substage_definition_version
                        and item.get("enabled")
                    ]
                    if (
                        not binding_refs
                        or {
                            (item.get("bindingId"), item.get("bindingVersion"))
                            for item in bindings
                        }
                        != binding_refs
                    ):
                        self._record_resolution_failure(
                            row, "dispatch_binding_drift", retryable=False
                        )
                        continue
                    runtime = resolve_registered_durable_skill_runtime(
                        assignment.executor_ref.executor_id,
                        definition_version=int(assignment.executor_ref.definition_version or 0),
                        snapshot_definition=definitions[0],
                    )
                    if runtime is None:
                        self._record_resolution_failure(
                            row, "dispatch_runtime_unavailable", retryable=True
                        )
                        continue
                    return DispatchProviderBinding(
                        provider_id=None,
                        dispatch_id=str(row["dispatch_id"]),
                        task_id=task.task_id,
                        execution_decision_id=decision.execution_decision_id,
                        work_unit_id=work_unit.work_unit_id,
                        registry_snapshot_ref=decision.registry_snapshot_ref.model_dump(
                            mode="json", by_alias=True
                        ),
                        executor_ref=assignment.executor_ref.model_dump(
                            mode="json", by_alias=True
                        ),
                        responsible_actor_ref=assignment.responsible_actor_ref.model_dump(
                            mode="json", by_alias=True
                        ),
                        owner_id=str(row["owner_id"]),
                        workspace_id=str(row["workspace_id"]),
                        expected_task_revision=task.revision,
                        executor_type="skill",
                        source_binding_refs=tuple(sorted(binding_refs)),
                        skill_definition=definitions[0],
                    )

                readiness = [
                    item
                    for item in decision.runtime_readiness
                    if getattr(item.capability_ref, "agent_id", None) == assignment.executor_ref.executor_id
                    and getattr(item.capability_ref, "definition_version", None)
                    == assignment.executor_ref.definition_version
                ]
                if (
                    len(readiness) != 1
                    or not readiness[0].ready
                    or readiness[0].runtime_binding_id is None
                ):
                    self._record_resolution_failure(
                        row, "dispatch_readiness_invalid", retryable=False
                    )
                    continue
                snapshot = self._repository.load_registry_snapshot(
                    decision.registry_snapshot_ref.content_hash
                )
                if not _execution_method_assignment_is_valid(
                    decision, assignment, work_unit, snapshot
                ):
                    self._record_resolution_failure(
                        row, "dispatch_execution_method_drift", retryable=False
                    )
                    continue
                agent_definitions = [
                    item
                    for item in snapshot.get("definitions", {}).get("agents", [])
                    if item.get("agentId") == assignment.executor_ref.executor_id
                    and item.get("definitionVersion") == assignment.executor_ref.definition_version
                    and item.get("enabled")
                ]
                if len(agent_definitions) != 1:
                    self._record_resolution_failure(
                        row, "dispatch_definition_unavailable", retryable=False
                    )
                    continue
                binding_refs = {
                    (item.binding_id, item.binding_version) for item in assignment.source_binding_refs
                }
                bindings = [
                    item
                    for item in snapshot.get("bindings", {}).get("agents", [])
                    if (item.get("bindingId"), item.get("bindingVersion")) in binding_refs
                    and item.get("agentId") == assignment.executor_ref.executor_id
                    and item.get("agentDefinitionVersion") == assignment.executor_ref.definition_version
                    and item.get("enabled")
                ]
                if {
                    (item.get("bindingId"), item.get("bindingVersion")) for item in bindings
                } != binding_refs:
                    self._record_resolution_failure(
                        row, "dispatch_binding_drift", retryable=False
                    )
                    continue
                provider_id = str(agent_definitions[0].get("defaultProvider") or "").strip()
                if provider_id not in agent_definitions[0].get("allowedProviders", []):
                    self._record_resolution_failure(
                        row, "dispatch_provider_contract_mismatch", retryable=False
                    )
                    continue
                return DispatchProviderBinding(
                    provider_id=provider_id,
                    dispatch_id=str(row["dispatch_id"]),
                    task_id=task.task_id,
                    execution_decision_id=decision.execution_decision_id,
                    work_unit_id=work_unit.work_unit_id,
                    registry_snapshot_ref=decision.registry_snapshot_ref.model_dump(
                        mode="json", by_alias=True
                    ),
                    executor_ref=assignment.executor_ref.model_dump(mode="json", by_alias=True),
                    responsible_actor_ref=assignment.responsible_actor_ref.model_dump(
                        mode="json", by_alias=True
                    ),
                    owner_id=str(row["owner_id"]),
                    workspace_id=str(row["workspace_id"]),
                    expected_task_revision=task.revision,
                    executor_type="agent",
                    source_binding_refs=tuple(sorted(binding_refs)),
                )
            except OperationalError:
                raise
            except _DispatchResolutionSettlementFailure:
                raise
            except Exception:
                self._record_resolution_failure(
                    row, "dispatch_resolver_internal_error", retryable=True
                )
                continue
        return None


def _validate_startup() -> None:
    """Fail closed when a production worker cannot honor the canonical contract."""
    if (
        not math.isfinite(RENEW_SECONDS)
        or not math.isfinite(ORCHESTRATE_TIMEOUT_SECONDS)
        or RENEW_SECONDS <= 0
        or ORCHESTRATE_TIMEOUT_SECONDS <= 0
        or not 1 <= EXECUTION_CAPACITY <= 64
        or not 1 <= MAX_INFLIGHT_PER_TASK <= EXECUTION_CAPACITY
        or settings.research_dispatch_max_attempts <= 0
        or not 1 <= settings.research_dispatch_reresolve_max_attempts <= 2
        or settings.research_dispatch_retry_base_seconds <= 0
        or settings.research_dispatch_retry_max_seconds < settings.research_dispatch_retry_base_seconds
    ):
        raise RuntimeError("durable worker lease and retry controls must be finite and positive")
    env = (os.environ.get("OPENAIR_ENV") or "development").strip().lower()
    if env == "test":
        return
    if PAUSE_AFTER_CLAIM_SECONDS > 0 or PAUSE_AFTER_EXECUTION_STARTED_SECONDS > 0:
        raise RuntimeError("durable worker pause controls are only allowed when OPENAIR_ENV=test")
    if env not in {"production", "prod"}:
        return
    raw_runtime_type = (os.environ.get("OPENAIR_RUNTIME_TYPE") or "").strip().lower()
    if raw_runtime_type not in {"inline", "container", "worker"}:
        raise RuntimeError("production durable worker requires an explicit supported runtime type")
    if not settings.auth_required:
        raise RuntimeError("production durable worker requires OPENAIR_AUTH_REQUIRED=1")
    if not settings.research_orchestration_v1:
        raise RuntimeError("production durable worker requires canonical orchestration v1")
    if settings.persistence_backend != "postgres":
        raise RuntimeError("production durable worker requires PostgreSQL persistence")
    if not TOKEN.strip():
        raise RuntimeError("production durable worker requires OPENAIR_WORKER_AUTH_TOKEN")
    if raw_runtime_type == "container" and not (
        settings.worker_workspace_isolation and settings.worker_per_task_token
    ):
        raise RuntimeError(
            "container runtime requires task workspace isolation and per-task callback tokens"
        )
    if raw_runtime_type == "container" and settings.worker_per_task_token:
        if settings.worker_token_ttl_seconds <= settings.worker_timeout_seconds:
            raise RuntimeError(
                "container callback token TTL must exceed the worker execution timeout"
            )
        if settings.worker_keepalive:
            raise RuntimeError(
                "execution-scoped callback tokens require worker keepalive to be disabled"
            )


def _verify_api_startup_contract() -> None:
    """Reject split-brain API/worker execution settings before polling."""
    env = (os.environ.get("OPENAIR_ENV") or "development").strip().lower()
    if env not in {"production", "prod"}:
        return
    response = _post("/internal/worker/startup-contract", {}, timeout=15)
    remote = response.get("contract")
    local = settings.execution_startup_contract()
    if not isinstance(remote, dict):
        raise RuntimeError("API startup contract response is invalid")
    if remote != local:
        mismatch = sorted(
            key for key in set(remote) | set(local) if remote.get(key) != local.get(key)
        )
        print(
            json.dumps(
                {
                    "event": "worker_startup_contract_mismatch",
                    "workerId": WORKER_ID,
                    "mismatchKeys": mismatch,
                }
            ),
            flush=True,
        )
        raise RuntimeError("API and durable worker execution contracts differ")


def _post(
    path: str,
    body: dict,
    *,
    timeout: float = 15,
    worker_token: str | None = None,
) -> dict:
    request = Request(
        f"{API_BASE}{path}",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-OpenAIR-Worker-Token": worker_token if worker_token is not None else TOKEN,
        },
    )
    with urlopen(request, timeout=timeout) as response:  # nosec B310: operator-configured internal URL
        return json.loads(response.read().decode("utf-8"))


def _run_worker_token(run: Mapping[str, Any]) -> str:
    if not settings.worker_per_task_token:
        return TOKEN
    token = str(run.get("workerToken") or "").strip()
    if not token:
        raise RuntimeError("durable run claim did not return an execution-scoped worker token")
    return token


def _post_for_run(path: str, body: dict, run: Mapping[str, Any]) -> dict:
    if settings.worker_per_task_token:
        return _post(path, body, worker_token=_run_worker_token(run))
    return _post(path, body)


class _LeaseKeeper:
    def __init__(self, run: dict):
        self._run = run
        self._stop = threading.Event()
        self.lost = False
        self._thread = threading.Thread(target=self._renew, name=f"lease-{run['runId']}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _renew(self) -> None:
        while not self._stop.wait(RENEW_SECONDS):
            try:
                _post_for_run(
                    f"/internal/durable-runs/{self._run['runId']}/renew",
                    {"workerId": WORKER_ID, "leaseEpoch": self._run["leaseEpoch"]},
                    self._run,
                )
                print(json.dumps({"event": "lease_renewed", "workerId": WORKER_ID, "runId": self._run["runId"]}), flush=True)
            except (HTTPError, OSError):
                self.lost = True
                print(json.dumps({"event": "lease_lost", "workerId": WORKER_ID, "runId": self._run["runId"]}), flush=True)
                return


def _upload_runner_artifacts(run: dict, result: dict, keeper: _LeaseKeeper) -> bool:
    artifacts = result.get("artifacts", [])
    if not isinstance(artifacts, list):
        raise ValueError("runner artifacts must be a list")
    for artifact in artifacts:
        if keeper.lost:
            return False
        if not isinstance(artifact, dict) or not isinstance(artifact.get("contentBase64"), str):
            raise ValueError("runner artifact requires contentBase64")
        if not isinstance(artifact.get("presentation"), dict):
            raise ValueError("runner artifact requires API-validated presentation")
        content = base64.b64decode(artifact["contentBase64"], validate=True)
        intent = _post_for_run(
            f"/internal/durable-runs/{run['runId']}/artifacts/request-upload",
            {
                "workerId": WORKER_ID,
                "leaseEpoch": run["leaseEpoch"],
                "expectedSize": len(content),
                "expectedSha256": hashlib.sha256(content).hexdigest(),
                "capabilityCallId": run["capabilityCallId"],
                "presentation": artifact["presentation"],
            },
            run,
        )
        if keeper.lost:
            return False
        put = Request(intent["uploadUrl"], data=content, method="PUT", headers={"Content-Type": artifact.get("contentType", "application/octet-stream")})
        with urlopen(put, timeout=30):  # nosec B310: API-issued one-time storage URL
            pass
        if keeper.lost:
            return False
        _post_for_run(
            f"/internal/durable-runs/{run['runId']}/artifacts/{intent['uploadId']}/confirm-upload",
            {"workerId": WORKER_ID, "leaseEpoch": run["leaseEpoch"]},
            run,
        )
    return not keeper.lost


def _renew_before_execution(run: dict) -> None:
    """Fence a lease that may have expired while the worker was paused.

    A pre-execution pause is safe because no external action has started.  The
    worker must still prove it owns the same lease before it records
    ``execution_started`` or invokes an external runner.
    """
    try:
        _post_for_run(
            f"/internal/durable-runs/{run['runId']}/renew",
            {"workerId": WORKER_ID, "leaseEpoch": run["leaseEpoch"]},
            run,
        )
        print(json.dumps({"event": "pre_execution_lease_verified", "workerId": WORKER_ID, "runId": run["runId"]}), flush=True)
    except (HTTPError, OSError):
        print(json.dumps({"event": "pre_execution_lease_lost", "workerId": WORKER_ID, "runId": run["runId"]}), flush=True)
        raise


def _create_orchestration_plan(run: dict) -> None:
    _post_for_run(
        f"/internal/durable-runs/{run['runId']}/orchestration-plan",
        {"workerId": WORKER_ID, "leaseEpoch": run["leaseEpoch"]},
        run,
    )


def _orchestrate(run: dict) -> dict:
    return process_durable_worker_run(
        run["runId"],
        WORKER_ID,
        run["leaseEpoch"],
    )


def _orchestration_disposition(result: dict) -> str:
    if result.get("knownPause") == "awaiting_human":
        return "paused"
    if result.get("knownFailure"):
        return "terminal"
    return "complete"


def _durable_claim_not_before() -> datetime | None:
    raw = os.environ.get("OPENAIR_DURABLE_CLAIM_NOT_BEFORE", "").strip()
    if not raw:
        if (os.environ.get("OPENAIR_ENV") or "development").strip().lower() in {"production", "prod"}:
            raise RuntimeError("OPENAIR_DURABLE_CLAIM_NOT_BEFORE is required in production")
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError("OPENAIR_DURABLE_CLAIM_NOT_BEFORE must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise RuntimeError("OPENAIR_DURABLE_CLAIM_NOT_BEFORE must include a timezone")
    return parsed.astimezone(timezone.utc)


def _claim_and_run_research_start() -> bool:
    repository = ResearchStartRepository()
    try:
        repository.reconcile_expired_leases()
        claim = repository.claim(WORKER_ID, not_before=_durable_claim_not_before())
    except OperationalError:
        print(json.dumps({"event": "research_start_global_db_failure", "workerId": WORKER_ID}), flush=True)
        raise
    if claim is None:
        return False
    print(
        json.dumps(
            {
                "event": "CLAIM_ACQUIRED",
                "workerId": WORKER_ID,
                "requestId": claim["requestId"],
                "leaseEpoch": claim["leaseEpoch"],
                "requestState": "leased",
                "phase": "research_start",
            }
        ),
        flush=True,
    )
    try:
        process_research_start_claim(
            claim,
            repository=repository,
            provider=_research_start_provider(),
            worker_id=WORKER_ID,
        )
    except OperationalError:
        # A post-claim database outage is a global authority failure, not a
        # request-local defect.  Preserve the existing fail-closed process
        # behavior so another worker cannot silently take an ambiguous lease.
        raise
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "POST_CLAIM_EXCEPTION",
                    "requestId": claim["requestId"],
                    "workerId": WORKER_ID,
                    "leaseEpoch": claim["leaseEpoch"],
                    "requestState": "leased",
                    "phase": "research_start",
                    "exceptionType": type(exc).__name__,
                    "exceptionMessage": str(exc)[:500],
                }
            ),
            flush=True,
        )
        failure_code = (
            "registry_snapshot_integrity_invalid"
            if isinstance(exc, RegistrySnapshotIntegrityError)
            else "research_start_request_processing_failed"
        )
        try:
            repository.fail_request(
                claim["requestId"],
                WORKER_ID,
                claim["leaseEpoch"],
                failure_code=failure_code,
                user_message="该研究请求的持久化记录无法安全处理，未执行任何外部操作。",
            )
        except OperationalError:
            raise
        except Exception:
            logger.exception(
                "research-start request failure could not be settled requestId=%s",
                claim["requestId"],
            )
        else:
            print(
                json.dumps(
                    {
                        "event": "research_start_request_failed",
                        "requestId": claim["requestId"],
                        "workerId": WORKER_ID,
                        "leaseEpoch": claim["leaseEpoch"],
                        "failureCode": failure_code,
                    }
                ),
                flush=True,
            )
    return True


def _research_start_provider() -> ResearchStartOneShotProvider | ResearchStartDeterministicTestProvider:
    if (
        (os.environ.get("OPENAIR_ENV") or "").strip().lower() == "test"
        and os.environ.get("OPENAIR_RESEARCH_START_TEST_FAKE_PROVIDER") == "1"
    ):
        return ResearchStartDeterministicTestProvider()
    return ResearchStartOneShotProvider()


def _claim_and_run_legacy_durable() -> bool:
    body: dict[str, object] = {"workerId": WORKER_ID}
    not_before = _durable_claim_not_before()
    if not_before is not None:
        body["notBefore"] = not_before.isoformat()
    try:
        claimed = _post("/internal/durable-runs/claim", body)
    except HTTPError:
        # Authentication and HTTP protocol failures retain their existing policy.
        raise
    except OSError as exc:
        # A pre-claim transport failure owns no work. Keep polling independent
        # PostgreSQL queues while the legacy HTTP consumer is unavailable.
        logger.warning("legacy claim transport unavailable errorType=%s", type(exc).__name__)
        return False
    run = claimed.get("run")
    if not run:
        return False
    if settings.worker_per_task_token:
        run = dict(run)
        run["workerToken"] = claimed.get("workerToken")
        _run_worker_token(run)
    print(json.dumps({"event": "claimed", "workerId": WORKER_ID, "runId": run["runId"], "leaseEpoch": run["leaseEpoch"]}), flush=True)
    if PAUSE_AFTER_CLAIM_SECONDS > 0:
        time.sleep(PAUSE_AFTER_CLAIM_SECONDS)
    _renew_before_execution(run)
    _create_orchestration_plan(run)
    _renew_before_execution(run)
    keeper = _LeaseKeeper(run)
    keeper.start()
    if PAUSE_AFTER_EXECUTION_STARTED_SECONDS > 0:
        print(json.dumps({"event": "orchestration_ready_pause", "workerId": WORKER_ID, "actualPid": os.getpid(), "runId": run["runId"]}), flush=True)
        time.sleep(PAUSE_AFTER_EXECUTION_STARTED_SECONDS)
    try:
        result = _orchestrate(run)
        if keeper.lost:
            return True
        disposition = _orchestration_disposition(result)
        if disposition != "complete":
            print(json.dumps({
                "event": "orchestration_paused" if disposition == "paused" else "orchestration_terminal",
                "workerId": WORKER_ID,
                "runId": run["runId"],
            }), flush=True)
            return True
        _renew_before_execution(run)
        if keeper.lost or not _upload_runner_artifacts(run, result, keeper):
            return True
        _renew_before_execution(run)
        if keeper.lost:
            return True
        _post_for_run(
            f"/internal/durable-runs/{run['runId']}/callback",
            {"workerId": WORKER_ID, "leaseEpoch": run["leaseEpoch"], "type": "succeeded", "payload": {}},
            run,
        )
        return True
    finally:
        keeper.close()


def _provider_for_dispatch_binding(
    binding: DispatchProviderBinding,
    *,
    provider_factory: RegistryDispatchProviderFactory | None,
    skill_provider_factory: Callable[[DispatchProviderBinding], ResearchDispatchProvider] | None,
) -> ResearchDispatchProvider | None:
    if binding.executor_type == "skill":
        if binding.skill_definition is None or not binding.source_binding_refs:
            return None
        if skill_provider_factory is not None:
            return skill_provider_factory(binding)
        return DirectSkillResearchDispatchProvider(
            skill_id=str(binding.executor_ref.get("executorId") or ""),
            definition_version=int(binding.executor_ref.get("definitionVersion") or 0),
            snapshot_definition=binding.skill_definition,
            source_binding_refs=binding.source_binding_refs,
        )
    if binding.provider_id is None:
        return None
    if (
        provider_factory is None
        and binding.executor_type == "agent"
        and str(binding.executor_ref.get("executorId") or "") == "key_literature_positioning"
    ):
        return HuiyanResearchDispatchProvider(provider_id=binding.provider_id)
    return (provider_factory or RegistryDispatchProviderFactory()).create(binding.provider_id)


def _settle_authorized_dispatch_failure(
    dispatch_service: ResearchDispatchService,
    authorized: Any,
    *,
    owner_id: str,
    workspace_id: str,
    error_code: str,
    permanent_before_start: bool = False,
) -> None:
    """Settle a recovery failure without ever replaying an action-started call."""
    if getattr(authorized, "external_action_started_at", None) is not None:
        recorder = getattr(dispatch_service, "record_outcome", None)
        if recorder is None:
            # Test doubles and older adapters may omit the new semantic method.
            # Crucially, do not route the row into the pre-submission retry API.
            logger.error(
                "dispatch service cannot settle started recovery dispatch=%s error=%s",
                getattr(authorized, "dispatch_id", ""),
                error_code,
            )
            return
        recorder(
            authorized,
            owner_id=owner_id,
            workspace_id=workspace_id,
            outcome="unknown",
            error_code=error_code,
        )
        return
    if permanent_before_start:
        dispatch_service.fail_authorized_unsubmitted(
            authorized,
            owner_id=owner_id,
            workspace_id=workspace_id,
            error_code=error_code,
        )
        return
    dispatch_service.defer_authorized(
        authorized,
        owner_id=owner_id,
        workspace_id=workspace_id,
        error_code=error_code,
    )


def claim_and_run_research_dispatch(
    *,
    binding_resolver: DispatchProviderBindingResolver | None = None,
    provider_factory: RegistryDispatchProviderFactory | None = None,
    skill_provider_factory: Callable[[DispatchProviderBinding], ResearchDispatchProvider] | None = None,
    service: ResearchDispatchService | None = None,
    execution_role: str = "worker",
    lease_owner: str | None = None,
) -> bool:
    """Poll one canonical research dispatch under one database-owned execution role.

    ``worker`` is the production default.  ``api`` is only enabled for explicit
    inline/fallback modes and still claims the same durable dispatch before any
    provider call.
    """
    mode = settings.workunit_execution_mode
    if execution_role == "worker":
        if mode == "inline":
            return False
        min_pending_age_seconds = 0
    elif execution_role == "api":
        if mode not in {"inline", "worker_with_fallback"}:
            return False
        min_pending_age_seconds = (
            settings.workunit_fallback_after_seconds
            if mode == "worker_with_fallback"
            else 0
        )
    else:
        raise ValueError("unsupported research dispatch execution role")

    if binding_resolver is not None:
        resolver = binding_resolver
    elif min_pending_age_seconds:
        resolver = PostgresDispatchProviderBindingResolver(
            min_pending_age_seconds=min_pending_age_seconds
        )
    else:
        resolver = PostgresDispatchProviderBindingResolver()
    if not settings.research_orchestration_v1 or settings.persistence_backend != "postgres":
        return False
    dispatch_service = service or ResearchDispatchService()
    binding = resolver.resolve()
    if binding is None:
        return False
    scope = {
        "lease_owner": (lease_owner or WORKER_ID),
        "lease_seconds": max(1, int(RENEW_SECONDS * 3)),
        "batch_size": 1,
        "dispatch_id": binding.dispatch_id,
        "task_id": binding.task_id,
        "owner_id": binding.owner_id,
        'workspace_id': binding.workspace_id,
        'max_inflight_per_task': MAX_INFLIGHT_PER_TASK,
    }

    recovery = dispatch_service.claim_authorized_recovery(**scope)
    if recovery:
        authorized = recovery[0]
        print(
            json.dumps(
                {
                    "event": "research_dispatch_recovery_claimed",
                    "executionRole": execution_role,
                    "executorId": scope["lease_owner"],
                    "executionNamespace": settings.execution_namespace,
                    "dispatchId": authorized.dispatch_id,
                    "taskId": authorized.task_id,
                    "workUnitId": authorized.work_unit_id,
                }
            ),
            flush=True,
        )
        if not _dispatch_matches_binding(authorized, binding):
            _settle_authorized_dispatch_failure(
                dispatch_service,
                authorized,
                owner_id=binding.owner_id,
                workspace_id=binding.workspace_id,
                error_code=(
                    "dispatch_binding_changed_after_action_started"
                    if authorized.external_action_started_at is not None
                    else "dispatch_binding_changed"
                ),
            )
            return True
        provider = None
        if not binding.providerless_recovery:
            try:
                provider = _provider_for_dispatch_binding(
                    binding,
                    provider_factory=provider_factory,
                    skill_provider_factory=skill_provider_factory,
                )
            except OperationalError:
                raise
            except DispatchProviderPermanentPreflightError as exc:
                _settle_authorized_dispatch_failure(
                    dispatch_service,
                    authorized,
                    owner_id=binding.owner_id,
                    workspace_id=binding.workspace_id,
                    error_code=exc.code,
                    permanent_before_start=True,
                )
                return True
            except DispatchProviderRetryablePreflightError as exc:
                _settle_authorized_dispatch_failure(
                    dispatch_service,
                    authorized,
                    owner_id=binding.owner_id,
                    workspace_id=binding.workspace_id,
                    error_code=exc.code,
                )
                return True
            except Exception:
                logger.exception(
                    "research_dispatch recovery provider factory failed dispatchId=%s taskId=%s",
                    authorized.dispatch_id,
                    authorized.task_id,
                )
                _settle_authorized_dispatch_failure(
                    dispatch_service,
                    authorized,
                    owner_id=binding.owner_id,
                    workspace_id=binding.workspace_id,
                    error_code="dispatch_provider_factory_error",
                )
                return True
            if provider is None:
                _settle_authorized_dispatch_failure(
                    dispatch_service,
                    authorized,
                    owner_id=binding.owner_id,
                    workspace_id=binding.workspace_id,
                    error_code="dispatch_provider_unavailable",
                )
                return True
        try:
            process_authorized_recovery(
                authorized,
                owner_id=binding.owner_id,
                workspace_id=binding.workspace_id,
                provider=provider,
                service=dispatch_service,
                lease_seconds=scope["lease_seconds"],
            )
        except OperationalError:
            raise
        except Exception:
            logger.exception(
                "research_dispatch recovery unexpected error dispatchId=%s taskId=%s",
                authorized.dispatch_id,
                authorized.task_id,
            )
            _settle_authorized_dispatch_failure(
                dispatch_service,
                authorized,
                owner_id=binding.owner_id,
                workspace_id=binding.workspace_id,
                error_code="dispatch_task_execution_error",
            )
        return True

    if binding.providerless_recovery:
        return False
    claims = dispatch_service.claim(
        **scope,
        min_pending_age_seconds=min_pending_age_seconds,
    )
    if not claims:
        return False
    fence = claims[0]
    print(
        json.dumps(
            {
                "event": "research_dispatch_claimed",
                "executionRole": execution_role,
                "executorId": scope["lease_owner"],
                "executionNamespace": settings.execution_namespace,
                "dispatchId": fence.dispatch_id,
                "taskId": fence.task_id,
                "workUnitId": fence.work_unit_id,
            }
        ),
        flush=True,
    )
    if not _dispatch_matches_binding(fence, binding):
        dispatch_service.defer_unsubmitted(
            fence,
            owner_id=binding.owner_id,
            workspace_id=binding.workspace_id,
            error_code="dispatch_binding_changed",
        )
        return True
    try:
        provider = _provider_for_dispatch_binding(
            binding,
            provider_factory=provider_factory,
            skill_provider_factory=skill_provider_factory,
        )
    except OperationalError:
        raise
    except DispatchProviderPermanentPreflightError as exc:
        dispatch_service.fail_unsubmitted(
            fence,
            owner_id=binding.owner_id,
            workspace_id=binding.workspace_id,
            error_code=exc.code,
        )
        return True
    except DispatchProviderRetryablePreflightError as exc:
        dispatch_service.defer_unsubmitted(
            fence,
            owner_id=binding.owner_id,
            workspace_id=binding.workspace_id,
            error_code=exc.code,
        )
        return True
    except Exception:
        logger.exception(
            "research_dispatch provider factory failed dispatchId=%s taskId=%s",
            fence.dispatch_id,
            fence.task_id,
        )
        dispatch_service.defer_unsubmitted(
            fence,
            owner_id=binding.owner_id,
            workspace_id=binding.workspace_id,
            error_code="dispatch_provider_factory_error",
        )
        return True
    if provider is None:
        dispatch_service.defer_unsubmitted(
            fence,
            owner_id=binding.owner_id,
            workspace_id=binding.workspace_id,
            error_code="dispatch_provider_unavailable",
        )
        return True
    try:
        result = process_dispatch_claim(
            fence,
            owner_id=binding.owner_id,
            workspace_id=binding.workspace_id,
            expected_task_revision=binding.expected_task_revision,
            provider=provider,
            service=dispatch_service,
            lease_seconds=scope["lease_seconds"],
        )
        print(
            json.dumps(
                {
                    "event": "research_dispatch_finished",
                    "executionRole": execution_role,
                    "executorId": scope["lease_owner"],
                    "dispatchId": fence.dispatch_id,
                    "taskId": fence.task_id,
                    "workUnitId": fence.work_unit_id,
                    "status": result.status,
                    "recorded": result.recorded,
                    "errorCode": result.error_code,
                }
            ),
            flush=True,
        )
        if result.status in {"failed", "submission_unknown"}:
            logger.warning(
                "research_dispatch finished status=%s errorCode=%s dispatchId=%s taskId=%s workUnitId=%s",
                result.status,
                result.error_code or "",
                fence.dispatch_id,
                fence.task_id,
                fence.work_unit_id,
            )
        # Wartime: immediately drain continuation so the next node is released
        # before the worker sleeps or claims another task's dispatch.
        if result.recorded or result.status in {
            "completed",
            "failed",
            "submission_unknown",
            "known_failed",
            "outcome_conflict",
        }:
            try:
                dispatch_service.process_next_dispatch_continuation(
                    lease_owner=scope["lease_owner"],
                    lease_seconds=scope["lease_seconds"],
                )
            except Exception:
                logger.exception(
                    "research_dispatch post-finish continuation failed dispatchId=%s taskId=%s",
                    fence.dispatch_id,
                    fence.task_id,
                )
    except OperationalError:
        raise
    except Exception:
        logger.exception(
            "research_dispatch unexpected error dispatchId=%s taskId=%s",
            fence.dispatch_id,
            fence.task_id,
        )
        dispatch_service.defer_unsubmitted(
            fence,
            owner_id=binding.owner_id,
            workspace_id=binding.workspace_id,
            error_code="dispatch_task_execution_error",
        )
    return True


# Backwards-compatible private name used by the existing worker poller/tests.
_claim_and_run_research_dispatch = claim_and_run_research_dispatch


def _claim_and_run_research_continuation() -> bool:
    if not settings.research_orchestration_v1 or settings.persistence_backend != "postgres":
        return False
    return ResearchDispatchService().process_next_dispatch_continuation(
        lease_owner=WORKER_ID,
        lease_seconds=max(1, int(RENEW_SECONDS * 3)),
    )


def _resume_pending_method_continuation() -> bool:
    if not settings.research_orchestration_v1 or settings.persistence_backend != 'postgres':
        return False
    return ResearchDispatchService().resume_pending_method_continuation()


def _dispatch_matches_binding(fence: Any, binding: DispatchProviderBinding) -> bool:
    return (
        fence.dispatch_id == binding.dispatch_id
        and fence.task_id == binding.task_id
        and fence.execution_decision_id == binding.execution_decision_id
        and fence.work_unit_id == binding.work_unit_id
    )


def _poll_handlers() -> tuple[Callable[[], bool], ...]:
    # Continuation before dispatch: after a node completes, release/enqueue the
    # next WorkUnit before claiming unrelated pending work (kills user-visible
    # "previous done, next still waiting for worker" gaps under single worker).
    return (
        _claim_and_run_legacy_durable,
        _claim_and_run_research_start,
        _claim_and_run_research_continuation,
        _resume_pending_method_continuation,
        _claim_and_run_research_dispatch,
        lambda: process_model_job_once(worker_id=WORKER_ID),
    )


def _poll_once(queue_cursor: int) -> tuple[bool, int]:
    handlers = _poll_handlers()
    start = queue_cursor % len(handlers)
    for offset in range(len(handlers)):
        index = (start + offset) % len(handlers)
        if handlers[index]():
            return True, (index + 1) % len(handlers)
    return False, (start + 1) % len(handlers)


@dataclass
class _ExecutionSlot:
    queue_cursor: int
    future: Any = None
    ready_at: float = 0.0


def _submit_ready_slots(executor, slots) -> None:
    now = time.monotonic()
    for slot in slots:
        if slot.future is None and slot.ready_at <= now:
            slot.future = executor.submit(_poll_once, slot.queue_cursor)


def _finish_slot(slot, *, now: float) -> bool:
    future = slot.future
    slot.future = None
    if future is None:
        return False
    try:
        handled, slot.queue_cursor = future.result()
    except Exception:
        slot.ready_at = now + POLL_SECONDS
        raise
    slot.ready_at = now if handled else now + POLL_SECONDS
    return handled


def _shutdown_execution_pool(executor) -> None:
    wait_for_slots = (os.environ.get("OPENAIR_ENV") or "").strip().lower() == "test"
    executor.shutdown(wait=wait_for_slots, cancel_futures=True)


def run_forever() -> None:
    _setup_worker_logging()
    _validate_startup()
    # _verify_api_startup_contract()
    print(json.dumps({"event": "worker_process_started", "workerId": WORKER_ID, "actualPid": os.getpid()}), flush=True)
    executor = ThreadPoolExecutor(
        max_workers=EXECUTION_CAPACITY,
        thread_name_prefix="openair-execution",
    )
    handler_count = len(_poll_handlers())
    slots = [
        _ExecutionSlot(queue_cursor=index % handler_count)
        for index in range(EXECUTION_CAPACITY)
    ]
    print(json.dumps({
        "event": "worker_execution_pool_started",
        "workerId": WORKER_ID,
        "capacity": EXECUTION_CAPACITY,
    }), flush=True)
    first_poll_ready = False
    while True:
        try:
            _submit_ready_slots(executor, slots)
            active = [slot.future for slot in slots if slot.future is not None]
            done, _pending = wait(
                active, timeout=POLL_SECONDS, return_when=FIRST_COMPLETED,
            )
            if not done:
                continue
            now = time.monotonic()
            for slot in slots:
                if slot.future in done:
                    _finish_slot(slot, now=now)
            if not first_poll_ready:
                print(json.dumps({"event": "worker_first_poll_ready", "workerId": WORKER_ID, "actualPid": os.getpid()}), flush=True)
                first_poll_ready = True
        except HTTPError as exc:
            print(json.dumps({"event": "http_error", "workerId": WORKER_ID, "status": exc.code}), flush=True)
            if exc.code in {401, 403}:
                _shutdown_execution_pool(executor)
                raise RuntimeError("durable worker authentication failed") from exc
            if EXIT_ON_HTTP_ERROR:
                _shutdown_execution_pool(executor)
                return
            time.sleep(POLL_SECONDS)
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            print(
                json.dumps(
                    {
                        "event": "stale_fence_rejected",
                        "workerId": WORKER_ID,
                        "status": exc.status_code,
                    }
                ),
                flush=True,
            )
            time.sleep(POLL_SECONDS)
        except OperationalError as exc:
            _shutdown_execution_pool(executor)
            print(
                json.dumps({"event": "worker_global_db_failure", "workerId": WORKER_ID}),
                flush=True,
            )
            raise RuntimeError("durable worker database authority unavailable") from exc
        except OSError:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run_forever()
