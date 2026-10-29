"""Assemble environment inputs that match the existing environment-setup contract."""
from __future__ import annotations

import sys
from typing import Any, Mapping

from .plan_common import AssemblyContext

def _source_items(
    raw: Any,
    *,
    target: str,
    context: AssemblyContext,
    required: bool,
) -> list[str] | None:
    if raw is None:
        if required:
            context.add_unresolved(target, "official_source_unspecified")
            return None
        return []
    if not isinstance(raw, list):
        context.add_unresolved(target, "source_value_invalid")
        return None if required else []

    values: list[str] = []
    for index, fact in enumerate(raw):
        item_target = f"{target}[{index}]"
        value = context.source_value(fact, target=item_target, )
        if value is None:
            continue
        values.append(value)
    if required and not values:
        context.add_unresolved(target, "no_valid_source_values")
        return None
    return values


def assemble_environment(
    environment: Mapping[str, Any],
    context: AssemblyContext,
) -> dict[str, Any]:
    python_fact = environment.get("python_version")
    if python_fact is None:
        python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
        context.system_selection(
            "python_version",
            "official source does not specify Python; use current worker major.minor for this execution",
        )
    else:
        python_version = context.source_value(
            python_fact,
            target="python_version",

        )

    requirements = _source_items(
        environment.get("requirements"),
        target="requirements",
        context=context,

        required=True,
    )
    verify_imports = _source_items(
        environment.get("verify_imports"),
        target="verify_imports",
        context=context,

        required=True,
    )
    raw_find_links = environment.get("find_links")
    if raw_find_links is None:
        find_links: list[str] = []
        context.system_selection(
            "find_links",
            "official source does not specify an alternate package index",
        )
    else:
        find_links = _source_items(
            raw_find_links,
            target="find_links",
            context=context,

                required=False,
        ) or []

    dependency_install_timeout_seconds = 240
    context.system_selection(
        "dependency_install_timeout_seconds",
        "bounded installer timeout; does not change research semantics",
    )
    return {
        "python_version": python_version,
        "requirements": requirements,
        "verify_imports": verify_imports,
        "dependency_install_timeout_seconds": dependency_install_timeout_seconds,
        "find_links": find_links,
    }
