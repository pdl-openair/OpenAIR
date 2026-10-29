from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app.models.external_action import ExternalActionKind
from app.models.workflow_definition import (
    FrozenWorkflowDefinitionV1,
    WorkflowDefinitionV1,
    WorkflowNodeDefinition,
    freeze_workflow_definition,
)
from app.modules.production_workflow_source import (
    ProductionWorkflowSource,
    WorkflowDefinitionSourceError,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_DIR = REPO_ROOT / "schemas" / "workflows"
REGISTRY_HASH = "sha256:" + "a" * 64


@dataclass(frozen=True)
class _ResolvedDefinition:
    definition_id: str
    definition_version: int
    stage_key: str
    substage_key: str
    enabled: bool = True
    required_external_action_kinds: tuple[ExternalActionKind, ...] = ()
    external_action_contract_known: bool = True


class _DefinitionAuthority:
    def __init__(
        self,
        definitions: list[_ResolvedDefinition],
        *,
        content_hash: str = REGISTRY_HASH,
    ) -> None:
        self._definitions_by_ref = {
            (row.definition_id, row.definition_version): row for row in definitions
        }
        self._definitions: dict[str, _ResolvedDefinition] = {}
        for row in definitions:
            current = self._definitions.get(row.definition_id)
            if current is None or row.definition_version > current.definition_version:
                self._definitions[row.definition_id] = row
        self._content_hash = content_hash

    def snapshot_ref(self) -> dict[str, Any]:
        return {"schemaVersion": 1, "contentHash": self._content_hash}

    def resolve(self, definition_id: str) -> _ResolvedDefinition | None:
        return self._definitions.get(definition_id)

    def resolve_version(
        self, definition_id: str, definition_version: int
    ) -> _ResolvedDefinition | None:
        return self._definitions_by_ref.get((definition_id, definition_version))

    def planner_catalog(self) -> tuple[dict[str, Any], ...]:
        raise AssertionError("Workflow 身份校验不应读取规划可见性或运行状态")


def _payload() -> dict[str, Any]:
    return {
        "schemaVersion": "workflow-definition.v1",
        "workflowId": "test_workflow",
        "workflowVersion": 1,
        "nameZh": "测试流程",
        "nameEn": "Test Workflow",
        "description": "结构测试流程",
        "skillPack": "test",
        "nodes": [
            {
                "nodeKey": "understand",
                "definitionId": "test:understand",
                "definitionVersion": 1,
                "requirement": "required",
                "displayOrder": 10,
            },
            {
                "nodeKey": "analyze",
                "definitionId": "test:analyze",
                "definitionVersion": 1,
                "requirement": "required",
                "displayOrder": 20,
            },
        ],
        "dependencies": [
            {
                "upstreamNodeKey": "understand",
                "downstreamNodeKey": "analyze",
                "dependencyType": "scientific_dependency",
            }
        ],
        "boundaryPolicy": {
            "allowedExternalActions": [],
            "confirmationRequiredActions": [],
            "prohibitedActions": [],
        },
    }


def _authority_for_payloads(*payloads: dict[str, Any]) -> _DefinitionAuthority:
    stage_cycle = (
        "problem_modeling",
        "solution_design",
        "execution_validation",
        "insight_generation",
    )
    definitions: list[_ResolvedDefinition] = []
    seen: set[str] = set()
    for payload in payloads:
        for index, node in enumerate(payload["nodes"]):
            definition_id = str(node["definitionId"])
            if definition_id in seen:
                continue
            seen.add(definition_id)
            definitions.append(
                _ResolvedDefinition(
                    definition_id=definition_id,
                    definition_version=int(node["definitionVersion"]),
                    stage_key=stage_cycle[index % len(stage_cycle)],
                    substage_key=str(node["nodeKey"]),
                )
            )
    return _DefinitionAuthority(definitions)


def _workflow_skill_packs() -> tuple[str, ...]:
    packs: set[str] = set()
    for workflow_path in WORKFLOW_DIR.glob("*.yaml"):
        payload = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        pack = str(payload.get("skillPack") or "").strip()
        if pack:
            packs.add(pack)
    return tuple(sorted(packs))


def _production_authority() -> _DefinitionAuthority:
    definitions: list[_ResolvedDefinition] = []
    for pack in _workflow_skill_packs():
        stage_map = yaml.safe_load(
            (REPO_ROOT / "schemas" / pack / "stage-substage-map.yaml").read_text(
                encoding="utf-8"
            )
        )
        action_catalog = yaml.safe_load(
            (
                REPO_ROOT
                / "schemas"
                / pack
                / "substage-external-actions.yaml"
            ).read_text(encoding="utf-8")
        )
        actions_by_substage = {
            row["substageKey"]: tuple(
                ExternalActionKind(value)
                for value in row["requiredExternalActions"]
            )
            for row in action_catalog["definitions"]
        }
        for stage, value in stage_map.items():
            entries = value.get("substages", []) if isinstance(value, dict) else value
            for entry in entries:
                versions = [int(entry.get("definitionVersion", 1))]
                versions.extend(
                    int(history["definitionVersion"])
                    for history in entry.get("historicalVersions", [])
                )
                definitions.extend(
                    _ResolvedDefinition(
                        definition_id=f"{pack}:{entry['key']}",
                        definition_version=version,
                        stage_key=stage,
                        substage_key=str(entry["key"]),
                        enabled=entry.get("enabled", True),
                        required_external_action_kinds=actions_by_substage[
                            entry["key"]
                        ],
                    )
                    for version in versions
                )
    return _DefinitionAuthority(definitions)


def _write_workflow(directory: Path, payload: dict[str, Any], name: str = "workflow.yaml") -> None:
    (directory / name).write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_production_source_loads_all_registered_workflows_as_structural_authority() -> None:
    source = ProductionWorkflowSource(
        WORKFLOW_DIR,
        definition_authority=_production_authority(),
    )

    ot = source.require("opentrajectory_research_workflow", 1)
    ot_latest = source.require("opentrajectory_research_workflow")
    materials = source.require("materials_candidate_discovery_workflow", 1)
    pvd = source.require("pvd_process_optimization_workflow", 1)
    pvd_latest = source.require("pvd_process_optimization_workflow")
    cs = source.require("cs_paper_reproduction_workflow", 1)
    cs_latest = source.require("cs_paper_reproduction_workflow")

    assert [node.node_key for node in ot.nodes] == [
        "intent_understanding",
        "constraints_criteria",
        "retrieval_strategy_design",
        "evidence_schema_design",
        "source_acquisition",
        "source_screening_selection",
        "progressive_reading",
        "evidence_extraction",
        "mechanism_explanation",
        "findings_synthesis",
    ]
    assert {
        node.node_key: node.requirement for node in ot.nodes
    } == {
        "intent_understanding": "required",
        "constraints_criteria": "required",
        "retrieval_strategy_design": "required",
        "evidence_schema_design": "required",
        "source_acquisition": "required",
        "source_screening_selection": "required",
        "progressive_reading": "required",
        "evidence_extraction": "required",
        "mechanism_explanation": "optional",
        "findings_synthesis": "required",
    }
    assert all(node.definition_version == 1 for node in ot.nodes)
    assert len(ot.dependencies) == len(ot.nodes) - 1

    assert ot_latest.workflow_version == 4
    assert ot_latest.schema_version == "workflow-definition.v2"
    assert [node.node_key for node in ot_latest.nodes] == [
        "research_record_establishment",
        "evidence_assessment_synthesis",
        "mechanism_explanation",
        "current_research_conclusion",
        "key_unknowns_candidate_explanations",
        "next_research_recommendation",
    ]
    assert [node.requirement for node in ot_latest.nodes] == [
        "optional", "required", "optional", "required", "required", "required"
    ]
    assert ot_latest.planning_policy.model_dump(mode="json", by_alias=True) == {
        "responsibilityPlanning": "project_adaptive",
        "existingResultReuse": "validated_existing_results",
        "conditionalResponsibilities": "planner_selected",
    }

    assert next(
        row for row in ot_latest.dependencies
        if row.upstream_node_key == "mechanism_explanation"
        and row.downstream_node_key == "current_research_conclusion"
    ).dependency_type == "optional_enhancement"

    assert [node.node_key for node in materials.nodes] == [
        "database_search",
        "candidate_generation",
    ]
    assert materials.dependencies[0].dependency_type == "execution_prerequisite"
    assert materials.boundary_policy.allowed_external_actions == (
        ExternalActionKind.EXTERNAL_READ,
        ExternalActionKind.EXTERNAL_COMPUTE,
    )
    assert materials.boundary_policy.confirmation_required_actions == ()

    assert [node.node_key for node in pvd.nodes] == [
        "research_goal_equipment_constraints",
        "research_retrieval_experiment_design",
        "literature_experiment_record_verification",
        "process_result_comparability_analysis",
        "process_factor_candidate_direction_analysis",
        "parameter_feasibility_experiment_plan_validation",
        "experimental_feedback_round_comparison",
        "four_parameter_recommendation",
    ]
    assert pvd.nodes[6].requirement == "optional"
    assert pvd.nodes[7].requirement == "required"
    assert pvd.skill_pack == "pvd"

    assert pvd_latest.workflow_version == 3
    assert pvd_latest.schema_version == "workflow-definition.v2"
    pvd_optional_edges = {
        (row.upstream_node_key, row.downstream_node_key): row.dependency_type
        for row in pvd_latest.dependencies
        if row.upstream_node_key in {
            "evidence_applicability_comparability_analysis",
            "experimental_feedback_round_analysis",
        }
    }
    assert pvd_optional_edges == {
        (
            "evidence_applicability_comparability_analysis",
            "process_factor_candidate_experiment_analysis",
        ): "optional_enhancement",
        (
            "experimental_feedback_round_analysis",
            "process_factor_candidate_experiment_analysis",
        ): "optional_enhancement",
    }

    assert [node.node_key for node in cs.nodes] == [
        "reproduction_input_structuring",
        "code_identity_acquisition",
        "reference_metric_acquisition",
        "execution_plan_acquisition",
    ]
    assert len(cs.dependencies) == 3
    assert cs.dependencies[-1].upstream_node_key == "reference_metric_acquisition"
    assert cs.dependencies[-1].downstream_node_key == "execution_plan_acquisition"
    assert cs.skill_pack == "cs_reproduction"

    assert cs_latest.workflow_version == 7
    assert cs_latest.schema_version == "workflow-definition.v2"
    assert [node.node_key for node in cs_latest.nodes] == [
        "reproduction_baseline_criteria",
        "reproduction_experiment_design",
        "execution_readiness_validation",
        "real_execution_result_acquisition",
        "result_comparability_judgment",
        "reproduction_conclusion_recommendation",
        "deviation_diagnosis_validation",
    ]
    assert [node.requirement for node in cs_latest.nodes] == [
        "required", "required", "required", "required", "required", "required", "optional"
    ]
    assert [node.definition_version for node in cs_latest.nodes] == [
        3, 3, 3, 3, 3, 4, 4
    ]
    assert cs_latest.planning_policy.model_dump(mode="json", by_alias=True) == {
        "responsibilityPlanning": "project_adaptive",
        "existingResultReuse": "validated_existing_results",
        "conditionalResponsibilities": "planner_selected",
    }

    assert next(
        row for row in cs_latest.dependencies
        if row.upstream_node_key == "deviation_diagnosis_validation"
        and row.downstream_node_key == "reproduction_conclusion_recommendation"
    ).dependency_type == "optional_enhancement"


def test_source_rejects_duplicate_workflow_yaml_keys(tmp_path: Path) -> None:
    (tmp_path / "workflow.yaml").write_text(
        """schemaVersion: workflow-definition.v1
workflowId: test_workflow
workflowId: overwritten_workflow
workflowVersion: 1
nameZh: 测试流程
nameEn: Test Workflow
description: 结构测试流程
skillPack: test
nodes: []
dependencies: []
boundaryPolicy:
  allowedExternalActions: []
  confirmationRequiredActions: []
  prohibitedActions: []
""",
        encoding="utf-8",
    )

    with pytest.raises(WorkflowDefinitionSourceError) as caught:
        ProductionWorkflowSource(
            tmp_path,
            definition_authority=_DefinitionAuthority([]),
        )

    assert caught.value.code == "workflow_yaml_invalid"


def test_display_text_is_trimmed_and_blank_text_is_rejected() -> None:
    payload = _payload()
    payload["nameZh"] = "  测试流程  "
    definition = WorkflowDefinitionV1.model_validate(payload)
    assert definition.name_zh == "测试流程"

    payload["description"] = "   "
    with pytest.raises(ValidationError):
        WorkflowDefinitionV1.model_validate(payload)


def test_workflow_node_contract_contains_structure_not_project_semantics() -> None:
    assert set(WorkflowNodeDefinition.model_fields) == {
        "node_key",
        "definition_id",
        "definition_version",
        "requirement",
        "display_order",
    }
    forbidden = {"objective", "target_outputs", "target_requirements", "acceptance_criteria"}
    assert forbidden.isdisjoint(WorkflowNodeDefinition.model_fields)


def test_freeze_round_trip_preserves_workflow_and_registry_identity() -> None:
    definition = WorkflowDefinitionV1.model_validate(_payload())
    frozen = freeze_workflow_definition(
        definition,
        registry_snapshot_ref={"schemaVersion": 1, "contentHash": REGISTRY_HASH},
    )
    round_trip = FrozenWorkflowDefinitionV1.model_validate(
        frozen.model_dump(mode="json", by_alias=True)
    )

    assert round_trip.content_hash == definition.content_hash()
    assert round_trip.definition == definition
    assert round_trip.registry_snapshot_ref.content_hash == REGISTRY_HASH
    assert len(round_trip.content_hash) == 64


def test_legacy_frozen_snapshot_keeps_its_original_content_hash() -> None:
    definition = WorkflowDefinitionV1.model_validate(_payload())
    frozen = freeze_workflow_definition(
        definition,
        registry_snapshot_ref={"schemaVersion": 1, "contentHash": REGISTRY_HASH},
    )
    legacy = frozen.model_dump(mode="json", by_alias=True)
    legacy_definition = legacy["definition"]
    legacy_definition["boundaryPolicy"].pop("scientificEvidenceBoundaries")
    legacy["contentHash"] = hashlib.sha256(
        json.dumps(
            legacy_definition,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    restored = FrozenWorkflowDefinitionV1.model_validate(legacy)

    assert restored.content_hash == legacy["contentHash"]
    assert restored.definition.boundary_policy.scientific_evidence_boundaries == ()


def test_frozen_snapshot_rejects_a_mismatched_raw_content_hash() -> None:
    definition = WorkflowDefinitionV1.model_validate(_payload())
    frozen = freeze_workflow_definition(
        definition,
        registry_snapshot_ref={"schemaVersion": 1, "contentHash": REGISTRY_HASH},
    )
    tampered = frozen.model_dump(mode="json", by_alias=True)
    tampered["definition"]["description"] = "被篡改的工作流"

    with pytest.raises(ValidationError, match="snapshot contentHash does not match definition"):
        FrozenWorkflowDefinitionV1.model_validate(tampered)


def test_definition_is_deeply_immutable() -> None:
    definition = WorkflowDefinitionV1.model_validate(_payload())

    with pytest.raises(ValidationError, match="frozen"):
        definition.workflow_version = 2
    with pytest.raises(ValidationError, match="frozen"):
        definition.nodes[0].node_key = "changed"
    with pytest.raises(ValidationError, match="frozen"):
        definition.boundary_policy.allowed_external_actions = ("external_read",)
    with pytest.raises(AttributeError):
        definition.nodes.pop()
    with pytest.raises(AttributeError):
        definition.dependencies.append(definition.dependencies[0])


def test_frozen_snapshot_does_not_alias_source_object() -> None:
    definition = WorkflowDefinitionV1.model_validate(_payload())
    frozen = freeze_workflow_definition(
        definition,
        registry_snapshot_ref={"schemaVersion": 1, "contentHash": REGISTRY_HASH},
    )

    assert frozen.definition == definition
    assert frozen.definition is not definition
    assert frozen.definition.nodes is not definition.nodes
    assert frozen.definition.nodes[0] is not definition.nodes[0]
    with pytest.raises(ValidationError, match="frozen"):
        frozen.registry_snapshot_ref.content_hash = "sha256:" + "b" * 64


def test_require_cannot_mutate_source_cache() -> None:
    source = ProductionWorkflowSource(
        WORKFLOW_DIR,
        definition_authority=_production_authority(),
    )
    definition = source.require("opentrajectory_research_workflow", 1)

    with pytest.raises(ValidationError, match="frozen"):
        definition.workflow_version = 2
    with pytest.raises(AttributeError):
        definition.nodes.pop()

    expected_node_keys = tuple(node.node_key for node in definition.nodes)
    reloaded = source.require("opentrajectory_research_workflow", 1)
    assert reloaded.workflow_version == 1
    assert tuple(node.node_key for node in reloaded.nodes) == expected_node_keys


def test_snapshot_hash_remains_valid_after_access() -> None:
    source = ProductionWorkflowSource(
        WORKFLOW_DIR,
        definition_authority=_production_authority(),
    )
    definition = source.require("opentrajectory_research_workflow", 1)
    frozen = source.freeze("opentrajectory_research_workflow", 1)

    with pytest.raises(ValidationError, match="frozen"):
        definition.workflow_version = 99
    with pytest.raises(AttributeError):
        frozen.definition.nodes.pop()

    assert frozen.content_hash == frozen.definition.content_hash()
    assert frozen.content_hash == source.freeze(
        "opentrajectory_research_workflow", 1
    ).content_hash
    assert frozen.registry_snapshot_ref == source.registry_snapshot_ref


def test_source_internal_indexes_are_read_only() -> None:
    source = ProductionWorkflowSource(
        WORKFLOW_DIR,
        definition_authority=_production_authority(),
    )
    definition = source.require("opentrajectory_research_workflow", 1)

    with pytest.raises(TypeError):
        source._definitions[("opentrajectory_research_workflow", 1)] = definition
    with pytest.raises(TypeError):
        source._latest_versions["opentrajectory_research_workflow"] = 99


def test_source_discovers_a_future_workflow_without_python_change(tmp_path: Path) -> None:
    first = _payload()
    second = deepcopy(first)
    second.update(
        {
            "workflowId": "future_pvd_workflow",
            "nameZh": "未来 PVD 流程",
            "nameEn": "Future PVD Workflow",
            "skillPack": "pvd",
        }
    )
    for node in second["nodes"]:
        node["definitionId"] = node["definitionId"].replace("test:", "pvd:")

    _write_workflow(tmp_path, first, "test.yaml")
    _write_workflow(tmp_path, second, "pvd.yaml")

    source = ProductionWorkflowSource(
        tmp_path,
        definition_authority=_authority_for_payloads(first, second),
    )
    catalog = source.workflow_catalog()

    assert [row["workflowId"] for row in catalog] == [
        "future_pvd_workflow",
        "test_workflow",
    ]
    assert source.require("future_pvd_workflow").skill_pack == "pvd"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda value: value["nodes"].append(deepcopy(value["nodes"][0])),
            "nodeKey values must be unique",
        ),
        (
            lambda value: value["dependencies"][0].update(
                {"upstreamNodeKey": "missing"}
            ),
            "unknown upstreamNodeKey",
        ),
        (
            lambda value: value["dependencies"].append(
                {
                    "upstreamNodeKey": "analyze",
                    "downstreamNodeKey": "understand",
                    "dependencyType": "scientific_dependency",
                }
            ),
            "must be acyclic",
        ),
        (
            lambda value: value["boundaryPolicy"].update(
                {
                    "allowedExternalActions": [],
                    "confirmationRequiredActions": ["external_read"],
                }
            ),
            "must be a subset",
        ),
        (
            lambda value: value["boundaryPolicy"].update(
                {"allowedExternalActions": ["database_query"]}
            ),
            "not a valid ExternalActionKind",
        ),
    ],
)
def test_invalid_workflow_contracts_fail_closed(mutate, message: str) -> None:
    payload = _payload()
    mutate(payload)
    with pytest.raises(ValidationError, match=message):
        WorkflowDefinitionV1.model_validate(payload)


