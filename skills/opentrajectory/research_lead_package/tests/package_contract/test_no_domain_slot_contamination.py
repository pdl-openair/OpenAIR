from __future__ import annotations

from pathlib import Path

from skills.opentrajectory.research_lead_package.domain_profiles.cs import (
    build_cs_research_lead_package_from_recorded_run,
)
from skills.opentrajectory.research_lead_package.schema import (
    GENERIC_PACKAGE_KEYS,
    PROPOSAL_KEYS,
)

ROOT = Path(__file__).resolve().parents[5]
RUN_DIR = ROOT / "fixtures" / "recorded_runs" / "cs_research_ope_006"


def test_domain_specific_fields_are_structurally_namespaced() -> None:
    package = build_cs_research_lead_package_from_recorded_run(RUN_DIR)
    normalized = package["domain_normalized"]
    domain_fields = normalized["fields"]

    assert normalized["profile_id"] == "cs_v0.3"
    assert domain_fields
    assert set(domain_fields).isdisjoint(GENERIC_PACKAGE_KEYS)
    assert set(domain_fields).isdisjoint(PROPOSAL_KEYS)
    assert set(domain_fields).isdisjoint(package["proposal"])
