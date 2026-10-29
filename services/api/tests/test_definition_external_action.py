from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.models.definition_external_action import DefinitionExternalActionCatalogV1
from app.models.external_action import ExternalActionKind
from app.modules.definition_external_action_source import (
    DefinitionExternalActionSourceError,
    load_definition_external_action_contracts,
)


def _payload() -> dict:
    return {
        "schemaVersion": "definition-external-actions.v1",
        "definitions": [
            {
                "substageKey": "source_acquisition",
                "definitionVersion": 1,
                "requiredExternalActions": ["external_read"],
            },
            {
                "substageKey": "findings_synthesis",
                "definitionVersion": 1,
                "requiredExternalActions": [],
            },
        ],
    }


def _write_catalog(root: Path, payload: dict) -> Path:
    path = root / "schemas" / "test" / "substage-external-actions.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def test_external_action_kind_is_the_minimal_four_kind_contract() -> None:
    assert tuple(action.value for action in ExternalActionKind) == (
        "external_read",
        "external_compute",
        "external_write",
        "physical_action",
    )


def test_definition_external_action_catalog_is_strict_and_immutable() -> None:
    catalog = DefinitionExternalActionCatalogV1.model_validate(_payload())

    assert catalog.definitions[0].required_external_actions == (
        ExternalActionKind.EXTERNAL_READ,
    )
    with pytest.raises(ValidationError, match="frozen"):
        catalog.definitions[0].definition_version = 2
    with pytest.raises(AttributeError):
        catalog.definitions.pop()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["definitions"][0].update(
            {"requiredExternalActions": ["unknown_action"]}
        ),
        lambda payload: payload["definitions"][0].update(
            {"requiredExternalActions": ["external_read", "external_read"]}
        ),
        lambda payload: payload["definitions"].append(payload["definitions"][0]),
    ],
)
def test_definition_external_action_catalog_rejects_invalid_contracts(
    mutate,
) -> None:
    payload = _payload()
    mutate(payload)

    with pytest.raises(ValidationError):
        DefinitionExternalActionCatalogV1.model_validate(payload)


def test_definition_external_action_source_binds_exact_versions(
    tmp_path: Path
) -> None:
    path = _write_catalog(tmp_path, _payload())
    result = load_definition_external_action_contracts(
        "test",
        {"source_acquisition": 1, "findings_synthesis": 1},
        repo_root=tmp_path,
        tracked_paths={path.relative_to(tmp_path).as_posix()},
    )

    assert dict(result) == {
        "source_acquisition": (ExternalActionKind.EXTERNAL_READ,),
        "findings_synthesis": (),
    }


def test_definition_external_action_source_rejects_version_mismatch(
    tmp_path: Path
) -> None:
    path = _write_catalog(tmp_path, _payload())
    with pytest.raises(
        DefinitionExternalActionSourceError,
        match="definition_external_actions_version_mismatch",
    ):
        load_definition_external_action_contracts(
            "test",
            {"source_acquisition": 2, "findings_synthesis": 1},
            repo_root=tmp_path,
            tracked_paths={path.relative_to(tmp_path).as_posix()},
        )


def test_definition_external_action_source_rejects_incomplete_coverage(
    tmp_path: Path
) -> None:
    payload = _payload()
    payload["definitions"].pop()
    path = _write_catalog(tmp_path, payload)
    with pytest.raises(
        DefinitionExternalActionSourceError,
        match="definition_external_actions_coverage_mismatch",
    ):
        load_definition_external_action_contracts(
            "test",
            {"source_acquisition": 1, "findings_synthesis": 1},
            repo_root=tmp_path,
            tracked_paths={path.relative_to(tmp_path).as_posix()},
        )


def test_definition_external_action_source_rejects_duplicate_yaml_keys(
    tmp_path: Path,
) -> None:
    path = tmp_path / "schemas" / "test" / "substage-external-actions.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """schemaVersion: definition-external-actions.v1
schemaVersion: definition-external-actions.v1
definitions: []
""",
        encoding="utf-8",
    )

    with pytest.raises(
        DefinitionExternalActionSourceError,
        match="invalid_definition_external_actions",
    ):
        load_definition_external_action_contracts(
            "test",
            {"source_acquisition": 1},
            repo_root=tmp_path,
            tracked_paths={path.relative_to(tmp_path).as_posix()},
        )


@pytest.mark.parametrize("pack", ["", ".", "..", "../escape", "a/b", "a\\b"])
def test_definition_external_action_source_rejects_invalid_pack(
    tmp_path: Path,
    pack: str,
) -> None:
    with pytest.raises(
        DefinitionExternalActionSourceError,
        match="invalid_definition_external_actions_pack",
    ):
        load_definition_external_action_contracts(
            pack,
            {"source_acquisition": 1},
            repo_root=tmp_path,
            tracked_paths=set(),
        )


def test_definition_external_action_source_rejects_symlink(
    tmp_path: Path,
) -> None:
    target_root = tmp_path / "target"
    target = _write_catalog(target_root, _payload())
    path = tmp_path / "schemas" / "test" / "substage-external-actions.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("当前文件系统不支持符号链接")

    with pytest.raises(
        DefinitionExternalActionSourceError,
        match="symlink_definition_external_actions",
    ):
        load_definition_external_action_contracts(
            "test",
            {"source_acquisition": 1, "findings_synthesis": 1},
            repo_root=tmp_path,
            tracked_paths={path.relative_to(tmp_path).as_posix()},
        )


def test_definition_external_action_source_rejects_untracked_contract(
    tmp_path: Path
) -> None:
    _write_catalog(tmp_path, _payload())
    with pytest.raises(
        DefinitionExternalActionSourceError,
        match="untracked_definition_external_actions",
    ):
        load_definition_external_action_contracts(
            "test",
            {"source_acquisition": 1, "findings_synthesis": 1},
            repo_root=tmp_path,
            tracked_paths=set(),
        )




def test_materials_stability_contract_does_not_force_remote_compute() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    definition_versions = {
        "database_search": 1,
        "candidate_generation": 1,
        "parameter_recommendation": 1,
        "stability_verification": 1,
        "synthesis_execution": 1,
        "characterization": 1,
    }

    contracts = load_definition_external_action_contracts(
        "materials",
        definition_versions,
        repo_root=repo_root,
        tracked_paths={"schemas/materials/substage-external-actions.yaml"},
    )

    assert contracts["stability_verification"] == ()


def test_missing_definition_external_action_source_remains_explicitly_unknown(
    tmp_path: Path
) -> None:
    result = load_definition_external_action_contracts(
        "legacy",
        {"legacy_substage": 1},
        repo_root=tmp_path,
        tracked_paths=set(),
    )

    assert dict(result) == {"legacy_substage": None}
