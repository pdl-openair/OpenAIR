from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.models.research_orchestration import ResearchTaskV12
from app.models.schemas import AgentResearchDesignRevisionIn
from app.modules import research_design_agent_revision as revision
from app.modules.research_design_service import (
    ResearchDesignRevisionUserError,
    ResolvedDefinition,
)
from app.modules.research_orchestration_repository import TaskRevisionConflictError
from pydantic import ValidationError


def _latest(ids=("sub_a", "sub_b")):
    return SimpleNamespace(instances=[SimpleNamespace(substage_instance_id=value) for value in ids])


def test_targeted_patch_accepts_only_same_ordered_canonical_substages():
    patch = revision._validate_targeted_patch(
        {
            "goal": "Compare only methods with open-source implementations.",
            "workItems": [
                {"substageInstanceId": "sub_a", "objective": "Keep the original first objective."},
                {"substageInstanceId": "sub_b", "objective": "Compare open-source methods only."},
            ],
        },
        _latest(),
    )
    assert [row.substageInstanceId for row in patch.workItems] == ["sub_a", "sub_b"]
    assert patch.workItems[1].objective == "Compare open-source methods only."


@pytest.mark.parametrize(
    "payload",
    [
        {
            "goal": "Goal",
            "workItems": [
                {"substageInstanceId": "sub_b", "objective": "B"},
                {"substageInstanceId": "sub_a", "objective": "A"},
            ],
        },
        {
            "goal": "Goal",
            "workItems": [
                {"substageInstanceId": "sub_a", "objective": "A"},
            ],
        },
        {
            "goal": "Goal",
            "workItems": [
                {"substageInstanceId": "sub_a", "objective": "A", "stageKey": "solution_design"},
                {"substageInstanceId": "sub_b", "objective": "B"},
            ],
        },
    ],
)
def test_identity_mutation_is_rejected(payload):
    with pytest.raises(ResearchDesignRevisionUserError):
        revision._validate_targeted_patch(payload, _latest())


@pytest.mark.parametrize(
    "payload",
    [
        {"expectedRevision": -1, "instruction": "change"},
        {"expectedRevision": 0, "instruction": ""},
        {"expectedRevision": 0, "instruction": "x" * 8001},
        {"expectedRevision": 0, "instruction": "change", "stageKey": "solution_design"},
        {"instruction": "change"},
    ],
)
def test_revision_input_rejects_invalid_or_extra_authority(payload):
    with pytest.raises(ValidationError):
        AgentResearchDesignRevisionIn.model_validate(payload)


def test_revision_input_accepts_instruction_and_expected_revision():
    request = AgentResearchDesignRevisionIn(expectedRevision=7, instruction="调整目标")
    assert request.model_dump() == {"expectedRevision": 7, "instruction": "调整目标"}


def test_stale_revision_never_calls_provider_or_writes():
    class Repository:
        def load(self, **kwargs):
            assert kwargs == {"task_id": "task-1", "owner_id": "owner", "workspace_id": "workspace"}
            return SimpleNamespace(revision=8, research_design_versions=[])

        def save(self, *_args, **_kwargs):
            pytest.fail("stale revision must not write")

    def provider(*_args):
        pytest.fail("stale revision must not call provider")

    with pytest.raises(TaskRevisionConflictError):
        revision.revise_research_design_with_agent(
            task_id="task-1",
            owner_id="owner",
            workspace_id="workspace",
            instruction="change",
            expected_task_revision=7,
            repository=Repository(),
            definition_source=object(),
            model_patch_provider=provider,
        )


def test_real_proposal_preserves_canonical_structural_authority():
    fixture = (
        Path(__file__).resolve().parents[6]
        / "packages/shared/test-fixtures/research-orchestration-v1.2-valid.json"
    )
    task = ResearchTaskV12.model_validate(
        json.loads(fixture.read_text(encoding="utf-8"))["researchTask"]
    )
    current = task.research_design_versions[-1]
    patch = revision._validate_targeted_patch(
        {
            "goal": "Revised bounded evidence summary",
            "workItems": [
                {"substageInstanceId": row.substage_instance_id, "objective": "Revised objective"}
                for row in current.instances
            ],
        },
        current,
    )

    class Definitions:
        def resolve(self, definition_id):
            row = next(
                item for item in current.instances if item.substage_definition_id == definition_id
            )
            return ResolvedDefinition(
                definition_id=definition_id,
                definition_version=row.substage_definition_version,
                stage_key=row.stage_key,
                required_input_refs=tuple(row.input_refs),
                artifact_types=tuple(row.expected_artifact_types),
                criterion_refs=(),
            )

    proposal = revision._proposal_from_current_design(current, patch)
    revision._assert_frozen_identity(current, proposal, Definitions())
    assert proposal.goal == patch.goal
    assert proposal.instances[0].objective == "Revised objective"
    assert proposal.understanding.model_dump(by_alias=True) == current.understanding.model_dump(
        by_alias=True
    )
    for boundary in (
        "scope_boundary",
        "budget_boundary",
        "external_action_boundary",
        "risk_boundary",
    ):
        assert getattr(proposal, boundary).model_dump(by_alias=True) == getattr(
            current, boundary
        ).model_dump(by_alias=True)
    assert proposal.instances[0].input_refs == list(current.instances[0].input_refs)
    assert proposal.instances[0].expected_artifact_types == list(
        current.instances[0].expected_artifact_types
    )


def _clear_provider_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "OPENAIR_RESEARCH_START_PLANNER_PROVIDER",
        "OPENTRAJECTORY_LLM_PROVIDER",
        "OPENAIR_RESEARCH_START_PLANNER_LLM_MODEL",
        "LLM_PROVIDER_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def _make_registry_resolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    # The default provider in schemas/providers/provider-registry.yaml speaks
    # huiyan_chat_completions; these variables let the planner binding resolve
    # so the registry adapter is known.
    monkeypatch.setenv("OPENAIR_RESEARCH_START_PLANNER_LLM_MODEL", "registry-model")
    monkeypatch.setenv("LLM_PROVIDER_MODEL", "registry-model")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")


def test_provider_selector_conflicting_with_registry_adapter_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_provider_environment(monkeypatch)
    _make_registry_resolvable(monkeypatch)
    monkeypatch.setenv("OPENTRAJECTORY_LLM_PROVIDER", "anthropic_messages")

    with pytest.raises(revision.ResearchDesignRevisionSaveUnavailable):
        revision._agent_revision_provider()


def test_provider_selector_matching_registry_adapter_is_honored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_provider_environment(monkeypatch)
    _make_registry_resolvable(monkeypatch)
    monkeypatch.setenv("OPENTRAJECTORY_LLM_PROVIDER", "openai_chat_completions")

    assert revision._agent_revision_provider() == "openai_chat_completions"


def test_legacy_provider_selector_survives_without_a_resolvable_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("OPENTRAJECTORY_LLM_PROVIDER", "anthropic_messages")

    assert revision._agent_revision_provider() == "anthropic_messages"
