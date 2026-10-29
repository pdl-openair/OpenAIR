"""Existing technical and frozen Authority protections remain effective."""

import pytest
from app.execution.research_start_provider_adapter import ProviderContractError
from app.modules.research_design_service import ResearchDesignServiceError

from .support import build, bundle_for, decode, real_payload


def test_direct_proposal_cannot_replace_frozen_owner(source):
    bundle = bundle_for(source)
    proposal = decode(real_payload(), bundle)
    spec = bundle.planning_context["targetOutputSpecs"][0]
    index = proposal.understanding.target_outputs.index(spec["description"])
    proposal.target_output_owners[f"understanding.targetOutputs[{index}]"] = (
        "next_research_recommendation"
    )
    with pytest.raises(ValueError, match="frozen Workflow"):
        build(proposal, bundle, source)


def test_unregistered_substage_cannot_enter_execution(source):
    bundle = bundle_for(source)
    proposal = decode(real_payload(), bundle)
    proposal.instances[0].definition_id = "pv2:unregistered"
    with pytest.raises(ResearchDesignServiceError):
        build(proposal, bundle, source)


def test_no_valid_registered_work_cannot_make_a_formal_design(source):
    bundle = bundle_for(source)
    with pytest.raises(ProviderContractError) as failure:
        decode(
            {
                "goal": "当前科学问题。",
                "targetOutputs": ["当前成果。"],
                "workItems": [
                    {"workTypeChoice": "unknown", "scientificObjective": "无法登记的工作。"}
                ],
            },
            bundle,
            freeform=True,
        )

    assert failure.value.safe_error_code == "planner_no_registered_work"
