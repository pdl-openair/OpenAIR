from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.modules.pvd_scientific_state_materialization import (
    materialize_experimental_system_context,
)
from app.modules.research_scientific_state_materialization import (
    materialize_declared_scientific_facts,
)
from app.modules.research_design_activation import scientific_states_for_confirmed_design
from app.modules.research_scientific_state_authority import (
    scientific_state_at_version_for_design,
)


NOW = datetime(2026, 9, 14, 13, 0, tzinfo=timezone.utc)


def _state(*, version: int, design_version: int, facts: dict) -> dict:
    return {
        "scientificStateVersion": version,
        "researchDesignVersion": design_version,
        "scientificFacts": facts,
        "executionState": {},
        "sourceRefs": [f"artifact-state-{version}"],
        "createdAt": NOW.isoformat(),
    }


def test_superseded_design_projection_cannot_mutate_current_scientific_state() -> None:
    payload = {
        "taskId": "task-1",
        "status": "running",
        "activeResearchDesignRef": {"researchDesignId": "design-1", "version": 2},
        "scientificStates": [
            _state(version=1, design_version=1, facts={"old_design_fact": "v1"}),
            _state(version=2, design_version=2, facts={"current_fact": "v2"}),
        ],
        "finalResultAuthority": None,
    }

    with pytest.raises(ValueError, match="scientific_state_projection_design_not_current"):
        materialize_declared_scientific_facts(
            task_payload=payload,
            projected_facts={"late_fact": "must-not-become-current"},
            source_artifact_id="artifact-late-v1",
            research_design_version=1,
            occurred_at=NOW,
        )

    assert payload["scientificStates"][-1]["scientificFacts"] == {"current_fact": "v2"}


def test_current_design_update_ignores_newer_stale_snapshot_from_other_design() -> None:
    payload = {
        "taskId": "task-1",
        "status": "running",
        "activeResearchDesignRef": {"researchDesignId": "design-1", "version": 2},
        "scientificStates": [
            _state(version=1, design_version=2, facts={"current_fact": "v2"}),
            _state(version=2, design_version=1, facts={"stale_fact": "v1"}),
        ],
        "finalResultAuthority": None,
    }

    result = materialize_declared_scientific_facts(
        task_payload=payload,
        projected_facts={"new_fact": "accepted-v2"},
        source_artifact_id="artifact-current-v2",
        research_design_version=2,
        occurred_at=NOW,
    )

    current = result["scientificStates"][-1]
    assert current["scientificStateVersion"] == 3
    assert current["researchDesignVersion"] == 2
    assert current["scientificFacts"] == {
        "current_fact": "v2",
        "new_fact": "accepted-v2",
    }
    assert "stale_fact" not in current["scientificFacts"]


def test_without_active_design_only_latest_design_scope_may_be_updated() -> None:
    payload = {
        "taskId": "legacy-minimal",
        "status": "running",
        "scientificStates": [
            _state(version=1, design_version=1, facts={"old": True}),
            _state(version=2, design_version=2, facts={"current": True}),
        ],
        "finalResultAuthority": None,
    }

    with pytest.raises(ValueError, match="scientific_state_projection_design_not_current"):
        materialize_declared_scientific_facts(
            task_payload=payload,
            projected_facts={"late": True},
            source_artifact_id="artifact-late",
            research_design_version=1,
            occurred_at=NOW,
        )


