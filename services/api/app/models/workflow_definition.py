"""定义 Workflow 的不可变结构与快照合同。"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal, Mapping, TypeAlias

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.models.external_action import ExternalActionKind
from app.models.research_orchestration import RegistrySnapshotRef


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


def _yaml_list_to_tuple(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(value)
    return value


def _external_actions_to_tuple(value: Any) -> Any:
    if not isinstance(value, (list, tuple)):
        return value
    return tuple(
        item if isinstance(item, ExternalActionKind) else ExternalActionKind(item)
        for item in value
    )


def _strip_display_text(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    return value


class _WorkflowModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
        strict=True,
        frozen=True,
    )


class FrozenRegistrySnapshotRef(RegistrySnapshotRef):
    """Workflow 快照中使用的不可变 Registry 快照引用。"""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
        strict=True,
        frozen=True,
    )


Identifier = Annotated[
    str,
    Field(min_length=1, max_length=160, pattern=r"^[a-z][a-z0-9_:\-.]*$"),
]
DisplayText = Annotated[
    str,
    BeforeValidator(_strip_display_text),
    Field(min_length=1, max_length=500),
]
DisplayTextTuple = Annotated[
    tuple[DisplayText, ...],
    BeforeValidator(_yaml_list_to_tuple),
    Field(max_length=16),
]
PositiveVersion = Annotated[int, Field(ge=1)]

WorkflowNodeRequirement = Literal["required", "optional"]
WorkflowDependencyType = Literal[
    "scientific_dependency",
    "execution_prerequisite",
    "optional_enhancement",
]

ExternalActionTuple = Annotated[
    tuple[ExternalActionKind, ...],
    BeforeValidator(_external_actions_to_tuple),
    Field(max_length=4),
]


class WorkflowNodeDefinition(_WorkflowModel):
    """Workflow 中一个稳定研究职责的 Definition 引用。"""

    node_key: Identifier
    definition_id: Identifier
    definition_version: PositiveVersion
    requirement: WorkflowNodeRequirement
    display_order: Annotated[int, Field(ge=0, le=100_000)]


class WorkflowDependencyDefinition(_WorkflowModel):
    """两个 Workflow 研究职责之间的必要依赖。"""

    upstream_node_key: Identifier
    downstream_node_key: Identifier
    dependency_type: WorkflowDependencyType


class WorkflowBoundaryPolicy(_WorkflowModel):
    """Workflow 固定的外部动作和风险边界。"""

    allowed_external_actions: ExternalActionTuple
    confirmation_required_actions: ExternalActionTuple
    prohibited_actions: ExternalActionTuple
    scientific_evidence_boundaries: DisplayTextTuple = ()

    @model_validator(mode="after")
    def validate_action_sets(self) -> "WorkflowBoundaryPolicy":
        allowed = set(self.allowed_external_actions)
        confirmation = set(self.confirmation_required_actions)
        prohibited = set(self.prohibited_actions)
        if len(allowed) != len(self.allowed_external_actions):
            raise ValueError("allowedExternalActions contains duplicates")
        if len(confirmation) != len(self.confirmation_required_actions):
            raise ValueError("confirmationRequiredActions contains duplicates")
        if len(prohibited) != len(self.prohibited_actions):
            raise ValueError("prohibitedActions contains duplicates")
        if not confirmation.issubset(allowed):
            raise ValueError(
                "confirmationRequiredActions must be a subset of allowedExternalActions"
            )
        overlap = allowed & prohibited
        if overlap:
            raise ValueError(
                "an external action cannot be both allowed and prohibited: "
                + ", ".join(sorted(overlap))
            )
        return self


class WorkflowPlanningPolicyV2(_WorkflowModel):
    """V2 Workflow 明确把稳定职责与项目任务数量解耦。"""

    responsibility_planning: Literal["project_adaptive"]
    existing_result_reuse: Literal["validated_existing_results"]
    conditional_responsibilities: Literal["planner_selected"]


class WorkflowTargetOutputDefinition(_WorkflowModel):
    """A stable user-visible completion deliverable owned by one Workflow responsibility."""

    description: DisplayText
    owner_node_key: Identifier


class WorkflowSupportingWorkDefinition(_WorkflowModel):
    """A conditional project task that prepares material for one responsibility.

    Supporting work is not a stable Workflow responsibility.  It is materialized
    deterministically into ResearchDesign only when the supported responsibility
    is planned in the current project.
    """

    support_key: Identifier
    definition_id: Identifier
    definition_version: PositiveVersion
    supports_node_key: Identifier
    depends_on_support_keys: Annotated[
        tuple[Identifier, ...], BeforeValidator(_yaml_list_to_tuple), Field(max_length=32)
    ] = ()
    display_order: Annotated[int, Field(ge=0, le=100_000)]


WorkflowNodeTuple = Annotated[
    tuple[WorkflowNodeDefinition, ...],
    BeforeValidator(_yaml_list_to_tuple),
    Field(min_length=1, max_length=256),
]
WorkflowDependencyTuple = Annotated[
    tuple[WorkflowDependencyDefinition, ...],
    BeforeValidator(_yaml_list_to_tuple),
    Field(max_length=1024),
]
WorkflowSupportingWorkTuple = Annotated[
    tuple[WorkflowSupportingWorkDefinition, ...],
    BeforeValidator(_yaml_list_to_tuple),
    Field(max_length=256),
]
WorkflowTargetOutputTuple = Annotated[
    tuple[WorkflowTargetOutputDefinition, ...],
    BeforeValidator(_yaml_list_to_tuple),
    Field(max_length=32),
]


def _validate_graph(
    *,
    nodes: tuple[WorkflowNodeDefinition, ...],
    dependencies: tuple[WorkflowDependencyDefinition, ...],
) -> None:
    node_keys = [node.node_key for node in nodes]
    definition_ids = [node.definition_id for node in nodes]
    display_orders = [node.display_order for node in nodes]
    if len(set(node_keys)) != len(node_keys):
        raise ValueError("Workflow nodeKey values must be unique")
    if len(set(definition_ids)) != len(definition_ids):
        raise ValueError("Workflow definitionId values must be unique")
    if len(set(display_orders)) != len(display_orders):
        raise ValueError("Workflow displayOrder values must be unique")

    nodes_by_key = {node.node_key: node for node in nodes}
    edge_keys: set[tuple[str, str, str]] = set()
    graph: dict[str, list[str]] = {key: [] for key in node_keys}
    for dependency in dependencies:
        upstream = dependency.upstream_node_key
        downstream = dependency.downstream_node_key
        if upstream not in nodes_by_key:
            raise ValueError(f"unknown upstreamNodeKey: {upstream}")
        if downstream not in nodes_by_key:
            raise ValueError(f"unknown downstreamNodeKey: {downstream}")
        if upstream == downstream:
            raise ValueError(
                f"Workflow dependency cannot be self-referential: {upstream}"
            )
        edge_key = (upstream, downstream, dependency.dependency_type)
        if edge_key in edge_keys:
            raise ValueError(
                f"duplicate Workflow dependency: {upstream} -> {downstream}"
            )
        if (
            dependency.dependency_type == "optional_enhancement"
            and nodes_by_key[upstream].requirement != "optional"
        ):
            raise ValueError(
                "optional_enhancement requires an optional upstream Workflow node: "
                f"{upstream}"
            )
        edge_keys.add(edge_key)
        graph[upstream].append(downstream)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_key: str) -> None:
        if node_key in visiting:
            raise ValueError("Workflow dependencies must be acyclic")
        if node_key in visited:
            return
        visiting.add(node_key)
        for downstream in graph[node_key]:
            visit(downstream)
        visiting.remove(node_key)
        visited.add(node_key)

    for node_key in node_keys:
        visit(node_key)


def _content_hash(model: BaseModel) -> str:
    return _mapping_content_hash(model.model_dump(mode="json", by_alias=True))


def _mapping_content_hash(value: Mapping[str, Any]) -> str:
    """Hash the exact frozen definition representation supplied at the boundary."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_raw_snapshot_content_hash(value: Any) -> None:
    """Validate a frozen snapshot before Pydantic supplies newly-added defaults.

    A frozen Workflow is an immutable record.  Its checksum must describe the
    representation that was actually persisted, rather than a newer model's
    normalized representation after a backward-compatible default is added.
    """
    if not isinstance(value, Mapping):
        return
    raw_definition = value.get("definition")
    if isinstance(raw_definition, BaseModel):
        raw_definition = raw_definition.model_dump(mode="json", by_alias=True)
    content_hash = value.get("contentHash", value.get("content_hash"))
    if isinstance(raw_definition, Mapping) and isinstance(content_hash, str):
        if content_hash != _mapping_content_hash(raw_definition):
            raise ValueError("snapshot contentHash does not match definition")


