from __future__ import annotations

import pytest
import yaml

from app.modules.registry_snapshot_repository import (
    RegistrySnapshotIntegrityError,
    parse_spec_document,
)


def _method() -> dict:
    return {
        "methodId": "sample_role_method",
        "methodVersion": 1,
        "semantics": "role_graph_v1",
        "roles": [
            {
                "roleId": "collect_evidence",
                "requirement": "required",
                "dependsOn": [],
                "capabilityKinds": ["skill", "agent"],
                "releasePolicy": {"kind": "always"},
                "failurePolicy": "fail_closed",
                "parallelism": "single",
            },
            {
                "roleId": "synthesize_evidence",
                "requirement": "required",
                "dependsOn": ["collect_evidence"],
                "capabilityKinds": ["agent", "skill"],
                "releasePolicy": {"kind": "always"},
                "failurePolicy": "fail_closed",
                "parallelism": "single",
            },
        ],
        "completionRoleIds": ["synthesize_evidence"],
        "stopConditions": [],
        "qualityConstraints": [],
    }


def _spec(method: dict) -> str:
    return (
        "# Execution Method contract sample\n\n"
        "## Execution Method\n\n"
        "```yaml\n"
        + yaml.safe_dump(method, sort_keys=False, allow_unicode=True)
        + "```\n"
    )


def _parse(method: dict) -> dict:
    return parse_spec_document(
        "pvd",
        "specs/execution-method-contract.md",
        _spec(method),
    )


def test_parser_accepts_role_only_execution_method() -> None:
    method = _method()

    parsed = _parse(method)

    assert parsed["executionMethod"] == method


def test_parser_rejects_cyclic_role_dependencies() -> None:
    method = _method()
    method["roles"][0]["dependsOn"] = ["synthesize_evidence"]

    with pytest.raises(RegistrySnapshotIntegrityError, match="contains a cycle"):
        _parse(method)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("skillId", "hard_wired_skill"),
        ("agentId", "hard_wired_agent"),
        ("model", "hard_wired_model"),
    ],
)
def test_parser_rejects_concrete_capability_or_model_identity(
    field: str,
    value: str,
) -> None:
    method = _method()
    method["roles"][0][field] = value

    with pytest.raises(RegistrySnapshotIntegrityError):
        _parse(method)
