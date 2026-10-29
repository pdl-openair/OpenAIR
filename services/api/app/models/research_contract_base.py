"""Shared strict research-contract base and nonempty string type."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


NonEmptyStr = Annotated[str, Field(min_length=1, strict=True)]


class ContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
        validate_assignment=True,
    )
