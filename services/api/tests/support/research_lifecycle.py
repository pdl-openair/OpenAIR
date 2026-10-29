"""Canonical structural and confirmed-lifecycle test builders."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
from typing import Any

from app.models.research_orchestration import ConfirmationV12, ResearchTaskV12
from app.modules.research_design_activation import (
    approved_design_activation_updates,
    design_versions_after_confirmation,
)
from app.modules.research_task_lifecycle import (
    ResearchDesignLifecycleFact,
    reconcile_task_lifecycle,
)


_STRUCTURAL_FIXTURE = (
    Path(__file__).resolve().parents[4]
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)
_DEFAULT_CONFIRMED_AT = datetime(2026, 7, 28, 0, 0, 3, tzinfo=timezone.utc)


def load_structural_research_task_payload() -> dict[str, Any]:
    """Return the shared JSON contract fixture without claiming lifecycle truth."""

    return deepcopy(
        json.loads(_STRUCTURAL_FIXTURE.read_text(encoding="utf-8"))["researchTask"]
    )


def _wire(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, list):
        return [_wire(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def build_confirmed_research_task(
    *,
    expected_artifact_types: list[str] | None = None,
    confirmed_at: datetime = _DEFAULT_CONFIRMED_AT,
) -> ResearchTaskV12:
    """Build a clean confirmed task through production lifecycle functions.

    The shared JSON fixture is a structural contract fixture. It intentionally is
    not treated as proof that a user-confirmed task passed through production
    activation. This builder starts from a draft/no-execution state, applies the
    production confirmation transition, and therefore creates the baseline
    ScientificState and runtime rows that real confirmation owns.
    """

    payload = load_structural_research_task_payload()
    design = payload["researchDesignVersions"][0]
    if expected_artifact_types is not None:
        design["instances"][0]["expectedArtifactTypes"] = list(expected_artifact_types)

    design.update(
        {
            "status": "draft",
            "confirmedAt": None,
            "confirmationRef": None,
        }
    )
    payload.update(
        {
            "status": "awaiting_confirmation",
            "activeResearchDesignRef": None,
            "reentryExecutionInstances": [],
            "substageRuntimeStates": [],
            "executionDecisions": [],
            "workUnits": [],
            "capabilityCalls": [],
            "results": [],
            "artifacts": [],
            "confirmations": [],
            "agentInteractionRecords": [],
            "progressProjection": None,
            "finalResultAuthority": None,
            "scientificStates": [],
            "completionDecisions": [],
            "finalResults": [],
            "startedAt": None,
            "finishedAt": None,
        }
    )
    base = ResearchTaskV12.model_validate(payload)

    confirmation_id = "confirmation_design_contract_full"
    confirmed_versions = design_versions_after_confirmation(
        versions=base.research_design_versions,
        design_id=base.research_design_versions[0].research_design_id,
        version=base.research_design_versions[0].version,
        decision="approved",
        confirmed_at=confirmed_at,
        confirmation_id=confirmation_id,
    )
    confirmed_design = confirmed_versions[0]
    confirmation = ConfirmationV12(
        confirmationId=confirmation_id,
        targetType="research_design",
        targetId=confirmed_design.research_design_id,
        targetVersion=confirmed_design.version,
        decision="approved",
        actorId="owner_contract",
        createdAt=confirmed_at,
    )
    activation = approved_design_activation_updates(
        task=base,
        design=confirmed_design,
        activated_at=confirmed_at,
    )

    confirmed_payload = base.model_dump(mode="json", by_alias=True)
    confirmed_payload["researchDesignVersions"] = _wire(confirmed_versions)
    confirmed_payload["confirmations"] = [_wire(confirmation)]
    for key, value in activation.items():
        confirmed_payload[key] = _wire(value)
    transition = reconcile_task_lifecycle(
        base.model_dump(mode="json", by_alias=True),
        fact=ResearchDesignLifecycleFact.DESIGN_APPROVED,
    )
    assert transition is not None
    confirmed_payload["status"] = transition.task_status
    return ResearchTaskV12.model_validate(confirmed_payload)