class WorkflowDefinitionV1(_WorkflowModel):
    """历史 V1：节点直接冻结为 ResearchDesign 必选/可选工作。"""

    schema_version: Literal["workflow-definition.v1"]
    workflow_id: Identifier
    workflow_version: PositiveVersion
    name_zh: DisplayText
    name_en: DisplayText
    description: DisplayText
    skill_pack: Identifier
    nodes: WorkflowNodeTuple
    dependencies: WorkflowDependencyTuple
    boundary_policy: WorkflowBoundaryPolicy

    @model_validator(mode="after")
    def validate_graph(self) -> "WorkflowDefinitionV1":
        _validate_graph(nodes=self.nodes, dependencies=self.dependencies)
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)

    def content_hash(self) -> str:
        return _content_hash(self)


class WorkflowDefinitionV2(_WorkflowModel):
    """职责型 Workflow：固定专业责任与依赖，不固定项目任务数量。"""

    schema_version: Literal["workflow-definition.v2"]
    workflow_id: Identifier
    workflow_version: PositiveVersion
    name_zh: DisplayText
    name_en: DisplayText
    description: DisplayText
    skill_pack: Identifier
    planning_policy: WorkflowPlanningPolicyV2
    target_output_specs: WorkflowTargetOutputTuple = ()
    nodes: WorkflowNodeTuple
    dependencies: WorkflowDependencyTuple
    supporting_work: WorkflowSupportingWorkTuple = ()
    boundary_policy: WorkflowBoundaryPolicy

    @model_validator(mode="after")
    def validate_graph(self) -> "WorkflowDefinitionV2":
        _validate_graph(nodes=self.nodes, dependencies=self.dependencies)
        if not any(node.requirement == "required" for node in self.nodes):
            raise ValueError("Workflow V2 requires at least one core responsibility")

        node_keys = {node.node_key for node in self.nodes}
        if unknown_target_owner_keys := {row.owner_node_key for row in self.target_output_specs} - node_keys:
            raise ValueError(
                "targetOutputSpecs references unknown nodes: "
                + ", ".join(sorted(unknown_target_owner_keys))
            )
        node_definition_ids = {node.definition_id for node in self.nodes}
        support_keys = [row.support_key for row in self.supporting_work]
        support_definition_ids = [row.definition_id for row in self.supporting_work]
        support_orders = [row.display_order for row in self.supporting_work]
        if len(set(support_keys)) != len(support_keys):
            raise ValueError("Workflow supportingWork supportKey values must be unique")
        if len(set(support_definition_ids)) != len(support_definition_ids):
            raise ValueError("Workflow supportingWork definitionId values must be unique")
        if len(set(support_orders)) != len(support_orders):
            raise ValueError("Workflow supportingWork displayOrder values must be unique")
        if node_definition_ids & set(support_definition_ids):
            raise ValueError("Workflow supportingWork cannot duplicate a responsibility Definition")

        support_by_key = {row.support_key: row for row in self.supporting_work}
        for row in self.supporting_work:
            if row.supports_node_key not in node_keys:
                raise ValueError(f"unknown supportingWork supportsNodeKey: {row.supports_node_key}")
            if len(set(row.depends_on_support_keys)) != len(row.depends_on_support_keys):
                raise ValueError(f"duplicate supportingWork dependency: {row.support_key}")
            for dependency_key in row.depends_on_support_keys:
                dependency = support_by_key.get(dependency_key)
                if dependency is None:
                    raise ValueError(f"unknown supportingWork dependency: {dependency_key}")
                if dependency.supports_node_key != row.supports_node_key:
                    raise ValueError("supportingWork dependencies must serve the same responsibility")

        visiting: set[str] = set()
        visited: set[str] = set()
        def visit_support(support_key: str) -> None:
            if support_key in visiting:
                raise ValueError("Workflow supportingWork dependencies must be acyclic")
            if support_key in visited:
                return
            visiting.add(support_key)
            for dependency_key in support_by_key[support_key].depends_on_support_keys:
                visit_support(dependency_key)
            visiting.remove(support_key)
            visited.add(support_key)
        for support_key in support_keys:
            visit_support(support_key)
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)

    def content_hash(self) -> str:
        return _content_hash(self)


