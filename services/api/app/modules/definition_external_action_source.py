"""从正式 YAML 读取 Definition 的外部动作合同。"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import AbstractSet, Mapping

import yaml
from pydantic import ValidationError

from app.models.definition_external_action import DefinitionExternalActionCatalogV1
from app.models.external_action import ExternalActionKind
from app.modules.yaml_contract_loader import load_unique_key_yaml


class DefinitionExternalActionSourceError(ValueError):
    """外部动作合同来源无效，并保留稳定错误码。"""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


def load_definition_external_action_contracts(
    pack: str,
    definition_versions: Mapping[str, int],
    *,
    repo_root: Path,
    tracked_paths: AbstractSet[str],
) -> Mapping[str, tuple[ExternalActionKind, ...] | None]:
    """按 Definition 版本加载最低动作合同；缺少来源时返回 unknown。"""
    if (
        not pack
        or pack in {".", ".."}
        or "/" in pack
        or "\\" in pack
    ):
        raise DefinitionExternalActionSourceError(
            "invalid_definition_external_actions_pack", pack
        )

    relative_path = f"schemas/{pack}/substage-external-actions.yaml"
    path = repo_root / relative_path
    if not path.exists():
        return MappingProxyType(
            {substage: None for substage in definition_versions}
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise DefinitionExternalActionSourceError(
            "invalid_definition_external_actions", pack
        ) from exc
    if resolved != path.absolute() or not resolved.is_file():
        raise DefinitionExternalActionSourceError(
            "symlink_definition_external_actions", pack
        )
    if relative_path not in tracked_paths:
        raise DefinitionExternalActionSourceError(
            "untracked_definition_external_actions", pack
        )

    try:
        payload = load_unique_key_yaml(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise DefinitionExternalActionSourceError(
            "invalid_definition_external_actions", pack
        ) from exc
    if not isinstance(payload, Mapping):
        raise DefinitionExternalActionSourceError(
            "invalid_definition_external_actions", pack
        )
    try:
        catalog = DefinitionExternalActionCatalogV1.model_validate(dict(payload))
    except ValidationError as exc:
        raise DefinitionExternalActionSourceError(
            "invalid_definition_external_actions", pack
        ) from exc

    by_substage = {row.substage_key: row for row in catalog.definitions}
    expected = set(definition_versions)
    actual = set(by_substage)
    if actual != expected:
        missing = ",".join(sorted(expected - actual))
        extra = ",".join(sorted(actual - expected))
        raise DefinitionExternalActionSourceError(
            "definition_external_actions_coverage_mismatch",
            f"{pack}:missing={missing}:extra={extra}",
        )

    for substage, expected_version in definition_versions.items():
        actual_version = by_substage[substage].definition_version
        if actual_version != expected_version:
            raise DefinitionExternalActionSourceError(
                "definition_external_actions_version_mismatch",
                (
                    f"{pack}:{substage}:definition={expected_version}:"
                    f"contract={actual_version}"
                ),
            )

    return MappingProxyType(
        {
            substage: by_substage[substage].required_external_actions
            for substage in definition_versions
        }
    )


__all__ = [
    "DefinitionExternalActionSourceError",
    "load_definition_external_action_contracts",
]
