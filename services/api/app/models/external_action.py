"""定义 OpenAIR 使用的四类外部动作。"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class ExternalActionKind(StrEnum):
    EXTERNAL_READ = "external_read"
    EXTERNAL_COMPUTE = "external_compute"
    EXTERNAL_WRITE = "external_write"
    PHYSICAL_ACTION = "physical_action"


EXTERNAL_ACTION_KIND_VALUES: frozenset[str] = frozenset(
    action.value for action in ExternalActionKind
)


def missing_external_action_kinds(
    required: Iterable[ExternalActionKind],
    allowed: Iterable[ExternalActionKind],
) -> frozenset[ExternalActionKind]:
    """返回尚未被边界允许的动作类型。"""
    return frozenset(required) - frozenset(allowed)


__all__ = [
    "EXTERNAL_ACTION_KIND_VALUES",
    "ExternalActionKind",
    "missing_external_action_kinds",
]