WorkflowDefinition: TypeAlias = WorkflowDefinitionV1 | WorkflowDefinitionV2


class FrozenWorkflowDefinitionV1(_WorkflowModel):
    """历史 V1 Task 持久化使用的 Workflow 与 Registry 联合快照。"""

    schema_version: Literal["workflow-definition-snapshot.v1"]
    workflow_id: Identifier
    workflow_version: PositiveVersion
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    registry_snapshot_ref: FrozenRegistrySnapshotRef
    definition: WorkflowDefinitionV1

    @model_validator(mode="before")
    @classmethod
    def validate_raw_snapshot_content_hash(cls, value: Any) -> Any:
        _validate_raw_snapshot_content_hash(value)
        return value

    @model_validator(mode="after")
    def validate_snapshot_identity(self) -> "FrozenWorkflowDefinitionV1":
        if self.workflow_id != self.definition.workflow_id:
            raise ValueError("snapshot workflowId does not match definition")
        if self.workflow_version != self.definition.workflow_version:
            raise ValueError("snapshot workflowVersion does not match definition")
        return self


class FrozenWorkflowDefinitionV2(_WorkflowModel):
    """职责型 Workflow 的不可变 Workflow + Registry 快照。"""

    schema_version: Literal["workflow-definition-snapshot.v2"]
    workflow_id: Identifier
    workflow_version: PositiveVersion
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    registry_snapshot_ref: FrozenRegistrySnapshotRef
    definition: WorkflowDefinitionV2

    @model_validator(mode="before")
    @classmethod
    def validate_raw_snapshot_content_hash(cls, value: Any) -> Any:
        _validate_raw_snapshot_content_hash(value)
        return value

    @model_validator(mode="after")
    def validate_snapshot_identity(self) -> "FrozenWorkflowDefinitionV2":
        if self.workflow_id != self.definition.workflow_id:
            raise ValueError("snapshot workflowId does not match definition")
        if self.workflow_version != self.definition.workflow_version:
            raise ValueError("snapshot workflowVersion does not match definition")
        return self