def test_optional_node_marker_is_preserved() -> None:
    payload = _payload()
    payload["nodes"][0]["requirement"] = "optional"

    definition = WorkflowDefinitionV1.model_validate(payload)

    assert definition.nodes[0].requirement == "optional"
    assert definition.dependencies[0].upstream_node_key == "understand"


def test_optional_enhancement_dependency_requires_optional_upstream() -> None:
    payload = _payload()
    payload["dependencies"][0]["dependencyType"] = "optional_enhancement"

    with pytest.raises(ValidationError, match="requires an optional upstream"):
        WorkflowDefinitionV1.model_validate(payload)

    payload["nodes"][0]["requirement"] = "optional"
    definition = WorkflowDefinitionV1.model_validate(payload)
    assert definition.dependencies[0].dependency_type == "optional_enhancement"


def test_source_rejects_duplicate_workflow_identity(tmp_path: Path) -> None:
    payload = _payload()
    _write_workflow(tmp_path, payload, "a.yaml")
    _write_workflow(tmp_path, payload, "b.yaml")

    with pytest.raises(WorkflowDefinitionSourceError) as caught:
        ProductionWorkflowSource(
            tmp_path,
            definition_authority=_authority_for_payloads(payload),
        )
    assert caught.value.code == "workflow_identity_duplicate"


