from __future__ import annotations

from copy import deepcopy

import pytest

from app.modules.research_package_asset import ResearchPackageAsset
from app.modules.research_package_protocol import validate_research_package
from app.modules.research_planning_sources import (
    PlanningSourceUseDecision,
    materialize_planning_source_usages,
    planning_source_prompt_rules,
    planning_sources_for_provider,
    resolve_research_package_planning_sources,
)


PACKAGE = {
    "spec_version": "2",
    "id": "rp-method-alpha",
    "name": "Reusable method alpha",
    "version": "2.0",
    "summary": "A prior validated research starting point.",
    "sources": [
        {"id": "source-main", "name": "Prior study", "ref": "doi:10.1000/example"}
    ],
    "method": {
        "goal": "Compare candidate mechanisms under bounded conditions.",
        "inputs": ["Current target and boundary conditions"],
        "logic": ["Separate transferable mechanism logic from case-specific values"],
        "outputs": ["A ranked set of hypotheses to validate"],
        "validation_criteria": ["Check against an independent current-project observation"],
        "source_refs": ["source-main"],
    },
    "reference_cases": [
        {
            "id": "case-main",
            "source_refs": ["source-main"],
            "target": "Validated the mechanism in the prior system",
            "conditions": ["Prior system condition A"],
            "results": [{"description": "Mechanism A matched the prior observation", "ref": "result:1"}],
            "resource_refs": ["code-main"],
        }
    ],
    "resources": [
        {
            "id": "code-main",
            "name": "Prior analysis code",
            "role": "code",
            "ref": "https://example.test/repo",
            "revision": "abc123",
        }
    ],
    "applicability": {
        "use_when": ["The current project asks an analogous mechanism question"],
        "preserve": ["Keep the comparison logic explicit"],
        "adaptable": ["Current-system parameter values"],
        "limits": ["Not validated outside the prior measurement regime"],
        "source_refs": ["source-main"],
    },
    "capabilities": ["Evaluate current evidence against candidate mechanisms"],
    "custom_note": {"meaning": "extension content remains available to planning"},
}


class _FakeAssetService:
    def __init__(self, package: dict) -> None:
        self.validated = validate_research_package(package)
        self.calls: list[tuple[str, str, str, str]] = []

    def read(self, *, owner_id: str, workspace_id: str, package_id: str, version: str):
        self.calls.append((owner_id, workspace_id, package_id, version))
        if (package_id, version) != (self.validated.package_id, self.validated.version):
            raise KeyError("not found")
        return ResearchPackageAsset(
            artifact_id="rp_artifact_exact_version",
            package=self.validated,
            source_task_id="task_prior",
        )


def _projected_source() -> dict:
    service = _FakeAssetService(deepcopy(PACKAGE))
    projected = resolve_research_package_planning_sources(
        [{"kind": "research_package", "id": PACKAGE["id"], "version": PACKAGE["version"]}],
        owner_id="owner",
        workspace_id="workspace",
        asset_service=service,
    )
    assert service.calls == [("owner", "workspace", PACKAGE["id"], PACKAGE["version"])]
    return projected[0]


def test_package_projection_preserves_exact_identity_and_source_semantics() -> None:
    source = _projected_source()

    assert source["sourceChoice"] == "planning_source_1"
    assert source["sourceKind"] == "research_package"
    assert source["sourceId"] == PACKAGE["id"]
    assert source["sourceVersion"] == PACKAGE["version"]
    assert source["sourceArtifactRef"] == "rp_artifact_exact_version"
    assert source["sourceContentHash"].startswith("sha256:")

    by_path = {row["sourcePath"]: row for row in source["units"]}
    assert by_path["/method/logic/0"]["content"] == PACKAGE["method"]["logic"][0]
    assert by_path["/method/logic/0"]["supportingSources"] == [
        {"id": "source-main", "name": "Prior study", "ref": "doi:10.1000/example"}
    ]
    assert by_path["/reference_cases/0/resource_refs/0"]["content"] == "code-main"
    assert by_path["/reference_cases/0/resource_refs/0"]["category"] == "reference_resource"
    assert by_path["/resources/0"]["category"] == "resource_reference"
    assert "/custom_note" in by_path


def test_provider_projection_keeps_formal_source_identity_server_owned() -> None:
    source = _projected_source()
    provider_source = planning_sources_for_provider([source])[0]

    assert set(provider_source) == {"sourceChoice", "name", "units"}
    assert provider_source["sourceChoice"] == "planning_source_1"
    assert provider_source["name"] == PACKAGE["name"]
    assert provider_source["units"][0]["unitNumber"] == 1
    assert "sourcePath" not in provider_source["units"][0]
    for forbidden in (
        "sourceKind",
        "sourceId",
        "sourceVersion",
        "sourceArtifactRef",
        "sourceContentHash",
    ):
        assert forbidden not in provider_source


def test_provider_decision_must_completely_partition_every_source_unit() -> None:
    source = _projected_source()
    numbers = [row["unitNumber"] for row in source["units"]]
    decision = PlanningSourceUseDecision(
        sourceChoice="planning_source_1",
        adoptedUnitNumbers=numbers[:2],
        notAdoptedUnitNumbers=numbers[2:-1],
        revalidationUnitNumbers=numbers[-1:],
    )

    (usage,) = materialize_planning_source_usages([decision], [source])
    assert usage.source_id == PACKAGE["id"]
    assert usage.source_version == PACKAGE["version"]
    assert usage.source_artifact_ref == "rp_artifact_exact_version"
    assert usage.adopted_source_paths == [
        source["units"][0]["sourcePath"],
        source["units"][1]["sourcePath"],
    ]
    assert usage.revalidation_source_paths == [source["units"][-1]["sourcePath"]]
    assert len(
        usage.adopted_source_paths
        + usage.not_adopted_source_paths
        + usage.revalidation_source_paths
    ) == len(source["units"])

    incomplete = PlanningSourceUseDecision(
        sourceChoice="planning_source_1",
        adoptedUnitNumbers=numbers[:-1],
        notAdoptedUnitNumbers=[],
        revalidationUnitNumbers=[],
    )
    with pytest.raises(ValueError, match="classify every supplied unit"):
        materialize_planning_source_usages([incomplete], [source])


def test_prompt_rules_preserve_authority_and_resource_boundary() -> None:
    rules = planning_source_prompt_rules([_projected_source()])

    assert "not current-project Evidence" in rules
    assert "classify every supplied unit exactly once" in rules.lower()
    assert "never means" in rules
    assert "obtained/bound" in rules
    assert "do not automatically become current-project completion requirements" in rules


def test_public_research_start_accepts_exact_versioned_planning_source_reference() -> None:
    from pydantic import ValidationError
    from app.routes.research_start import ResearchStartSubmitIn

    request = ResearchStartSubmitIn.model_validate({
        "input": "Use the prior method as a starting point for this current research question.",
        "clientRequestId": "client-rp-r3-1",
        "planningSources": [
            {"kind": "research_package", "id": "rp-method-alpha", "version": "2.0"}
        ],
    })
    assert request.planningSources[0].id == "rp-method-alpha"
    assert request.planningSources[0].version == "2.0"

    with pytest.raises(ValidationError):
        ResearchStartSubmitIn.model_validate({
            "input": "Use a prior source.",
            "clientRequestId": "client-rp-r3-2",
            "planningSources": [
                {"kind": "unknown_source", "id": "x", "version": "1"}
            ],
        })