FrozenWorkflowDefinition: TypeAlias = FrozenWorkflowDefinitionV1 | FrozenWorkflowDefinitionV2


def parse_frozen_workflow_definition(value: Mapping[str, Any]) -> FrozenWorkflowDefinition:
    schema_version = value.get("schemaVersion")
    if schema_version == "workflow-definition-snapshot.v1":
        return FrozenWorkflowDefinitionV1.model_validate(value)
    if schema_version == "workflow-definition-snapshot.v2":
        return FrozenWorkflowDefinitionV2.model_validate(value)
    raise ValueError(f"unsupported Workflow snapshot schemaVersion: {schema_version}")


def freeze_workflow_definition(
    definition: WorkflowDefinition,
    *,
    registry_snapshot_ref: RegistrySnapshotRef | Mapping[str, Any],
) -> FrozenWorkflowDefinition:
    """冻结 Workflow，并绑定规划时使用的确切 Registry 快照。"""
    frozen_registry_ref = FrozenRegistrySnapshotRef.model_validate(
        registry_snapshot_ref
        if isinstance(registry_snapshot_ref, Mapping)
        else registry_snapshot_ref.model_dump(mode="json", by_alias=True)
    )
    if isinstance(definition, WorkflowDefinitionV2):
        detached = WorkflowDefinitionV2.model_validate(
            definition.model_dump(mode="json", by_alias=True)
        )
        return FrozenWorkflowDefinitionV2(
            schema_version="workflow-definition-snapshot.v2",
            workflow_id=detached.workflow_id,
            workflow_version=detached.workflow_version,
            content_hash=detached.content_hash(),
            registry_snapshot_ref=frozen_registry_ref,
            definition=detached,
        )
    detached = WorkflowDefinitionV1.model_validate(
        definition.model_dump(mode="json", by_alias=True)
    )
    return FrozenWorkflowDefinitionV1(
        schema_version="workflow-definition-snapshot.v1",
        workflow_id=detached.workflow_id,
        workflow_version=detached.workflow_version,
        content_hash=detached.content_hash(),
        registry_snapshot_ref=frozen_registry_ref,
        definition=detached,
    )


__all__ = [
    "FrozenRegistrySnapshotRef",
    "FrozenWorkflowDefinition",
    "FrozenWorkflowDefinitionV1",
    "FrozenWorkflowDefinitionV2",
    "WorkflowBoundaryPolicy",
    "WorkflowDefinition",
    "WorkflowDefinitionV1",
    "WorkflowDefinitionV2",
    "WorkflowDependencyDefinition",
    "WorkflowDependencyType",
    "WorkflowNodeDefinition",
    "WorkflowNodeRequirement",
    "WorkflowPlanningPolicyV2",
    "WorkflowSupportingWorkDefinition",
    "freeze_workflow_definition",
    "parse_frozen_workflow_definition",
]
