'''PVD real-experiment feedback command boundary.'''

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.auth import RequestIdentity, resolve_request_identity
from app.config import settings
from app.models.research_orchestration import ResearchTaskV12
from app.modules.model_job_queue import (
    ModelJobConflict,
    ModelJobQueueRepository,
    ModelJobResourceNotFound,
)
from app.modules.pvd_scientific_state_materialization import (
    materialize_experiment_feedback,
)
from app.modules.research_orchestration_repository import (
    PersistenceBackendUnsupportedError,
    ResearchOrchestrationRepository,
    ResearchTaskNotFoundError,
    TaskRevisionConflictError,
)
from app.modules.research_scientific_state_authority import current_scientific_state
from app.modules.repeatable_definition_reentry import (
    reactivate_confirmed_definition_for_scientific_state,
)


router = APIRouter(prefix='/tasks', tags=['pvd-feedback'])
PVD_FEEDBACK_ANALYSIS_DEFINITION_ID = (
    'pvd:experimental_feedback_round_analysis'
)


class PvdFeedbackExperimentParameterOut(BaseModel):
    model_config = ConfigDict(extra='forbid')

    parameterKey: str
    displayName: str | None = None
    recommendedValue: str | int | float | bool | None = None
    unit: str | None = None


class PvdFeedbackExperimentOut(BaseModel):
    model_config = ConfigDict(extra='forbid')

    experimentRef: str
    hasActualExecution: bool
    hasMeasurements: bool
    parameters: list[PvdFeedbackExperimentParameterOut] = Field(default_factory=list)
    validationGoal: str | None = None
    failureSignal: str | None = None
    nextStep: str | None = None


class PvdFeedbackExperimentsOut(BaseModel):
    model_config = ConfigDict(extra='forbid')

    taskId: str
    scientificStateVersion: int
    experiments: list[PvdFeedbackExperimentOut] = Field(default_factory=list)


def _feedback_experiment_rows(task: ResearchTaskV12) -> list[dict[str, Any]]:
    state = current_scientific_state(task)
    if state is None:
        return []
    facts = getattr(state, 'scientific_facts', None)
    if isinstance(facts, Mapping):
        fact_rows = dict(facts)
    else:
        fact_rows = (
            facts.model_dump(by_alias=True)
            if hasattr(facts, 'model_dump')
            else {}
        )
    history = fact_rows.get('experiment_history')
    if not isinstance(history, list):
        return []
    rows: list[dict[str, Any]] = []
    for record in history:
        if not isinstance(record, Mapping):
            continue
        experiment_ref = record.get('experiment_ref')
        if not isinstance(experiment_ref, str) or not experiment_ref.strip():
            continue
        recommendation = record.get('system_recommendation')
        if not isinstance(recommendation, Mapping):
            recommendation = {}
        parameters: list[dict[str, Any]] = []
        for parameter in recommendation.get('parameters') or []:
            if not isinstance(parameter, Mapping):
                continue
            parameter_key = parameter.get('parameter_key')
            if not isinstance(parameter_key, str) or not parameter_key.strip():
                continue
            parameters.append(
                {
                    'parameterKey': parameter_key,
                    'displayName': parameter.get('display_name'),
                    'recommendedValue': parameter.get('recommended_value'),
                    'unit': parameter.get('unit'),
                }
            )
        actual_execution = record.get('actual_execution')
        measurements = record.get('measurements')
        rows.append(
            {
                'experimentRef': experiment_ref,
                'hasActualExecution': isinstance(actual_execution, Mapping),
                'hasMeasurements': isinstance(measurements, list) and bool(measurements),
                'parameters': parameters,
                'validationGoal': recommendation.get('validation_goal'),
                'failureSignal': recommendation.get('failure_signal'),
                'nextStep': recommendation.get('next_step'),
            }
        )
    return rows