def test_source_rejects_unknown_definition(tmp_path: Path) -> None:
    payload = _payload()
    _write_workflow(tmp_path, payload)
    authority = _DefinitionAuthority(
        [_ResolvedDefinition("test:understand", 1, "problem_modeling", "understand")]
    )

    with pytest.raises(WorkflowDefinitionSourceError) as caught:
        ProductionWorkflowSource(tmp_path, definition_authority=authority)
    assert caught.value.code == "definition_unknown"
    assert "test:analyze" in caught.value.detail


def test_source_rejects_definition_version_mismatch(tmp_path: Path) -> None:
    payload = _payload()
    payload["nodes"][0]["definitionVersion"] = 2
    _write_workflow(tmp_path, payload)

    with pytest.raises(WorkflowDefinitionSourceError) as caught:
        ProductionWorkflowSource(
            tmp_path,
            definition_authority=_authority_for_payloads(_payload()),
        )
    assert caught.value.code == "definition_version_mismatch"


def test_source_identity_validation_does_not_use_definition_status(tmp_path: Path) -> None:
    payload = _payload()
    _write_workflow(tmp_path, payload)
    authority = _DefinitionAuthority(
        [
            _ResolvedDefinition(
                "test:understand", 1, "problem_modeling", "understand"
            ),
            _ResolvedDefinition(
                "test:analyze", 1, "solution_design", "analyze", enabled=False
            ),
        ]
    )

    source = ProductionWorkflowSource(tmp_path, definition_authority=authority)

    assert source.require("test_workflow", 1).nodes[1].definition_id == "test:analyze"


