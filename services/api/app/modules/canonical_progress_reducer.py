"""Pure, durable-fact progress aggregation for research orchestration."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping

_ACTIVE_STATUSES = frozenset({"queued", "running", "reviewing"})
_TERMINAL_SUCCESS_STATUSES = frozenset({"completed", "skipped"})
_AWAITING_CONFIRMATION_STATUSES = frozenset({"needs_input"})
_DURABLE_PROGRESSION_PAUSE_REASONS = frozenset(
    {"stage_confirmation_required", "manual_step_confirmation_required"}
)


def _value(row: Any, *names: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        for name in names:
            if name in row:
                return row[name]
        return default
    for name in names:
        if hasattr(row, name):
            return getattr(row, name)
    return default


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", value)


def is_durable_progression_pause(status: Any, reason_code: Any) -> bool:
    """Identify the progression pause already committed by the authority."""
    return (
        _text(status) == "ready"
        and _text(reason_code) in _DURABLE_PROGRESSION_PAUSE_REASONS
    )


def _json_projection(row: Any) -> Any:
    if hasattr(row, "model_dump"):
        return row.model_dump(mode="json", by_alias=True)
    return row


class CanonicalProgressReducer:
    """Project canonical lifecycle state and its durable supporting references."""

    def reduce(
        self,
        *,
        revision: int,
        active_design: Any | None,
        substage_runtime_states: Iterable[Any],
        work_units: Iterable[Any],
        capability_calls: Iterable[Any],
        artifacts: Iterable[Any],
        confirmations: Iterable[Any],
        task_status: Any,
        agent_interactions: Iterable[Any] = (),
    ) -> dict[str, Any]:
        instances = list(_value(active_design, "instances", default=[]) or [])
        runtimes = list(substage_runtime_states)
        units = list(work_units)
        artifacts = list(artifacts)

        units_by_instance = self._by_instance(units)
        artifacts_by_instance = self._by_instance(artifacts)
        runtime_by_instance = {
            self._instance_key(runtime): runtime
            for runtime in runtimes
            if self._instance_key(runtime) is not None
        }

        substage_progress = []
        for instance in instances:
            key = self._instance_key(instance)
            runtime = runtime_by_instance.get(key)
            status, reason = self._substage_status(runtime)
            substage_progress.append(
                {
                    "substageInstanceId": _value(instance, "substage_instance_id", "substageInstanceId", "instance_id", "instanceId"),
                    "instanceRevision": _value(instance, "instance_revision", "instanceRevision", default=1),
                    "substageDefinitionId": _value(instance, "substage_definition_id", "substageDefinitionId"),
                    "stageKey": _text(_value(instance, "stage_key", "stageKey", "stage")),
                    "label": _value(instance, "label", "title", default=""),
                    "status": status,
                    "statusReasonCode": reason,
                    "activeExecutionDecisionId": _value(runtime, "active_execution_decision_id", "activeExecutionDecisionId"),
                    "workUnitIds": sorted(
                        _text(_value(unit, "work_unit_id", "workUnitId", "id"))
                        for unit in units_by_instance.get(key, [])
                    ),
                    "artifactRefs": sorted(
                        _text(_value(artifact, "artifact_id", "artifactId", "id"))
                        for artifact in artifacts_by_instance.get(key, [])
                    ),
                }
            )

        stage_progress = self._stage_progress(substage_progress)
        canonical_task_status = _text(task_status)
        if canonical_task_status is None:
            raise ValueError("canonical task status is required")
        return {
            "revision": revision,
            "substageProgress": substage_progress,
            "stageProgress": stage_progress,
            "taskProgress": {
                "status": canonical_task_status,
                "activeStageKeys": self._active_stage_keys(substage_progress, units),
                "awaitingConfirmationRefs": (
                    self._awaiting_confirmation_refs(substage_progress)
                    if canonical_task_status == "awaiting_confirmation"
                    else []
                ),
            },
            "workbenchProjection": self._workbench_projection(
                units,
                artifacts,
                agent_interactions,
                execution_visible=canonical_task_status == "running",
            ),
        }

    @staticmethod
    def _instance_key(row: Any) -> tuple[str, int] | None:
        instance_id = _value(row, "substage_instance_id", "substageInstanceId", "instance_id", "instanceId")
        revision = _value(row, "instance_revision", "instanceRevision", "substage_instance_revision", "substageInstanceRevision", default=1)
        if instance_id is None:
            return None
        return str(instance_id), int(revision)

    @classmethod
    def _by_instance(cls, rows: Iterable[Any]) -> dict[tuple[str, int], list[Any]]:
        grouped: dict[tuple[str, int], list[Any]] = defaultdict(list)
        for row in rows:
            key = cls._instance_key(row)
            if key is not None:
                grouped[key].append(row)
        return grouped

    @staticmethod
    def _substage_status(runtime: Any) -> tuple[str, str | None]:
        runtime_status = _text(_value(runtime, "status")) or "planned"
        # Lifecycle convergence owns this value. Lower-level execution rows are
        # supporting history and cannot reopen or override canonical state.
        return runtime_status, _value(runtime, "status_reason_code", "statusReasonCode")

    @staticmethod
    def _stage_progress(substages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_stage: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for substage in substages:
            if substage["stageKey"]:
                by_stage[substage["stageKey"]].append(substage)
        progress = []
        for stage_key in sorted(by_stage):
            rows = by_stage[stage_key]
            statuses = {row["status"] for row in rows}
            if statuses <= _TERMINAL_SUCCESS_STATUSES:
                status = "completed"
            elif "needs_input" in statuses:
                status = "needs_input"
            elif statuses & (_ACTIVE_STATUSES | {"ready"}):
                status = "running"
            elif "blocked_recoverable" in statuses:
                status = "blocked_recoverable"
            elif "insufficient_evidence" in statuses:
                status = "insufficient_evidence"
            elif statuses == {"planned"}:
                status = "not_started"
            elif statuses & {"failed", "cancelled"}:
                status = "failed"
            else:
                status = "invariant_error"
            progress.append(
                {
                    "stageKey": stage_key,
                    "status": status,
                    "substageInstanceRefs": [row["substageInstanceId"] for row in rows],
                    "completedCount": sum(row["status"] in _TERMINAL_SUCCESS_STATUSES for row in rows),
                    "totalCount": len(rows),
                    "artifactRefs": sorted({ref for row in rows for ref in row["artifactRefs"]}),
                    "statusReasonCode": None,
                }
            )
        return progress

    @staticmethod
    def _active_stage_keys(substages: list[dict[str, Any]], work_units: list[Any]) -> list[str]:
        active_instances = {
            CanonicalProgressReducer._instance_key(unit)
            for unit in work_units
            if _text(_value(unit, "status")) in _ACTIVE_STATUSES
        }
        return sorted(
            row["stageKey"]
            for row in substages
            if (row["substageInstanceId"], row["instanceRevision"]) in active_instances and row["stageKey"]
        )

    @staticmethod
    def _awaiting_confirmation_refs(substages: list[dict[str, Any]]) -> list[str]:
        return sorted(
            row["substageInstanceId"]
            for row in substages
            if row["status"] in _AWAITING_CONFIRMATION_STATUSES
            or is_durable_progression_pause(row["status"], row["statusReasonCode"])
        )

    @staticmethod
    def _workbench_projection(
        work_units: list[Any],
        artifacts: list[Any],
        interactions: Iterable[Any],
        *,
        execution_visible: bool,
    ) -> dict[str, Any]:
        live_agent_units = [
            unit for unit in work_units
            if _text(_value(unit, "source_mode", "sourceMode", default="live")) == "live"
            and _text(_value(unit, "executor_ref", "executorRef", default={}),) != "skill"
            and _text(_value(_value(unit, "executor_ref", "executorRef", default={}), "executor_type", "executorType")) != "skill"
            and _text(_value(unit, "strategy")) != "direct_skill"
        ]
        collaboration_nodes = [
            {
                "workUnitId": _value(unit, "work_unit_id", "workUnitId", "id"),
                "agentId": _value(_value(unit, "responsible_actor_ref", "responsibleActorRef", default={}), "actor_id", "actorId"),
                "status": _text(_value(unit, "status")),
            }
            for unit in live_agent_units
        ] if execution_visible else []
        tool_records = [
            {
                "workUnitId": _value(unit, "work_unit_id", "workUnitId", "id"),
                "kind": "skill",
            }
            for unit in work_units
            if _text(_value(unit, "strategy")) == "direct_skill"
            or _text(_value(_value(unit, "executor_ref", "executorRef", default={}), "executor_type", "executorType")) == "skill"
        ]
        return {
            "collaborationNodes": collaboration_nodes,
            "collaborationEvents": [_json_projection(interaction) for interaction in interactions],
            "toolResourceRecords": tool_records,
            "artifactRefs": sorted(_value(row, "artifact_id", "artifactId", "id") for row in artifacts),
        }
