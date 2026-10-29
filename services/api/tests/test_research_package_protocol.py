from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from app.modules.research_package_protocol import (
    ResearchPackageValidationError,
    load_and_validate_research_package,
    validate_research_package,
)


FIXTURE = Path(__file__).parent / "fixtures" / "research_packages" / "ag4ge3s5_engineering_v02.yaml"


def _package() -> dict:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


def test_yaml_and_json_have_same_canonical_identity() -> None:
    package = _package()
    from_yaml = load_and_validate_research_package(FIXTURE.read_bytes(), format_hint="yaml")
    from_json = load_and_validate_research_package(
        json.dumps(package, ensure_ascii=False, indent=2).encode("utf-8"),
        format_hint="json",
    )

    assert from_yaml.document == from_json.document
    assert from_yaml.canonical_bytes == from_json.canonical_bytes
    assert from_yaml.content_sha256 == from_json.content_sha256


def test_optional_resources_can_be_omitted_without_default_injection() -> None:
    package = _package()
    package["reference_cases"][0]["resource_refs"] = []
    package.pop("resources")

    validated = validate_research_package(package)

    assert "resources" not in validated.document


def test_unsupported_spec_version_is_rejected() -> None:
    package = _package()
    package["spec_version"] = "3"

    with pytest.raises(ResearchPackageValidationError) as exc:
        validate_research_package(package)

    assert exc.value.code == "research_package_spec_version_unsupported"
    assert exc.value.path == "$.spec_version"


def test_missing_source_reference_is_rejected() -> None:
    package = _package()
    package["method"]["source_refs"].append("missing-source")

    with pytest.raises(ResearchPackageValidationError) as exc:
        validate_research_package(package)

    assert exc.value.code == "research_package_reference_not_found"
    assert exc.value.path.endswith("[5]")


def test_missing_resource_reference_is_rejected() -> None:
    package = _package()
    package["reference_cases"][0]["resource_refs"].append("missing-resource")

    with pytest.raises(ResearchPackageValidationError) as exc:
        validate_research_package(package)

    assert exc.value.code == "research_package_reference_not_found"


def test_duplicate_package_local_identity_is_rejected() -> None:
    package = _package()
    package["sources"].append(copy.deepcopy(package["sources"][0]))

    with pytest.raises(ResearchPackageValidationError) as exc:
        validate_research_package(package)

    assert exc.value.code == "research_package_duplicate_id"


def test_yaml_python_constructor_is_not_executed() -> None:
    malicious = b'!!python/object/apply:os.system ["echo should-not-run"]'

    with pytest.raises(ResearchPackageValidationError) as exc:
        load_and_validate_research_package(malicious, format_hint="yaml")

    assert exc.value.code == "research_package_parse_failed"


def test_extension_fields_are_preserved_without_reinterpreting_standard_fields() -> None:
    package = _package()
    package["x_openair_note"] = {"status": "engineering-only", "count": 2}

    validated = validate_research_package(package)

    assert validated.document["x_openair_note"] == package["x_openair_note"]


def test_yaml_only_non_json_scalar_is_rejected_deterministically() -> None:
    raw = FIXTURE.read_text(encoding="utf-8") + "\nx_extension_date: 2026-09-17\n"

    with pytest.raises(ResearchPackageValidationError) as exc:
        load_and_validate_research_package(raw, format_hint="yaml")

    assert exc.value.code == "research_package_value_not_json_compatible"
    assert exc.value.path == "$.x_extension_date"
