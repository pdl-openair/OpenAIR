"""Policy authority for automatic durable ResearchStart recovery."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlannerHttpRecoveryPolicy:
    """Bounded policy for explicit Provider HTTP failures during Planner."""

    max_automatic_recoveries: int = 1

    @staticmethod
    def is_eligible_status(status_code: int | None) -> bool:
        return bool(
            status_code in {408, 429}
            or (isinstance(status_code, int) and 500 <= status_code <= 599)
        )


PLANNER_HTTP_RECOVERY_POLICY = PlannerHttpRecoveryPolicy()


__all__ = [
    "PLANNER_HTTP_RECOVERY_POLICY",
    "PlannerHttpRecoveryPolicy",
]
