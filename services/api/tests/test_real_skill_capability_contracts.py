from __future__ import annotations

from app.execution.durable_skill_runtime import registered_skill_capability_contract
from app.modules.research_orchestration_resolver import build_registry_snapshot


EXPECTED = {
    "opentrajectory_source_acquisition": (
        [
            "opentrajectory.source_acquisition",
            "opentrajectory.source_acquisition.planned_output",
        ],
        False,
    ),
    "opentrajectory_source_screening_selection": (
        [
            "opentrajectory.source_screening_selection",
            "opentrajectory.source_screening_selection.planned_output",
        ],
        True,
    ),
    "deepxiv_progressive_reading": (
        [
            "opentrajectory.progressive_reading",
            "opentrajectory.progressive_reading.planned_output",
        ],
        False,
    ),
    "evidence_node_extraction": (
        [
            "opentrajectory.evidence_extraction.planned_output",
            "research_records",
        ],
        True,
    ),
}


def test_real_skill_capability_contracts_reach_registry_snapshot() -> None:
    snapshot = build_registry_snapshot()
    skills = {
        row["skillId"]: row
        for row in snapshot.envelope["definitions"]["skills"]
    }

    for skill_id, (artifact_types, professional) in EXPECTED.items():
        row = skills[skill_id]
        frozen_input = row["sourceContract"]["frozenSchemas"]["input"]["schema"]

        assert row["inputContract"] == {
            "status": "known",
            "acceptedInputRefs": sorted(frozen_input["properties"]),
        }
        assert row["outputContract"] == {
            "status": "known",
            "artifactTypes": artifact_types,
        }
        assert row["judgmentSupport"] == {
            "deterministic": False,
            "domainProfessional": professional,
            "humanAuthority": False,
            "source": "skill_capability_contract",
        }


def test_missing_capability_declaration_remains_fail_closed() -> None:
    input_contract, output_contract, support = (
        registered_skill_capability_contract({})
    )
    assert input_contract == {
        "status": "unknown",
        "acceptedInputRefs": [],
    }
    assert output_contract == {
        "status": "unknown",
        "artifactTypes": [],
    }
    assert support == {
        "deterministic": False,
        "source": "canonical_registry_unspecified",
    }
