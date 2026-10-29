"""Canonical call input persistence; no research or action is approved here."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from app.models.research_contract_base import ContractModel, NonEmptyStr


class CapabilityType(StrEnum):
    SKILL = "skill"
    TOOL = "tool"
    AGENT_RUNTIME = "agent_runtime"


class CapabilityCallStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXTERNAL_RESULT_UNKNOWN = "external_result_unknown"


Sha256Ref = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$", strict=True)]


class InternalCapabilityCallInput(ContractModel):
    contract_version: Literal[1]
    parent_capability_call_id: NonEmptyStr
    registry_snapshot_hash: Sha256Ref
    source_binding_ref: dict[str, JsonValue]
    parameters: dict[str, JsonValue]
    resolved_execution_policy: dict[str, JsonValue] | None
    execution_timeout_seconds: (
        Annotated[float, Field(gt=0, strict=True, allow_inf_nan=False)] | None
    )
    content_hash: Sha256Ref

    @field_validator("contract_version", mode="before")
    @classmethod
    def strict_version(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("internal CapabilityCall input version must be an integer")
        return value

    @model_validator(mode="after")
    def validate_frozen_input(self) -> "InternalCapabilityCallInput":
        ref = self.source_binding_ref
        if (
            set(ref) != {"bindingId", "bindingVersion"}
            or not isinstance(ref["bindingId"], str)
            or not ref["bindingId"]
            or isinstance(ref["bindingVersion"], bool)
            or not isinstance(ref["bindingVersion"], int)
            or ref["bindingVersion"] < 1
        ):
            raise ValueError("internal CapabilityCall Binding identity invalid")
        expected = frozen_input_content_hash(self.model_dump(mode="json", by_alias=True))
        if self.content_hash != expected:
            raise ValueError("internal CapabilityCall input hash mismatch")
        return self


def frozen_input_content_hash(snapshot: Mapping[str, Any]) -> str:
    body = {key: value for key, value in snapshot.items() if key != "contentHash"}
    encoded = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def validate_capability_call_lifecycle(call: Any) -> Any:
    active = call.status in {CapabilityCallStatus.QUEUED, CapabilityCallStatus.RUNNING}
    if active and call.finished_at is not None:
        raise ValueError("active CapabilityCall cannot have finishedAt")
    if not active and call.finished_at is None:
        raise ValueError("terminal CapabilityCall requires finishedAt")
    if call.finished_at is not None and call.finished_at < call.started_at:
        raise ValueError("CapabilityCall finishedAt cannot precede startedAt")
    return call


def validate_capability_call_work_units(
    calls: Sequence[Any], work_units: Mapping[str, Any]
) -> None:
    indexed = {row.capability_call_id: row for row in calls}
    for call in calls:
        unit = work_units.get(call.work_unit_id)
        if unit is None:
            raise ValueError("CapabilityCall references an unknown WorkUnit")
        if unit.source_mode.value != "live":
            raise ValueError("legacy CapabilityCall cannot enter a live research aggregate")
        if call.substage_instance_id != unit.substage_instance_id:
            raise ValueError("CapabilityCall Substage does not match its WorkUnit")
        frozen = call.internal_call_input_snapshot
        if frozen is None:
            continue
        parent = indexed.get(frozen.parent_capability_call_id)
        if (
            parent is None
            or parent.capability_call_id == call.capability_call_id
            or parent.work_unit_id != call.work_unit_id
            or parent.capability_type is not CapabilityType.SKILL
            or parent.capability_id != unit.executor_ref.executor_id
            or call.capability_type is not CapabilityType.SKILL
            or frozen.parent_capability_call_id not in call.input_refs
        ):
            raise ValueError("internal CapabilityCall parent responsibility mismatch")
        ref = frozen.source_binding_ref
        if (
            frozen.registry_snapshot_hash != unit.registry_snapshot_ref.content_hash
            or call.runtime_binding_id != f"registry:{ref['bindingId']}:{ref['bindingVersion']}"
        ):
            raise ValueError("internal CapabilityCall frozen authority mismatch")