def test_generic_then_pvd_facts_share_one_scientific_state_authority() -> None:
    payload = {
        "taskId": "task-1",
        "status": "running",
        "activeResearchDesignRef": {"researchDesignId": "design-1", "version": 1},
        "scientificStates": [
            _state(version=1, design_version=1, facts={}),
        ],
        "finalResultAuthority": None,
    }
    generic = materialize_declared_scientific_facts(
        task_payload=payload,
        projected_facts={
            "evidence_supported_conclusions": [{"statement": "accepted"}],
            "evidence_gaps": [{"gap": "remaining"}],
        },
        source_artifact_id="artifact-generic",
        research_design_version=1,
        occurred_at=NOW,
    )

    combined = materialize_experimental_system_context(
        task_payload=generic,
        projected_facts={
            "experimental_system_context": {"material_system": "AlScN/Mo"}
        },
        source_artifact_id="artifact-pvd",
        research_design_version=1,
        occurred_at=NOW,
    )

    facts = combined["scientificStates"][-1]["scientificFacts"]
    assert facts["evidence_supported_conclusions"] == [{"statement": "accepted"}]
    assert facts["evidence_gaps"] == [{"gap": "remaining"}]
    assert facts["experimental_system_contexts"][0][
        "experimental_system_context"
    ] == {"material_system": "AlScN/Mo"}
    assert combined["scientificStates"][-1]["researchDesignVersion"] == 1



def test_pvd_then_generic_facts_remain_composed_in_one_scientific_state() -> None:
    payload = {
        "taskId": "task-1",
        "status": "running",
        "activeResearchDesignRef": {"researchDesignId": "design-1", "version": 1},
        "scientificStates": [
            _state(version=1, design_version=1, facts={}),
        ],
        "finalResultAuthority": None,
    }
    pvd = materialize_experimental_system_context(
        task_payload=payload,
        projected_facts={
            "experimental_system_context": {"material_system": "AlScN/Mo"}
        },
        source_artifact_id="artifact-pvd",
        research_design_version=1,
        occurred_at=NOW,
    )

    combined = materialize_declared_scientific_facts(
        task_payload=pvd,
        projected_facts={"evidence_gaps": [{"gap": "remaining"}]},
        source_artifact_id="artifact-generic",
        research_design_version=1,
        occurred_at=NOW,
    )

    facts = combined["scientificStates"][-1]["scientificFacts"]
    assert facts["evidence_gaps"] == [{"gap": "remaining"}]
    assert facts["current_experimental_system_context_ref"].startswith(
        "pvd-experimental-system-context:"
    )
    assert facts["experimental_system_contexts"][0][
        "experimental_system_context"
    ] == {"material_system": "AlScN/Mo"}

def test_new_design_inherits_only_the_current_active_design_scientific_state() -> None:
    current_v1 = SimpleNamespace(
        scientific_state_version=1,
        research_design_version=2,
        scientific_facts={"accepted_current": "v2"},
        execution_state={"stage": "done"},
        source_refs=["artifact-current-v1"],
        created_at=NOW,
    )
    stale_v0 = SimpleNamespace(
        scientific_state_version=2,
        research_design_version=1,
        scientific_facts={"stale_other_design": "must-not-carry"},
        execution_state={},
        source_refs=["artifact-stale-v0"],
        created_at=NOW,
    )
    task = SimpleNamespace(
        active_research_design_ref=SimpleNamespace(version=2),
        scientific_states=[current_v1, stale_v0],
    )
    next_design = SimpleNamespace(version=3)

    states = scientific_states_for_confirmed_design(
        task=task,
        design=next_design,
        activated_at=NOW,
    )

    baseline = states[-1]
    assert baseline.scientific_state_version == 3
    assert baseline.research_design_version == 3
    assert baseline.scientific_facts == {"accepted_current": "v2"}
    assert baseline.execution_state == {}
    assert baseline.source_refs == ["artifact-current-v1"]


def test_frozen_scientific_state_lookup_does_not_drift_to_newer_state() -> None:
    states = [
        _state(version=1, design_version=2, facts={"frozen": True}),
        _state(version=2, design_version=2, facts={"newer": True}),
    ]

    frozen = scientific_state_at_version_for_design(
        states,
        research_design_version=2,
        scientific_state_version_value=1,
    )

    assert frozen is not None
    assert frozen["scientificStateVersion"] == 1
    assert frozen["scientificFacts"] == {"frozen": True}