def test_source_allows_cross_pack_substage_reuse(tmp_path: Path) -> None:
    payload = _payload()
    payload["skillPack"] = "pvd"
    payload["nodes"][0]["definitionId"] = "opentrajectory:understand"
    payload["nodes"][1]["definitionId"] = "shared:analyze"
    _write_workflow(tmp_path, payload)
    authority = _DefinitionAuthority(
        [
            _ResolvedDefinition(
                "opentrajectory:understand", 1, "problem_modeling", "understand"
            ),
            _ResolvedDefinition(
                "shared:analyze", 1, "solution_design", "analyze"
            ),
        ]
    )

    source = ProductionWorkflowSource(tmp_path, definition_authority=authority)

    workflow = source.require("test_workflow", 1)
    assert workflow.skill_pack == "pvd"
    assert [node.definition_id for node in workflow.nodes] == [
        "opentrajectory:understand",
        "shared:analyze",
    ]


def test_source_rejects_missing_definition_substage_identity(tmp_path: Path) -> None:
    payload = _payload()
    _write_workflow(tmp_path, payload)
    authority = _DefinitionAuthority(
        [
            _ResolvedDefinition(
                "test:understand", 1, "problem_modeling", ""
            ),
            _ResolvedDefinition(
                "test:analyze", 1, "solution_design", "analyze"
            ),
        ]
    )

    with pytest.raises(WorkflowDefinitionSourceError) as caught:
        ProductionWorkflowSource(tmp_path, definition_authority=authority)
    assert caught.value.code == "definition_substage_identity_invalid"


