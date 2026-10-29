"""Assemble executable inputs and resource requests without claiming actual use."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from .plan_common import RESOURCE_PLACEHOLDER, AssemblyContext, join_workspace, safe_relative


def assemble_execution(
    execution: Mapping[str, Any],
    *,
    workspace_relative_path: Any,
    context: AssemblyContext,
) -> dict[str, Any]:
    entrypoint = (
        context.source_value(
            execution.get("entrypoint"),
            target="entrypoint",
        )
        if execution.get("entrypoint") is not None
        else None
    )
    if execution.get("entrypoint") is None:
        context.add_unresolved("entrypoint", "official_source_unspecified")

    arguments = (
        context.source_value(
            execution.get("arguments"),
            target="arguments",
        )
        if execution.get("arguments") is not None
        else None
    )
    if execution.get("arguments") is None:
        context.add_unresolved("arguments", "official_source_unspecified")

    if execution.get("working_directory") is None:
        working_source = "."
        context.system_selection(
            "working_directory",
            "official source does not specify a start directory; use fixed repository root",
        )
    else:
        working_source = context.source_value(
            execution.get("working_directory"),
            target="working_directory",
        )
    normalized_working = safe_relative(working_source) if working_source is not None else None
    working_directory = (
        join_workspace(workspace_relative_path, normalized_working)
        if normalized_working is not None
        else None
    )
    if working_directory is None:
        context.add_unresolved("working_directory", "workspace_path_invalid")

    if execution.get("timeout_seconds") is None:
        timeout_seconds = 3600
        context.system_selection(
            "timeout_seconds",
            "official source does not specify a run timeout; use bounded one-hour execution limit",
        )
    else:
        timeout_seconds = context.source_value(
            execution.get("timeout_seconds"),
            target="timeout_seconds",
        )

    if execution.get("environment_variables") is None:
        environment_variables: dict[str, str] | None = {}
        context.system_selection(
            "environment_variables",
            "official source does not require execution environment overrides",
        )
    else:
        raw_environment = context.source_value(
            execution.get("environment_variables"),
            target="environment_variables",
        )
        environment_variables = (
            dict(raw_environment) if isinstance(raw_environment, Mapping) else None
        )

    executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    context.system_selection(
        "executable",
        "execute with the Python interpreter created by the formal environment setup",
    )
    return {
        "entrypoint": entrypoint,
        "arguments": arguments,
        "executable": executable,
        "working_directory": working_directory,
        "timeout_seconds": timeout_seconds,
        "environment_variables": environment_variables,
    }


def assemble_resources(
    resources: list[Any],
    *,
    entrypoint: Any,
    arguments: Any,
    context: AssemblyContext,
) -> dict[str, Any]:
    requests: list[dict[str, Any]] = []
    binding_plan: list[dict[str, Any]] = []
    argv = (
        [entrypoint, *arguments]
        if isinstance(entrypoint, str) and isinstance(arguments, list)
        else None
    )
    bound_argv_indices: set[int] = set()

    for index, row in enumerate(resources):
        target = f"resources[{index}]"
        if not isinstance(row, Mapping) or not {
            "usage",
            "execution_usage",
            "resource_kind",
            "repo_id",
            "repo_type",
            "revision",
        }.issubset(row):
            context.add_unresolved(target, "resource_identity_request_invalid")
            continue
        before = len(context.unresolved)
        context.basis_for_excerpt(
            target=target,
            document_id=row.get("source_document_id"),
            excerpt=row.get("source_excerpt"),
        )
        if len(context.unresolved) != before:
            continue

        request = {
            "usage": row["usage"],
            "resource_kind": row["resource_kind"],
            "repo_id": row["repo_id"],
            "repo_type": row["repo_type"],
            "revision": row["revision"],
        }
        if row.get("filename") is not None:
            request["filename"] = row["filename"]
        if row.get("expected_commit_sha") is not None:
            request["expected_commit_sha"] = str(row["expected_commit_sha"]).lower()
        if row.get("expected_sha256") is not None:
            request["expected_sha256"] = str(row["expected_sha256"]).lower()
        request_index = len(requests)
        requests.append(request)
        if context.provenance and context.provenance[-1].get("target") == target:
            context.provenance[-1]["target"] = f"resource_acquisition_requests[{request_index}]"

        delivery = row.get("delivery")
        if not isinstance(delivery, Mapping):
            context.add_unresolved(target + ".delivery", "resource_delivery_unknown")
            continue
        execution_usage = row["execution_usage"]
        if delivery.get("kind") == "command_argument":
            argument_index = delivery.get("argument_index")
            if (
                argv is None
                or not isinstance(argument_index, int)
                or isinstance(argument_index, bool)
                or argument_index < 0
                or argument_index >= len(arguments)
            ):
                context.add_unresolved(target + ".delivery", "resource_argument_index_invalid")
                continue
            argv_index = argument_index + 1
            if argv_index in bound_argv_indices:
                context.add_unresolved(target + ".delivery", "resource_argument_index_conflict")
                continue
            resource_arguments = {row["repo_id"]}
            if isinstance(row.get("filename"), str):
                resource_arguments.add(row["filename"])
            if argv[argv_index] not in resource_arguments:
                context.add_unresolved(target + ".delivery", "resource_argument_target_mismatch")
                continue
            argv[argv_index] = RESOURCE_PLACEHOLDER.get(execution_usage, row["repo_id"])
            bound_argv_indices.add(argv_index)
            binding_plan.append(
                {
                    "usage": execution_usage,
                    "resource_request_index": request_index,
                    "argv_index": argv_index,
                }
            )
        elif delivery.get("kind") == "tar_extract_working_directory":
            binding_plan.append(
                {
                    "usage": execution_usage,
                    "resource_request_index": request_index,
                    "delivery": {"kind": "tar_extract_working_directory"},
                }
            )
        else:
            context.add_unresolved(target + ".delivery", "resource_delivery_unknown")

    return {
        "resource_acquisition_requests": requests,
        "execution_resource_binding_plan": binding_plan,
        "argv": argv,
    }
