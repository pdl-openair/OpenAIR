"""Input and workspace boundary contract for environment setup."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any, Callable
from urllib.parse import urlsplit

from .environment_common import (
    ENVIRONMENT_DIRNAME,
    INSTALL_TIMEOUT_SECONDS,
    MAX_INSTALL_TIMEOUT_SECONDS,
    RUNTIME_WORKSPACE_ENV,
)

PYTHON_VERSION = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")
_IMPORT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_REQUIREMENT = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"
    r"(?:\[[A-Za-z0-9_,.-]+\])?"
    r"(?:\s*(?:===|==|!=|<=|>=|~=|<|>)\s*[A-Za-z0-9*+!._-]+"
    r"(?:\s*,\s*(?:===|==|!=|<=|>=|~=|<|>)\s*[A-Za-z0-9*+!._-]+)*)?$"
)
MAX_PROBE_ARGV_ITEMS = 256
MAX_PROBE_ARGUMENT_LENGTH = 4096
MAX_PROBE_TIMEOUT_SECONDS = 300


def _contains_control_character(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _unique_text_list(
    value: Any, *, validator: Callable[[str], bool], max_items: int
) -> list[str] | None:
    if not isinstance(value, list) or not value or len(value) > max_items:
        return None
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or item != item.strip() or not item:
            return None
        if _contains_control_character(item) or not validator(item) or item in seen:
            return None
        seen.add(item)
        result.append(item)
    return result


def _valid_requirement(value: str) -> bool:
    return len(value) <= 256 and _REQUIREMENT.fullmatch(value) is not None


def _valid_import_name(value: str) -> bool:
    return len(value) <= 256 and _IMPORT_NAME.fullmatch(value) is not None


def _valid_find_link(value: str) -> bool:
    if len(value) > 2048:
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
        and not parsed.query
    )


def relative_path_parts(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, str) or value != value.strip() or not value:
        return None
    if "\x00" in value or _contains_control_character(value):
        return None
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts:
        return None
    if any(part in {"", ".", ".."} or ":" in part for part in path.parts):
        return None
    return tuple(path.parts)


def _validated_runtime_readiness_probe(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    required = {"executable", "argv", "working_directory", "timeout_seconds"}
    if set(value) != required:
        return None

    executable_parts = relative_path_parts(value.get("executable"))
    working_directory_parts = relative_path_parts(value.get("working_directory"))
    if executable_parts is None or working_directory_parts is None:
        return None

    argv = value.get("argv")
    if not isinstance(argv, list) or len(argv) > MAX_PROBE_ARGV_ITEMS:
        return None
    normalized_argv: list[str] = []
    for item in argv:
        if (
            not isinstance(item, str)
            or len(item) > MAX_PROBE_ARGUMENT_LENGTH
            or "\x00" in item
            or _contains_control_character(item)
        ):
            return None
        normalized_argv.append(item)

    timeout_seconds = value.get("timeout_seconds")
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds < 1
        or timeout_seconds > MAX_PROBE_TIMEOUT_SECONDS
    ):
        return None

    return {
        "executable": value["executable"].replace("\\", "/"),
        "executable_parts": executable_parts,
        "argv": normalized_argv,
        "working_directory": value["working_directory"].replace("\\", "/"),
        "working_directory_parts": working_directory_parts,
        "timeout_seconds": timeout_seconds,
    }


def validated_input(payload: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {
        "python_version",
        "requirements",
        "dependency_install_timeout_seconds",
        "find_links",
        "verify_imports",
        "runtime_readiness_probe",
    }
    required = {"python_version", "requirements", "verify_imports"}
    if set(payload) - allowed or not required.issubset(payload):
        return None

    python_version = payload.get("python_version")
    if not isinstance(python_version, str) or PYTHON_VERSION.fullmatch(python_version) is None:
        return None

    requirements = _unique_text_list(
        payload.get("requirements"), validator=_valid_requirement, max_items=128
    )
    verify_imports = _unique_text_list(
        payload.get("verify_imports"), validator=_valid_import_name, max_items=128
    )
    if requirements is None or verify_imports is None:
        return None

    dependency_install_timeout_seconds = payload.get(
        "dependency_install_timeout_seconds", INSTALL_TIMEOUT_SECONDS
    )
    if (
        not isinstance(dependency_install_timeout_seconds, int)
        or isinstance(dependency_install_timeout_seconds, bool)
        or dependency_install_timeout_seconds < 1
        or dependency_install_timeout_seconds > MAX_INSTALL_TIMEOUT_SECONDS
    ):
        return None

    raw_find_links = payload.get("find_links", [])
    if raw_find_links == []:
        find_links: list[str] = []
    else:
        find_links = _unique_text_list(
            raw_find_links, validator=_valid_find_link, max_items=16
        ) or []
        if not find_links:
            return None

    raw_probe = payload.get("runtime_readiness_probe")
    runtime_readiness_probe = None
    if raw_probe is not None:
        runtime_readiness_probe = _validated_runtime_readiness_probe(raw_probe)
        if runtime_readiness_probe is None:
            return None

    return {
        "python_version": python_version,
        "requirements": requirements,
        "dependency_install_timeout_seconds": dependency_install_timeout_seconds,
        "find_links": find_links,
        "verify_imports": verify_imports,
        "runtime_readiness_probe": runtime_readiness_probe,
    }


def runtime_workspace() -> Path | None:
    raw = os.environ.get(RUNTIME_WORKSPACE_ENV, "")
    if not raw:
        return None
    try:
        expected = Path(raw).resolve(strict=True)
        actual = Path.cwd().resolve(strict=True)
    except OSError:
        return None
    if expected != actual or not expected.is_dir():
        return None
    return expected


def environment_target(workspace: Path) -> tuple[Path | None, str | None]:
    try:
        root = workspace.resolve(strict=True)
    except OSError:
        return None, "workspace_boundary_invalid"
    target = (root / ENVIRONMENT_DIRNAME).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError:
        return None, "workspace_boundary_invalid"
    if target.exists() or target.is_symlink():
        return None, "environment_target_conflict"
    return target, None


def requested_python_matches(requested: str, actual: dict[str, Any]) -> bool:
    match = PYTHON_VERSION.fullmatch(requested)
    if match is None:
        return False
    expected = tuple(int(part) for part in match.groups() if part is not None)
    observed = (actual.get("major"), actual.get("minor"), actual.get("micro"))
    return tuple(observed[: len(expected)]) == expected


def current_python_matches(requested: str) -> bool:
    return requested_python_matches(
        requested,
        {
            "major": sys.version_info.major,
            "minor": sys.version_info.minor,
            "micro": sys.version_info.micro,
        },
    )


def environment_python(target: Path) -> Path:
    if os.name == "nt":
        return target / "Scripts" / "python.exe"
    return target / "bin" / "python"


def resolve_probe_working_directory(
    workspace: Path, parts: tuple[str, ...]
) -> Path | None:
    candidate = workspace.joinpath(*parts)
    try:
        root = workspace.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    if not resolved.is_dir():
        return None
    return resolved


def resolve_probe_executable(target: Path, parts: tuple[str, ...]) -> Path | None:
    candidate = target.joinpath(*parts)
    try:
        target_absolute = target.absolute()
        candidate_absolute = candidate.absolute()
        candidate_absolute.relative_to(target_absolute)
    except (OSError, ValueError):
        return None

    current = candidate_absolute.parent
    while current != target_absolute:
        if current.is_symlink():
            return None
        parent = current.parent
        if parent == current:
            return None
        current = parent

    try:
        if not candidate_absolute.is_file():
            return None
    except OSError:
        return None
    return candidate_absolute


def workspace_relative(path: Path, workspace: Path) -> str:
    return path.absolute().relative_to(workspace.absolute()).as_posix()
