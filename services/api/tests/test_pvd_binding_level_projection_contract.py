"""Contract + regression tests for binding-level scientific_state_projection.

These guard the structural fix for the PVD S2 materializer death-loop
(``pvd_research_request_history_conflict``). The projection declaration for
``pvd_research_request_understanding`` moved from the skill level
(``skill.yaml`` sourceContract) to the binding level
(``skill-bindings.yaml`` row field ``scientificStateProjection``), carried
ONLY by the S1 (``research_goal_equipment_constraints``) binding. S2/S5/S6
reuse the same skill for context-rebuild and must NOT re-materialize
experiment history into ScientificState (that would deterministically raise
``pvd_research_request_history_conflict`` against S1's already-written rows
and, under ``single_attempt_no_replay``, loop forever).

The materializer's real conflict-detection invariant (f575ebef, the ``!=``
raise) is NOT touched and remains guarded by
``test_pvd_scientific_state_materialization.py``.
"""
from __future__ import annotations

from typing import Any

import pytest
import yaml

from app.execution import research_dispatch_skill_provider as skill_provider_module
from app.modules.skill_binding_source import load_skill_binding_source_rows

# Reuse the shared handoff fixture + helpers from the dispatch-provider suite
# rather than duplicating a frozen-dispatch harness here.
from tests.test_research_dispatch_skill_provider import (
    _direct_skill_handoff_fixture,
    _payload,
    _request,
    _runtime,
)

ROOT = __import__("pathlib").Path(__file__).resolve().parents[3]

S1_BINDING = (
    "bind:pvd:research_goal_equipment_constraints:skill:pvd_research_request_understanding"
)
# S2/S5/S6 reuse pvd_research_request_understanding for context-rebuild only.
NON_S1_BINDINGS = (
    "bind:pvd:research_retrieval_experiment_design:skill:pvd_research_request_understanding",
    "bind:pvd:experimental_feedback_round_analysis:skill:pvd_research_request_understanding",
    "bind:pvd:process_factor_candidate_experiment_analysis:skill:pvd_research_request_understanding",
)
SKILL_ID = "pvd_research_request_understanding"


def _binding_rows() -> dict[str, dict[str, Any]]:
    return {row["bindingId"]: row for row in load_skill_binding_source_rows()}


def _skill_yaml() -> dict[str, Any]:
    return yaml.safe_load(
        (ROOT / "skills/pvd/pvd-research-request-understanding/skill.yaml").read_text(
            encoding="utf-8"
        )
    )


# ---------------------------------------------------------------------------
# Contract: projection lives on the S1 binding only, never on the skill.
# ---------------------------------------------------------------------------


def test_s1_binding_carries_scientific_state_projection():
    binding = _binding_rows()[S1_BINDING]
    projection = binding.get("scientificStateProjection")
    assert isinstance(projection, dict)
    assert projection["contract_version"] == 2
    assert projection["materializer_ref"] == "pvd:research-request-understanding:v1"
    facts = projection["facts"]
    assert facts == [
        {
            "fact_key": "pvd_research_request_context",
            "source_field": "structured_context",
        }
    ]


def test_non_s1_bindings_do_not_carry_scientific_state_projection():
    rows = _binding_rows()
    for binding_id in NON_S1_BINDINGS:
        assert binding_id in rows, f"missing binding row {binding_id}"
        assert "scientificStateProjection" not in rows[binding_id], (
            f"{binding_id} must NOT carry scientificStateProjection: only the S1 "
            "entry binding writes experiment history into ScientificState."
        )


def test_skill_yaml_no_longer_declares_skill_level_projection():
    skill = _skill_yaml()
    assert "scientific_state_projection" not in skill, (
        "pvd_research_request_understanding skill.yaml must not declare "
        "scientific_state_projection at the skill level; it moved to the binding."
    )
    # The finalization block stays, so frozenSchemas (output schema) are still
    # built and available for the binding-level projection cross-entity check.
    assert skill.get("finalization", {}).get("entrypoint") == "scripts.finalize:finalize"


