from __future__ import annotations

import json
from pathlib import Path

from skills.opentrajectory.research_lead_package import (
    build_research_lead_package_from_recorded_run,
    validate_research_lead_package,
)
from skills.opentrajectory.research_lead_package.domain_profiles.cs import (
    CSDomainProfile,
    build_cs_research_lead_package_from_recorded_run,
)

ROOT = Path(__file__).resolve().parents[5]
RUN_DIR = ROOT / "fixtures" / "recorded_runs" / "cs_research_ope_006"
EXPECTED = Path(__file__).resolve().parent / "fixtures" / "cs_research_ope_006_expected_contract.json"


def _package() -> dict:
    return build_cs_research_lead_package_from_recorded_run(RUN_DIR)


def test_generic_recorded_run_builder_requires_explicit_domain_profile() -> None:
    try:
        build_research_lead_package_from_recorded_run(RUN_DIR)
    except ValueError as exc:
        assert str(exc) == "domain_profile is required"
    else:
        raise AssertionError("generic builder must not default recorded runs to cs_v0.3")


def test_generic_recorded_run_builder_accepts_explicit_cs_profile_instance() -> None:
    package = build_research_lead_package_from_recorded_run(RUN_DIR, domain_profile=CSDomainProfile())

    assert package["domain_profile"] == "cs_v0.3"


def test_generic_recorded_run_builder_accepts_explicit_cs_profile_id() -> None:
    package = build_research_lead_package_from_recorded_run(RUN_DIR, domain_profile_id="cs_v0.3")

    assert package["domain_profile"] == "cs_v0.3"


def test_cs_recorded_run_maps_to_expected_contract_summary() -> None:
    package = _package()
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))

    assert validate_research_lead_package(package) == []
    assert package["package_version"] == expected["package_version"]
    assert package["evaluation_mode"] == expected["evaluation_mode"]
    assert package["domain_profile"] == expected["domain_profile"]
    assert package["proposal"]["claimed_result_status"] == expected["claimed_result_status"]
    assert len(package["proposal"]["evidence_refs"]) == expected["evidence_refs_count"]
    assert len(package["proposal"]["artifact_refs"]) == expected["artifact_refs_count"]
    assert len(package["proposal"]["gap_records"]) >= expected["min_gap_records_count"]
    assert package["source_mapping"]["mapping_confidence"] == expected["mapping_confidence"]


def test_artifact_refs_do_not_become_evidence_refs() -> None:
    package = _package()

    assert package["proposal"]["artifact_refs"]
    assert package["proposal"]["evidence_refs"] == []
    assert package["proposal"]["artifact_refs"] != package["proposal"]["evidence_refs"]


def test_empty_evidence_and_missing_results_create_gap_records() -> None:
    package = _package()
    gap_types = {gap["type"] for gap in package["proposal"]["gap_records"]}

    assert "missing_evidence_refs" in gap_types
    assert "missing_measured_results" in gap_types
    assert "missing_reproducibility_protocol" in gap_types
    assert "offline_proposal_traceability_limited" in gap_types


def test_offline_proposal_cannot_become_reproduced_result() -> None:
    package = _package()

    assert package["evaluation_mode"] == "offline_proposal"
    assert package["proposal"]["claimed_result_status"] == "planned"
    assert not package["proposal"].get("observed_results")
    assert not package["proposal"].get("measured_results")