@router.get(
    '/{task_id}/pvd/experiments',
    response_model=PvdFeedbackExperimentsOut,
)
def list_pvd_feedback_experiments(
    task_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> PvdFeedbackExperimentsOut:
    '''Read-only catalog of experiments that can receive user feedback.'''

    _require_api()
    repository = ResearchOrchestrationRepository()
    try:
        task = repository.load(
            task_id=task_id,
            owner_id=identity['ownerId'],
            workspace_id=identity['workspaceId'],
        )
    except ResearchTaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail='research task not found') from exc
    state = current_scientific_state(task)
    if state is None:
        raise HTTPException(status_code=409, detail='current_scientific_state_missing')
    return PvdFeedbackExperimentsOut(
        taskId=task_id,
        scientificStateVersion=state.scientific_state_version,
        experiments=_feedback_experiment_rows(task),
    )


class PvdExperimentFeedbackIn(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    expectedRevision: int = Field(ge=0)
    experimentRef: str = Field(min_length=1)
    sourceRef: str = Field(min_length=1)
    sourceOrigin: Literal['user', 'imported_project_record']
    plannedExecution: dict[str, Any] | None = None
    actualExecution: dict[str, Any] | None = None
    measurements: list[dict[str, Any]] = Field(default_factory=list)
    processEvents: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator('experimentRef', 'sourceRef')
    @classmethod
    def strip_identity(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError('feedback identity cannot be blank')
        return stripped

    @model_validator(mode='after')
    def require_feedback_fact(self) -> 'PvdExperimentFeedbackIn':
        if (
            self.plannedExecution is None
            and self.actualExecution is None
            and not self.measurements
            and not self.processEvents
        ):
            raise ValueError('at least one feedback fact is required')
        return self


class PvdExperimentFeedbackOut(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    taskId: str
    taskRevision: int = Field(ge=0)
    scientificStateVersion: int = Field(ge=1)
    experimentRef: str
    selectionRequestId: str
    state: str = 'accepted'


def _canonical_execution_in_flight(task: ResearchTaskV12) -> bool:
    return any(
        str(getattr(decision.status, 'value', decision.status))
        in {'selected', 'dispatched'}
        for decision in task.execution_decisions
    )


def _reject_caller_provenance(value: Any) -> None:
    if isinstance(value, Mapping):
        if 'provenance' in value:
            raise ValueError('pvd_experiment_feedback_provenance_is_server_owned')
        for nested in value.values():
            _reject_caller_provenance(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_caller_provenance(nested)


def _execution_fact(
    value: Mapping[str, Any] | None,
    *,
    provenance: Mapping[str, str],
) -> dict[str, Any] | None:
    if value is None:
        return None
    _reject_caller_provenance(value)
    fact = deepcopy(dict(value))
    parameters = fact.get('parameters')
    if isinstance(parameters, list):
        parameter_rows = parameters
    elif isinstance(parameters, dict):
        parameter_rows = list(parameters.values())
    else:
        raise ValueError('pvd_experiment_feedback_invalid')
    for parameter in parameter_rows:
        if not isinstance(parameter, dict):
            raise ValueError('pvd_experiment_feedback_invalid')
        parameter['provenance'] = dict(provenance)
    fact['provenance'] = dict(provenance)
    return fact


def _ref_facts(
    values: list[dict[str, Any]],
    *,
    provenance: Mapping[str, str],
) -> list[dict[str, Any]]:
    _reject_caller_provenance(values)
    rows = deepcopy(values)
    for row in rows:
        row['provenance'] = dict(provenance)
    return rows


def _feedback_facts(body: PvdExperimentFeedbackIn) -> dict[str, Any]:
    provenance = {'origin': body.sourceOrigin, 'source_ref': body.sourceRef}
    return {
        'experiment_ref': body.experimentRef,
        'provenance': provenance,
        'planned_execution': _execution_fact(
            body.plannedExecution,
            provenance=provenance,
        ),
        'actual_execution': _execution_fact(
            body.actualExecution,
            provenance=provenance,
        ),
        'measurements': _ref_facts(body.measurements, provenance=provenance),
        'process_events': _ref_facts(body.processEvents, provenance=provenance),
    }


def _require_api() -> None:
    if not settings.research_orchestration_v1:
        raise HTTPException(status_code=404, detail='research design API is disabled')
    if settings.persistence_backend != 'postgres':
        raise HTTPException(
            status_code=409,
            detail='PVD feedback API requires PostgreSQL',
        )


@router.post(
    '/{task_id}/pvd/experiment-feedback',
    response_model=PvdExperimentFeedbackOut,
    status_code=202,
)
def submit_pvd_experiment_feedback(
    task_id: str,
    body: PvdExperimentFeedbackIn,
    idempotency_key: str | None = Header(None, alias='Idempotency-Key'),
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> PvdExperimentFeedbackOut:
    '''Accept real PVD facts, append canonical state, and schedule continuation.'''

    _require_api()
    selection_request_id = (idempotency_key or '').strip()
    if not selection_request_id:
        raise HTTPException(status_code=428, detail='Idempotency-Key is required')
    repository = ResearchOrchestrationRepository()
    try:
        task = repository.load(
            task_id=task_id,
            owner_id=identity['ownerId'],
            workspace_id=identity['workspaceId'],
        )
        active = task.active_research_design_ref
        if active is None:
            raise ValueError('active_research_design_missing')
        before = current_scientific_state(task)
        if before is None:
            raise ValueError('current_scientific_state_missing')
        submitted_at = datetime.now(timezone.utc)
        materialized_payload = materialize_experiment_feedback(
            task_payload=task.model_dump(mode='json', by_alias=True),
            feedback_facts=_feedback_facts(body),
            source_ref=body.sourceRef,
            research_design_version=active.version,
            occurred_at=submitted_at,
        )
        materialized = ResearchTaskV12.model_validate(materialized_payload)
        after = current_scientific_state(materialized)
        if after is None:
            raise ValueError('current_scientific_state_missing')
        changed = after.scientific_state_version != before.scientific_state_version
        if changed:
            if _canonical_execution_in_flight(task):
                raise ValueError('pvd_experiment_feedback_execution_in_flight')
            materialized = reactivate_confirmed_definition_for_scientific_state(
                materialized,
                substage_definition_id=PVD_FEEDBACK_ANALYSIS_DEFINITION_ID,
                scientific_state_version=after.scientific_state_version,
                occurred_at=submitted_at,
                reason_code='pvd_experiment_feedback_received',
            )
            task = repository.save(
                materialized,
                owner_id=identity['ownerId'],
                workspace_id=identity['workspaceId'],
                expected_task_revision=body.expectedRevision,
            )
            after = current_scientific_state(task)
            if after is None:
                raise ValueError('current_scientific_state_missing')
        elif body.sourceRef not in after.source_refs:
            raise ValueError('pvd_experiment_feedback_idempotency_conflict')

        ModelJobQueueRepository().enqueue(
            resource_kind='task',
            resource_id=task_id,
            owner_id=identity['ownerId'],
            workspace_id=identity['workspaceId'],
            job_id=selection_request_id,
            job_kind='execution_selection',
            expected_revision=task.revision,
            command_payload={
                'requestKind': 'pvd_experiment_feedback',
                'invocationContext': {'experiment_ref': body.experimentRef},
                'invocationContextDefinitionId': PVD_FEEDBACK_ANALYSIS_DEFINITION_ID,
            },
        )
    except (ResearchTaskNotFoundError, ModelJobResourceNotFound) as exc:
        raise HTTPException(status_code=404, detail='research task not found') from exc
    except (
        TaskRevisionConflictError,
        ModelJobConflict,
        PersistenceBackendUnsupportedError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        code = str(exc)
        status_code = 409 if (
            'conflict' in code
            or code.endswith('_not_found')
            or code.endswith('_in_flight')
            or code.endswith('_missing')
        ) else 422
        raise HTTPException(status_code=status_code, detail=code) from exc

    return PvdExperimentFeedbackOut(
        taskId=task_id,
        taskRevision=task.revision,
        scientificStateVersion=after.scientific_state_version,
        experimentRef=body.experimentRef,
        selectionRequestId=selection_request_id,
    )


__all__ = [
    'PvdExperimentFeedbackIn',
    'PvdExperimentFeedbackOut',
    'router',
    'submit_pvd_experiment_feedback',
]
