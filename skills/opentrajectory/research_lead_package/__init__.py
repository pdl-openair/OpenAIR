"""Generic research-lead package contract helpers."""
from __future__ import annotations

from .builder import build_research_lead_package, build_research_lead_package_from_recorded_run
from .domain_profiles import get_domain_profile, list_domain_profiles, register_domain_profile
from .literature_evidence_contract_v1 import (
    adapt_legacy_literature_output,
    assemble_comparators_from_structured_formulation_facts,
    collect_structured_formulation_facts,
    consume_literature_evidence_contract,
    consume_literature_skill_output,
    normalize_structured_formulation_fact,
)
from .schema import PACKAGE_VERSION, validate_research_lead_package

__all__ = [
    "PACKAGE_VERSION",
    "adapt_legacy_literature_output",
    "assemble_comparators_from_structured_formulation_facts",
    "build_research_lead_package",
    "build_research_lead_package_from_recorded_run",
    "collect_structured_formulation_facts",
    "consume_literature_evidence_contract",
    "consume_literature_skill_output",
    "normalize_structured_formulation_fact",
    "get_domain_profile",
    "list_domain_profiles",
    "register_domain_profile",
    "validate_research_lead_package",
]
