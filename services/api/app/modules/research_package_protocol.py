"""Research Package v0.2 deterministic protocol handling.

This module owns only machine-readable structure, identity references and
serialization. Scientific meaning remains in the package content; no field here
is promoted into ResearchDesign, ScientificState, Evidence or execution state.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping

import yaml


SUPPORTED_SPEC_VERSION = "2"


class ResearchPackageValidationError(ValueError):
    """Deterministic Research Package protocol validation failure."""

    def __init__(self, code: str, path: str = "$") -> None:
        self.code = code
        self.path = path
        super().__init__(f"{code}:{path}")


@dataclass(frozen=True)
class ValidatedResearchPackage:
    document: dict[str, Any]
    canonical_bytes: bytes
    content_sha256: str

    @property
    def package_id(self) -> str:
        return str(self.document["id"])

    @property
    def version(self) -> str:
        return str(self.document["version"])

    @property
    def spec_version(self) -> str:
        return str(self.document["spec_version"])


def parse_research_package(raw: bytes | str, *, format_hint: str | None = None) -> dict[str, Any]:
    """Parse YAML or JSON without executing constructors or referenced actions."""
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ResearchPackageValidationError("research_package_not_utf8") from exc
    elif isinstance(raw, str):
        text = raw
    else:
        raise TypeError("raw must be bytes or str")

    normalized_hint = str(format_hint or "").strip().lower().lstrip(".")
    try:
        if normalized_hint == "json":
            parsed = json.loads(text)
        elif normalized_hint in {"yaml", "yml"}:
            parsed = yaml.safe_load(text)
        else:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ResearchPackageValidationError("research_package_parse_failed") from exc

    if not isinstance(parsed, dict):
        raise ResearchPackageValidationError("research_package_root_must_be_object")
    _validate_string_keys(parsed, path="$")
    return dict(parsed)


def validate_research_package(document: Mapping[str, Any]) -> ValidatedResearchPackage:
    """Validate v0.2 structure and deterministic identity relationships.

    Unknown/extension fields are preserved. The validator never invents defaults,
    so an omitted optional field stays omitted rather than becoming an asserted
    scientific value.
    """
    if not isinstance(document, Mapping):
        raise ResearchPackageValidationError("research_package_root_must_be_object")

    candidate = dict(document)
    _validate_json_value(candidate, path="$")

    required = (
        "spec_version",
        "id",
        "name",
        "version",
        "summary",
        "sources",
        "method",
        "reference_cases",
        "applicability",
        "capabilities",
    )
    for field in required:
        if field not in candidate:
            raise ResearchPackageValidationError("research_package_required_field_missing", f"$.{field}")

    spec_version = _nonempty_text(candidate["spec_version"], "$.spec_version")
    if spec_version != SUPPORTED_SPEC_VERSION:
        raise ResearchPackageValidationError("research_package_spec_version_unsupported", "$.spec_version")

    _nonempty_text(candidate["id"], "$.id")
    _nonempty_text(candidate["name"], "$.name")
    _nonempty_text(candidate["version"], "$.version")
    _nonempty_text(candidate["summary"], "$.summary")

    source_ids = _validate_sources(candidate["sources"])
    resource_ids = _validate_resources(candidate.get("resources", []))
    _validate_method(candidate["method"], source_ids)
    _validate_reference_cases(candidate["reference_cases"], source_ids, resource_ids)
    _validate_applicability(candidate["applicability"], source_ids)
    _string_list(candidate["capabilities"], "$.capabilities")

    canonical = json.dumps(
        candidate,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return ValidatedResearchPackage(
        document=candidate,
        canonical_bytes=canonical,
        content_sha256=hashlib.sha256(canonical).hexdigest(),
    )


def load_and_validate_research_package(
    raw: bytes | str, *, format_hint: str | None = None
) -> ValidatedResearchPackage:
    return validate_research_package(parse_research_package(raw, format_hint=format_hint))


def _validate_string_keys(value: Any, *, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ResearchPackageValidationError("research_package_object_key_must_be_string", path)
            _validate_string_keys(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_string_keys(child, path=f"{path}[{index}]")


def _validate_json_value(value: Any, *, path: str) -> None:
    """Keep YAML input semantically equivalent to a JSON package document."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ResearchPackageValidationError("research_package_number_must_be_finite", path)
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ResearchPackageValidationError("research_package_object_key_must_be_string", path)
            _validate_json_value(child, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_json_value(child, path=f"{path}[{index}]")
        return
    raise ResearchPackageValidationError("research_package_value_not_json_compatible", path)


def _nonempty_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchPackageValidationError("research_package_text_required", path)
    return value


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ResearchPackageValidationError("research_package_object_required", path)
    return value


def _list(value: Any, path: str, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list):
        raise ResearchPackageValidationError("research_package_list_required", path)
    if nonempty and not value:
        raise ResearchPackageValidationError("research_package_list_must_not_be_empty", path)
    return value


def _string_list(value: Any, path: str, *, nonempty: bool = False) -> list[str]:
    rows = _list(value, path, nonempty=nonempty)
    for index, item in enumerate(rows):
        _nonempty_text(item, f"{path}[{index}]")
    return rows  # type: ignore[return-value]


def _required_fields(value: dict[str, Any], path: str, fields: tuple[str, ...]) -> None:
    for field in fields:
        if field not in value:
            raise ResearchPackageValidationError("research_package_required_field_missing", f"{path}.{field}")


def _unique_ids(rows: list[Any], path: str) -> tuple[set[str], list[dict[str, Any]]]:
    identifiers: set[str] = set()
    typed_rows: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        row_path = f"{path}[{index}]"
        row = _mapping(raw, row_path)
        identifier = _nonempty_text(row.get("id"), f"{row_path}.id")
        if identifier in identifiers:
            raise ResearchPackageValidationError("research_package_duplicate_id", f"{row_path}.id")
        identifiers.add(identifier)
        typed_rows.append(row)
    return identifiers, typed_rows


def _validate_sources(value: Any) -> set[str]:
    rows = _list(value, "$.sources", nonempty=True)
    identifiers, typed_rows = _unique_ids(rows, "$.sources")
    for index, source in enumerate(typed_rows):
        path = f"$.sources[{index}]"
        _required_fields(source, path, ("id", "name", "ref"))
        _nonempty_text(source["name"], f"{path}.name")
        _nonempty_text(source["ref"], f"{path}.ref")
    return identifiers


def _validate_resources(value: Any) -> set[str]:
    rows = _list(value, "$.resources")
    identifiers, typed_rows = _unique_ids(rows, "$.resources")
    for index, resource in enumerate(typed_rows):
        path = f"$.resources[{index}]"
        _required_fields(resource, path, ("id", "name", "role", "ref"))
        _nonempty_text(resource["name"], f"{path}.name")
        _nonempty_text(resource["role"], f"{path}.role")
        _nonempty_text(resource["ref"], f"{path}.ref")
        for optional in ("revision", "note"):
            if optional in resource and resource[optional] is not None:
                _nonempty_text(resource[optional], f"{path}.{optional}")
    return identifiers


def _validate_refs(value: Any, path: str, valid_ids: set[str], *, nonempty: bool = False) -> None:
    refs = _string_list(value, path, nonempty=nonempty)
    for index, ref in enumerate(refs):
        if ref not in valid_ids:
            raise ResearchPackageValidationError("research_package_reference_not_found", f"{path}[{index}]")


def _validate_method(value: Any, source_ids: set[str]) -> None:
    method = _mapping(value, "$.method")
    _required_fields(method, "$.method", ("goal", "inputs", "logic", "outputs", "source_refs"))
    _nonempty_text(method["goal"], "$.method.goal")
    _string_list(method["inputs"], "$.method.inputs")
    _string_list(method["logic"], "$.method.logic", nonempty=True)
    _string_list(method["outputs"], "$.method.outputs")
    _validate_refs(method["source_refs"], "$.method.source_refs", source_ids, nonempty=True)
    if "validation_criteria" in method:
        _string_list(method["validation_criteria"], "$.method.validation_criteria")


def _validate_reference_cases(value: Any, source_ids: set[str], resource_ids: set[str]) -> None:
    rows = _list(value, "$.reference_cases", nonempty=True)
    _identifiers, typed_rows = _unique_ids(rows, "$.reference_cases")
    for index, case in enumerate(typed_rows):
        path = f"$.reference_cases[{index}]"
        _required_fields(
            case,
            path,
            ("id", "source_refs", "target", "conditions", "results", "resource_refs"),
        )
        _validate_refs(case["source_refs"], f"{path}.source_refs", source_ids, nonempty=True)
        _nonempty_text(case["target"], f"{path}.target")
        _string_list(case["conditions"], f"{path}.conditions")
        results = _list(case["results"], f"{path}.results", nonempty=True)
        for result_index, raw_result in enumerate(results):
            result_path = f"{path}.results[{result_index}]"
            result = _mapping(raw_result, result_path)
            _required_fields(result, result_path, ("description",))
            _nonempty_text(result["description"], f"{result_path}.description")
            if "ref" in result and result["ref"] is not None:
                _nonempty_text(result["ref"], f"{result_path}.ref")
        _validate_refs(case["resource_refs"], f"{path}.resource_refs", resource_ids)


def _validate_applicability(value: Any, source_ids: set[str]) -> None:
    applicability = _mapping(value, "$.applicability")
    _required_fields(
        applicability,
        "$.applicability",
        ("use_when", "preserve", "adaptable", "limits", "source_refs"),
    )
    _string_list(applicability["use_when"], "$.applicability.use_when")
    _string_list(applicability["preserve"], "$.applicability.preserve")
    _string_list(applicability["adaptable"], "$.applicability.adaptable")
    _string_list(applicability["limits"], "$.applicability.limits")
    _validate_refs(
        applicability["source_refs"],
        "$.applicability.source_refs",
        source_ids,
        nonempty=True,
    )
