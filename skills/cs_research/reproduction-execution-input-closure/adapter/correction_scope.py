"""Allow measured plan corrections within original action targets and pins."""

import shlex
from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256

from adapter.input_materialization import project_design_execution_fields

_RESOURCE_CONSTRAINTS = (
    "usage",
    "resource_kind",
    "repo_id",
    "repo_type",
    "revision",
    "filename",
    "expected_commit_sha",
    "expected_sha256",
)


class RepairCorrectionScopeError(ValueError):
    """The proposed plan changes the existing authorized action scope."""


def _resource_constraints(plan):
    rows = plan.get("resource_acquisition_requests")
    if not isinstance(rows, list):
        raise RepairCorrectionScopeError("repair_resource_scope_missing")
    result = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("usage"), str):
            raise RepairCorrectionScopeError("repair_resource_scope_invalid")
        if row["usage"] in result:
            raise RepairCorrectionScopeError("repair_resource_scope_ambiguous")
        result[row["usage"]] = {key: row.get(key) for key in _RESOURCE_CONSTRAINTS}
    return result


def _program(plan):
    argv = plan.get("argv")
    if not isinstance(argv, list) or not argv or not isinstance(argv[0], str):
        raise RepairCorrectionScopeError("repair_original_program_missing")
    try:
        tokens = shlex.split(argv[0])
    except ValueError as exc:
        raise RepairCorrectionScopeError("repair_original_program_invalid") from exc
    # Normalize the actual legacy interpreter-prefixed entrypoint representation.
    # No shell execution, heuristic program selection or changed entry is allowed.
    if len(tokens) == 2 and tokens[0] in {"python", "python3", "python.exe"}:
        return tokens[1].replace("\\", "/")
    return argv[0].replace("\\", "/")


def _source_does_not_pin_revision(original, usage, source):
    if not isinstance(source, Mapping) or {
        "repository": source.get("repository"),
        "resolved_commit_sha": source.get("resolved_commit_sha"),
    } != original.get("source_identity"):
        return False
    rows = original["resource_acquisition_requests"]
    index = next(index for index, row in enumerate(rows) if row["usage"] == usage)
    request = rows[index]
    bases = [
        row
        for row in original.get("execution_plan_provenance", [])
        if row.get("target") == f"resource_acquisition_requests[{index}]"
        and row.get("origin") == "official_source"
    ]
    # Legacy source interpretation encoded an unspecified revision as this
    # sentinel. All actual revision strings and explicit content pins remain
    # constraints; lack of a literal in README is not permission to drop them.
    if len(bases) != 1 or request.get("revision") != "unknown":
        return False
    basis = bases[0]
    documents = [
        row
        for row in source.get("documents", [])
        if row.get("document_id") == basis.get("document_id")
    ]
    if len(documents) != 1:
        return False
    document = documents[0]
    content = document.get("content")
    excerpt = basis.get("locator", {}).get("excerpt")
    return (
        isinstance(content, str)
        and isinstance(excerpt, str)
        and bool(excerpt)
        and document.get("sha256") == sha256(content.encode("utf-8")).hexdigest()
        and basis.get("sha256") == document.get("sha256")
        and content.count(excerpt) == 1
        and request["repo_id"] in excerpt
        and request["revision"] not in excerpt
    )


def validate_plan_correction(original, revised, *, original_source=None):
    if (
        not isinstance(original, Mapping)
        or not isinstance(revised, Mapping)
        or revised.get("schema_version") != "openair-execution-plan/v1"
        or revised.get("state") != "succeeded"
    ):
        raise RepairCorrectionScopeError("repair_corrected_plan_invalid")
    if revised.get("source_identity") != original.get("source_identity"):
        raise RepairCorrectionScopeError("repair_corrected_code_target_changed")
    old_resources = _resource_constraints(original)
    new_resources = _resource_constraints(revised)
    for usage, row in new_resources.items():
        old = old_resources.get(usage)
        if (
            old is not None
            and row["revision"] is None
            and old["revision"] is not None
            and _source_does_not_pin_revision(original, usage, original_source)
        ):
            # Explicitly request an observed provider default. The adapter keeps
            # requested_revision=None and pins the actual resolved commit before
            # download. It does not turn an original string into invented main.
            old["revision"] = None
    if new_resources != old_resources:
        raise RepairCorrectionScopeError("repair_corrected_resource_target_or_pin_changed")
    if _program(revised) != _program(original) or revised.get("executable") != original.get(
        "executable"
    ):
        raise RepairCorrectionScopeError("repair_corrected_execution_program_changed")


def install_verified_correction(original_inputs, revised, context, *, original_source=None):
    """Called only with a decoded, integrity-checked formal assembler artifact.

    This does not assert plan completeness or readiness and grants no I/O. Each
    later action still passes the canonical executor's current fence and budget.
    Original parameters remain immutable historical evidence.
    """
    validate_plan_correction(
        original_inputs.get("execution_plan_facts"),
        revised,
        original_source=original_source,
    )
    design = project_design_execution_fields(
        original_inputs.get("reproduction_experiment_design"), revised
    )
    context["execution_plan_facts"] = deepcopy(dict(revised))
    context["reproduction_experiment_design"] = design
