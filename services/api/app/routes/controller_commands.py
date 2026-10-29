import logging
import traceback
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException

from app.auth import RequestIdentity, resolve_request_identity
from app.models.controller_commands import ControllerCommandIn, ControllerCommandOut
from app.modules.controller_command_repository import ControllerCommandRepository
from app.modules.durable_execution import DurableExecutionError

router = APIRouter(prefix="/tasks", tags=["controller-commands"])
logger = logging.getLogger(__name__)


def _canonical_supplement_progression_method():
    try:
        from app.modules.research_dispatch_service import ResearchDispatchService
        service = ResearchDispatchService()
    except Exception:
        return None
    method = getattr(service, "progress_controller_command", None)
    return method if callable(method) else None


def _canonical_supplement_progression_available() -> bool:
    return _canonical_supplement_progression_method() is not None


def _trigger_canonical_supplement_progression(
    *, task_id: str, command_id: str, owner_id: str, workspace_id: str,
    expected_task_revision: int,
) -> None:
    method = _canonical_supplement_progression_method()
    if method is None:
        raise RuntimeError("supplement_progression_service_unavailable")
    method(
        task_id=task_id,
        command_id=command_id,
        owner_id=owner_id,
        workspace_id=workspace_id,
        expected_task_revision=expected_task_revision,
    )


def _progression_waits_for_safe_boundary(exc: Exception) -> bool:
    return isinstance(exc, RuntimeError) and str(exc) in {
        "controller_command_terminal_decision_missing",
        "controller_command_substage_not_recoverable",
    }


def _safe_progression_error_code(exc: Exception) -> str:
    """Retain a bounded machine fact without persisting arbitrary exception text."""
    candidates = [getattr(exc, "code", None)]
    if isinstance(exc, RuntimeError):
        candidates.append(str(exc))
    for candidate in candidates:
        value = str(candidate or "").strip()
        if (
            value
            and len(value) <= 160
            and value.isascii()
            and value == value.lower()
            and all(character.isalnum() or character in "_.:-" for character in value)
        ):
            return value
    return f"internal_exception:{type(exc).__module__}.{type(exc).__name__}"[:160].lower()


def _progression_failure_location(exc: Exception) -> str | None:
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return None
    frame = frames[-1]
    filename = frame.filename.replace("\\", "/").rsplit("/", 1)[-1]
    return f"{filename}:{frame.lineno}:{frame.name}"


