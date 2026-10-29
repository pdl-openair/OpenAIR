from __future__ import annotations

from pathlib import Path

from skills.opentrajectory.research_lead_package.schema import (
    GENERIC_PACKAGE_KEYS,
    PACKAGE_VERSION,
    PROPOSAL_KEYS,
    empty_package,
    validate_research_lead_package,
)

BUILDER = Path(__file__).resolve().parents[2] / "builder.py"



def test_generic_contract_schema_has_required_keys() -> None:
    package = empty_package()

    assert package["package_version"] == PACKAGE_VERSION
    assert set(GENERIC_PACKAGE_KEYS).issubset(package)
    assert set(PROPOSAL_KEYS).issubset(package["proposal"])
    assert validate_research_lead_package(package) == []



def test_generic_builder_has_no_direct_cs_profile_import() -> None:
    builder_source = BUILDER.read_text(encoding="utf-8")

    assert "CSDomainProfile" not in builder_source
    assert "domain_profiles.cs import" not in builder_source
