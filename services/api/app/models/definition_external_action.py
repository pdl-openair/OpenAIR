"""定义 Substage 无法省略的最低外部动作要求。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.models.external_action import ExternalActionKind


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


def _list_to_tuple(value: Any) -> Any:
    return tuple(value) if isinstance(value, list) else value


def _external_actions_to_tuple(value: Any) -> Any:
    if not isinstance(value, (list, tuple)):
        return value
    return tuple(
        item if isinstance(item, ExternalActionKind) else ExternalActionKind(item)
        for item in value
    )


class _ContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
        strict=True,
        frozen=True,
    )


Identifier = Annotated[
    str,
    Field(min_length=1, max_length=160, pattern=r"^[a-z][a-z0-9_:\-.]*$"),
]
PositiveVersion = Annotated[int, Field(ge=1)]
ExternalActionTuple = Annotated[
    tuple[ExternalActionKind, ...],
    BeforeValidator(_external_actions_to_tuple),
    Field(max_length=4),
]


class DefinitionExternalActionEntryV1(_ContractModel):
    """一项 Definition 无法省略的最低外部动作要求。"""

    substage_key: Identifier
    definition_version: PositiveVersion
    required_external_actions: ExternalActionTuple

    @model_validator(mode="after")
    def validate_actions(self) -> "DefinitionExternalActionEntryV1":
        if len(set(self.required_external_actions)) != len(
            self.required_external_actions
        ):
            raise ValueError("requiredExternalActions contains duplicates")
        return self


DefinitionExternalActionEntryTuple = Annotated[
    tuple[DefinitionExternalActionEntryV1, ...],
    BeforeValidator(_list_to_tuple),
    Field(min_length=1, max_length=256),
]


class DefinitionExternalActionCatalogV1(_ContractModel):
    """一个 pack 的 Definition 外部动作合同。"""

    schema_version: Literal["definition-external-actions.v1"]
    definitions: DefinitionExternalActionEntryTuple

    @model_validator(mode="after")
    def validate_unique_definitions(self) -> "DefinitionExternalActionCatalogV1":
        keys = [row.substage_key for row in self.definitions]
        if len(set(keys)) != len(keys):
            raise ValueError("substageKey values must be unique")
        return self


__all__ = [
    "DefinitionExternalActionCatalogV1",
    "DefinitionExternalActionEntryV1",
]