def test_source_rejects_definition_substage_mismatch(tmp_path: Path) -> None:
    payload = _payload()
    payload["nodes"][0]["definitionId"] = "shared:different"
    _write_workflow(tmp_path, payload)
    authority = _DefinitionAuthority(
        [
            _ResolvedDefinition(
                "shared:different", 1, "problem_modeling", "different"
            ),
            _ResolvedDefinition(
                "test:analyze", 1, "solution_design", "analyze"
            ),
        ]
    )

    with pytest.raises(WorkflowDefinitionSourceError) as caught:
        ProductionWorkflowSource(tmp_path, definition_authority=authority)
    assert caught.value.code == "definition_substage_mismatch"


def test_source_rejects_invalid_definition_stage(tmp_path: Path) -> None:
    payload = _payload()
    _write_workflow(tmp_path, payload)
    authority = _DefinitionAuthority(
        [
            _ResolvedDefinition("test:understand", 1, "wrong_stage", "understand"),
            _ResolvedDefinition("test:analyze", 1, "solution_design", "analyze"),
        ]
    )

    with pytest.raises(WorkflowDefinitionSourceError) as caught:
        ProductionWorkflowSource(tmp_path, definition_authority=authority)
    assert caught.value.code == "definition_stage_invalid"


def test_source_rejects_unknown_definition_external_action_contract(
    tmp_path: Path,
) -> None:
    payload = _payload()
    _write_workflow(tmp_path, payload)
    authority = _DefinitionAuthority(
        [
            _ResolvedDefinition(
                "test:understand",
                1,
                "problem_modeling",
                "understand",
                external_action_contract_known=False,
            ),
            _ResolvedDefinition(
                "test:analyze", 1, "solution_design", "analyze"
            ),
        ]
    )

    with pytest.raises(WorkflowDefinitionSourceError) as caught:
        ProductionWorkflowSource(tmp_path, definition_authority=authority)
    assert caught.value.code == "definition_external_action_contract_unknown"


