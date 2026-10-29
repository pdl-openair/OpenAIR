"""Preserve received execution identities; only required input keys are checked."""
from __future__ import annotations
from copy import deepcopy
from typing import Any


def validate_code_identity(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    if raw is None:
        return None, None
    required = {"state", "repository", "requested_revision", "resolved_commit_sha", "verified_head", "workspace_relative_path", "execution_evidence"}
    if not isinstance(raw, dict) or not required.issubset(raw):
        return None, "code_identity_keys_missing"
    return deepcopy(raw), None


def validate_resource_identities(raw: Any) -> tuple[list[dict[str, Any]] | None, str | None]:
    if not isinstance(raw, list):
        return None, "resource_identities_structure_invalid"
    required = {"state", "resource_kind", "repo_id", "repo_type", "requested_revision", "resolved_commit_sha", "actual_identity", "expected_identity_verification", "workspace_relative_path", "execution_evidence"}
    if any(not isinstance(item, dict) or not required.issubset(item) for item in raw):
        return None, "resource_identity_keys_missing"
    return deepcopy(raw), None


def validate_environment_record(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    if raw is None:
        return None, None
    return (deepcopy(raw), None) if isinstance(raw, dict) else (None, "environment_record_structure_invalid")


def validate_execution_record(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    if raw is None:
        return None, None
    return (deepcopy(raw), None) if isinstance(raw, dict) else (None, "execution_record_structure_invalid")
