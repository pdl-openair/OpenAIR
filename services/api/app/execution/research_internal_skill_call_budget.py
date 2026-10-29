"""Project reservations from canonical calls and physical Provider evidence."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.models.research_orchestration import ResearchTaskV12


class InternalCallBudgetError(ValueError):
    pass


@dataclass(frozen=True)
class InternalCallBudget:
    model_calls: int
    tool_calls: int
    execution_seconds: float
    cost_amount: float | None


def _union_seconds(intervals: list[tuple[datetime, datetime]]) -> float:
    total = 0.0
    right = None
    for start, end in sorted(intervals):
        if end < start:
            raise InternalCallBudgetError("internal_call_time_evidence_invalid")
        if right is None or start >= right:
            total += (end - start).total_seconds()
        elif end > right:
            total += (end - right).total_seconds()
        right = max(right, end) if right is not None else end
    return total


def project_internal_call_budget(
    task: ResearchTaskV12,
    *,
    runtime_kinds: Mapping[str, str],
    provider_attempt_counts: Mapping[str, int],
    observed_at: datetime,
    cost_amount: float | None,
) -> InternalCallBudget:
    """Retain reservations for admitted calls, including known failures.

    One admitted atomic LLM call reserves at least one model attempt. More
    physical attempts and reasoning-adapter children are counted from the
    Provider ledger. This is a conservative reservation, not a success count.
    Nested call intervals are unioned so parent/child time is not charged twice.
    """
    models = tools = 0
    intervals = []
    ids = {row.capability_call_id for row in task.capability_calls}
    if set(provider_attempt_counts) - ids:
        raise InternalCallBudgetError("internal_call_budget_foreign_attempt")
    for call in task.capability_calls:
        kind = runtime_kinds.get(call.capability_call_id)
        if kind not in {"deterministic", "external_adapter", "llm_reasoning", "reasoning_adapter"}:
            raise InternalCallBudgetError("internal_call_budget_runtime_evidence_missing")
        attempts = provider_attempt_counts.get(call.capability_call_id, 0)
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
            raise InternalCallBudgetError("internal_call_budget_attempt_evidence_invalid")
        models += max(1 if kind == "llm_reasoning" else 0, attempts)
        tools += 1
        if call.status.value != "queued":
            end = call.finished_at or observed_at
            if end > observed_at:
                raise InternalCallBudgetError("internal_call_budget_future_time")
            intervals.append((call.started_at, end))
    return InternalCallBudget(models, tools, _union_seconds(intervals), cost_amount)


def require_internal_call_budget(
    *,
    boundary: Any,
    consumed: InternalCallBudget,
    runtime_kind: str,
    maximum_call_cost: float | None = None,
    reserve_new_call: bool = True,
) -> float | None:
    """Admit one atomic call and return the remaining execution-time ceiling."""
    if runtime_kind not in {"deterministic", "external_adapter", "llm_reasoning"}:
        raise InternalCallBudgetError("internal_call_child_must_be_atomic")
    additional_models = int(reserve_new_call and runtime_kind == "llm_reasoning")
    checks = (
        (boundary.max_model_calls, consumed.model_calls + additional_models, "model"),
        (boundary.max_tool_calls, consumed.tool_calls + int(reserve_new_call), "tool"),
    )
    for maximum, reserved, name in checks:
        if maximum is not None and reserved > maximum:
            raise InternalCallBudgetError(f"internal_call_{name}_budget_exhausted")
    if boundary.max_cost_amount is not None:
        if consumed.cost_amount is None or maximum_call_cost is None:
            raise InternalCallBudgetError("internal_call_cost_evidence_missing")
        if maximum_call_cost < 0:
            raise InternalCallBudgetError("internal_call_cost_evidence_invalid")
        if consumed.cost_amount + maximum_call_cost > boundary.max_cost_amount:
            raise InternalCallBudgetError("internal_call_cost_budget_exhausted")
    if boundary.max_wall_time_sec is None:
        return None
    remaining = boundary.max_wall_time_sec - consumed.execution_seconds
    if remaining <= 0:
        raise InternalCallBudgetError("internal_call_time_budget_exhausted")
    return remaining