def test_source_rejects_malformed_definition_external_action_contract(
    tmp_path: Path,
) -> None:
    payload = _payload()
    _write_workflow(tmp_path, payload)
    authority = _DefinitionAuthority(
        [
            _ResolvedDefinition(
                "test:understand",
                1,
                "problem_modeling",
                "understand",
                required_external_action_kinds=("external_read",),  # type: ignore[arg-type]
            ),
            _ResolvedDefinition(
                "test:analyze", 1, "solution_design", "analyze"
            ),
        ]
    )

    with pytest.raises(WorkflowDefinitionSourceError) as caught:
        ProductionWorkflowSource(tmp_path, definition_authority=authority)
    assert caught.value.code == "definition_external_action_contract_invalid"


def test_source_rejects_external_action_boundary_conflict(tmp_path: Path) -> None:
    payload = _payload()
    _write_workflow(tmp_path, payload)
    authority = _DefinitionAuthority(
        [
            _ResolvedDefinition(
                "test:understand",
                1,
                "problem_modeling",
                "understand",
                required_external_action_kinds=(
                    ExternalActionKind.EXTERNAL_READ,
                ),
            ),
            _ResolvedDefinition(
                "test:analyze", 1, "solution_design", "analyze"
            ),
        ]
    )

    with pytest.raises(WorkflowDefinitionSourceError) as caught:
        ProductionWorkflowSource(tmp_path, definition_authority=authority)
    assert caught.value.code == "workflow_external_action_boundary_conflict"
    assert "external_read" in caught.value.detail