def test_registry_snapshot_builds_with_binding_level_projection():
    """The full snapshot envelope must admit the new field and validate it."""
    from app.modules.research_orchestration_resolver import build_registry_snapshot

    snapshot = build_registry_snapshot()
    envelope = snapshot.envelope if hasattr(snapshot, "envelope") else snapshot
    bindings = {
        row["bindingId"]: row
        for row in envelope["bindings"]["skills"]
        if row.get("skillId") == SKILL_ID
    }
    assert isinstance(bindings[S1_BINDING].get("scientificStateProjection"), dict)
    for binding_id in NON_S1_BINDINGS:
        assert "scientificStateProjection" not in bindings[binding_id]


# ---------------------------------------------------------------------------
# Regression: an S2/S5/S6 frozen dispatch carries no projection, so the
# materializer is never invoked on its completed result.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("binding_id", NON_S1_BINDINGS)
def test_non_s1_frozen_dispatch_has_no_scientific_state_projection(
    monkeypatch: pytest.MonkeyPatch,
    binding_id: str,
) -> None:
    """The frozen orchestrationPlan for S2/S5/S6 must omit scientificStateProjection.

    This is the read-path half of the death-loop fix: ``frozen_skill_request_for_dispatch``
    reads the projection from the binding (with a skill-level fallback for old
    snapshots). With the projection gone from both the binding and skill.yaml,
    the plan carries no projection, so ``record_dispatch_completed_result`` is
    called with ``scientific_state_projection_declaration=None`` and the
    materializer is never reached.
    """
    monkeypatch.setattr(
        skill_provider_module,
        "resolve_registered_durable_skill_runtime",
        lambda *_args, **_kwargs: _runtime(),
    )
    repository, _definition, binding = _direct_skill_handoff_fixture(
        binding_id=binding_id,
        skill_id=SKILL_ID,
        resolved_parameters={"query": "Canonical user research question."},
        scientific_state_version=7,
    )
    assert "scientificStateProjection" not in binding

    frozen = skill_provider_module.frozen_skill_request_for_dispatch(
        _request(_payload()),
        skill_id=SKILL_ID,
        definition_version=1,
        snapshot_definition=_definition,
        source_binding_refs=((binding["bindingId"], binding["bindingVersion"]),),
        repository=repository,  # type: ignore[arg-type]
    )
    assert "scientificStateProjection" not in frozen.orchestrationPlan


def test_materialize_research_request_understanding_not_invoked_without_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the frozen plan carries no projection, the materializer is not called.

    Drives the downstream projection path (``project_validated_structured_result``
    → ``materialize_scientific_state_projection``) with the declaration that an
    S2 dispatch would produce (``None``) and asserts the PVD request-understanding
    materializer — whose ``!=`` raise is the death-loop crash — is never reached.
    """
    from datetime import datetime, timezone

    from app.modules import pvd_scientific_state_materialization as pvd_materialization
    from app.modules.scientific_state_fact_projection import (
        materialize_scientific_state_projection,
        project_validated_structured_result,
    )

    calls: list[Any] = []

    def _spy(*_args, **_kwargs):
        calls.append(_kwargs)
        return {"__spy__": True}

    monkeypatch.setattr(
        pvd_materialization,
        "materialize_research_request_understanding",
        _spy,
    )

    content = '{"structured_context":{"rounds":[]}}'
    # No projection declaration on the frozen plan → no projected facts.
    projected = project_validated_structured_result(content, declaration=None)
    assert projected == {}

    occurred_at = datetime(2026, 10, 12, tzinfo=timezone.utc)
    result = materialize_scientific_state_projection(
        task_payload={"workUnits": [], "scientificStates": []},
        declaration=None,
        projected_facts=projected,
        source_artifact_id="artifact-s2",
        research_design_version=1,
        occurred_at=occurred_at,
    )
    # Empty projected facts short-circuit: payload returned unchanged, no
    # materializer branch entered.
    assert calls == []
    assert "scientificStates" in result