def _reconcile_canonical_supplement(
    *,
    repository: ControllerCommandRepository,
    row: dict,
    owner_id: str,
    workspace_id: str,
) -> dict:
    """Recover a committed received command after POST/process uncertainty."""
    if row.get("state") != "received":
        return row
    lifecycle_kind = repository.get_lifecycle_kind(
        task_id=row["taskId"],
        command_id=row["commandId"],
        owner_id=owner_id,
        workspace_id=workspace_id,
    )
    if lifecycle_kind != "canonical_v1_2":
        return row
    context = repository.get_progression_context(
        task_id=row["taskId"],
        command_id=row["commandId"],
        owner_id=owner_id,
        workspace_id=workspace_id,
    )
    if not _canonical_supplement_progression_available():
        return repository.reject_received(
            task_id=row["taskId"],
            command_id=row["commandId"],
            owner_id=owner_id,
            workspace_id=workspace_id,
            reason_code="supplement_progression_service_unavailable",
        ) or row

    claim_method = getattr(repository, "claim_received_for_progression", None)
    claim = (
        claim_method(
            task_id=row["taskId"],
            command_id=row["commandId"],
            owner_id=owner_id,
            workspace_id=workspace_id,
            lease_owner=f"controller-command-route:{uuid4().hex}",
        )
        if callable(claim_method)
        else row
    )
    if claim is None:
        return repository.get(
            task_id=row["taskId"],
            command_id=row["commandId"],
            owner_id=owner_id,
            workspace_id=workspace_id,
        ) or row
    try:
        _trigger_canonical_supplement_progression(
            task_id=row["taskId"],
            command_id=row["commandId"],
            owner_id=owner_id,
            workspace_id=workspace_id,
            expected_task_revision=int(row["submittedTaskRevision"]) + 1,
        )
    except Exception as exc:
        # A normally active execution keeps the supplement received until its
        # next safe Dispatch boundary. A running-but-recoverable task reaches
        # this route too and must be allowed to materialize its recovery work.
        if (
            context is not None
            and context.get("taskStatus") == "running"
            and _progression_waits_for_safe_boundary(exc)
        ):
            return row
        error_code = _safe_progression_error_code(exc)
        logger.error(
            "controller command progression failed and remains retryable",
            extra={
                "task_id": row["taskId"],
                "command_id": row["commandId"],
                "progression_error_code": error_code,
                "progression_error_type": type(exc).__name__,
                "progression_error_location": _progression_failure_location(exc),
            },
        )
        try:
            return repository.record_progression_failure(
                task_id=row["taskId"],
                command_id=row["commandId"],
                owner_id=owner_id,
                workspace_id=workspace_id,
                error_code=error_code,
            ) or row
        except Exception as persistence_error:
            logger.error(
                "controller command progression failure fact could not be persisted",
                extra={
                    "task_id": row["taskId"],
                    "command_id": row["commandId"],
                    "persistence_error_type": type(persistence_error).__name__,
                },
            )
            return row
    # Successful recovery materialization still leaves the command in received.
    # applied is authoritative only after a real Dispatch authorization carries
    # the supplement into execution.
    return repository.get(
        task_id=row["taskId"],
        command_id=row["commandId"],
        owner_id=owner_id,
        workspace_id=workspace_id,
    ) or row


@router.post("/{task_id}/controller-commands", response_model=ControllerCommandOut, status_code=201)
def create_controller_command(task_id: str, body: ControllerCommandIn, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), identity: RequestIdentity = Depends(resolve_request_identity)) -> ControllerCommandOut:
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="text must not be blank")
    repository = ControllerCommandRepository()
    progression_available = _canonical_supplement_progression_available()
    try:
        row, _replay = repository.create(
            task_id=task_id,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
            kind=body.kind,
            text=text,
            expected_task_revision=body.expectedTaskRevision,
            idempotency_key=idempotency_key or "",
            canonical_progression_available=progression_available,
        )
    except DurableExecutionError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    if row["state"] == "received" and progression_available:
        row = _reconcile_canonical_supplement(
            repository=repository,
            row=row,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
        )
    return ControllerCommandOut.model_validate(row)


@router.get("/{task_id}/controller-commands/{command_id}", response_model=ControllerCommandOut)
def get_controller_command(task_id: str, command_id: str, identity: RequestIdentity = Depends(resolve_request_identity)) -> ControllerCommandOut:
    repository = ControllerCommandRepository()
    row = repository.get(task_id=task_id, command_id=command_id, owner_id=identity["ownerId"], workspace_id=identity["workspaceId"])
    if not row:
        raise HTTPException(status_code=404, detail="controller command not found")
    row = _reconcile_canonical_supplement(
        repository=repository,
        row=row,
        owner_id=identity["ownerId"],
        workspace_id=identity["workspaceId"],
    )
    return ControllerCommandOut.model_validate(row)


@router.get("/{task_id}/controller-commands", response_model=ControllerCommandOut)
def find_controller_command(
    task_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ControllerCommandOut:
    normalized_key = idempotency_key.strip()
    if not normalized_key:
        raise HTTPException(status_code=422, detail="Idempotency-Key must not be blank")
    repository = ControllerCommandRepository()
    row = repository.get_by_idempotency_key(
        task_id=task_id,
        idempotency_key=normalized_key,
        owner_id=identity["ownerId"],
        workspace_id=identity["workspaceId"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="controller command not found")
    row = _reconcile_canonical_supplement(
        repository=repository,
        row=row,
        owner_id=identity["ownerId"],
        workspace_id=identity["workspaceId"],
    )
    return ControllerCommandOut.model_validate(row)