def test_optional_node_must_also_fit_workflow_external_action_boundary(
    tmp_path: Path,
) -> None:
    payload = _payload()
    payload["nodes"][0]["requirement"] = "optional"
    _write_workflow(tmp_path, payload)
    authority = _DefinitionAuthority(
        [
            _ResolvedDefinition(
                "test:understand",
                1,
                "problem_modeling",
                "understand",
                required_external_action_kinds=(
                    ExternalActionKind.EXTERNAL_READ,
                ),
            ),
            _ResolvedDefinition(
                "test:analyze", 1, "solution_design", "analyze"
            ),
        ]
    )

    with pytest.raises(WorkflowDefinitionSourceError) as caught:
        ProductionWorkflowSource(tmp_path, definition_authority=authority)

    assert caught.value.code == "workflow_external_action_boundary_conflict"


def test_source_accepts_required_actions_inside_workflow_boundary(
    tmp_path: Path,
) -> None:
    payload = _payload()
    payload["boundaryPolicy"]["allowedExternalActions"] = ["external_read"]
    _write_workflow(tmp_path, payload)
    authority = _DefinitionAuthority(
        [
            _ResolvedDefinition(
                "test:understand",
                1,
                "problem_modeling",
                "understand",
                required_external_action_kinds=(
                    ExternalActionKind.EXTERNAL_READ,
                ),
            ),
            _ResolvedDefinition(
                "test:analyze", 1, "solution_design", "analyze"
            ),
        ]
    )

    source = ProductionWorkflowSource(tmp_path, definition_authority=authority)

    assert source.require("test_workflow", 1).boundary_policy.allowed_external_actions == (
        ExternalActionKind.EXTERNAL_READ,
    )


def test_source_rejects_invalid_registry_snapshot_ref(tmp_path: Path) -> None:
    payload = _payload()
    _write_workflow(tmp_path, payload)
    authority = _DefinitionAuthority(
        [
            _ResolvedDefinition("test:understand", 1, "problem_modeling", "understand"),
            _ResolvedDefinition("test:analyze", 1, "solution_design", "analyze"),
        ],
        content_hash="not-a-hash",
    )

    with pytest.raises(WorkflowDefinitionSourceError) as caught:
        ProductionWorkflowSource(tmp_path, definition_authority=authority)
    assert caught.value.code == "registry_snapshot_ref_invalid"


def test_frozen_workflow_keeps_exact_registry_snapshot_ref(tmp_path: Path) -> None:
    payload = _payload()
    _write_workflow(tmp_path, payload)
    authority = _authority_for_payloads(payload)
    source = ProductionWorkflowSource(tmp_path, definition_authority=authority)

    frozen = source.freeze("test_workflow", 1)

    assert frozen.registry_snapshot_ref.schema_version == 1
    assert frozen.registry_snapshot_ref.content_hash == REGISTRY_HASH
    assert frozen.registry_snapshot_ref == source.registry_snapshot_ref
