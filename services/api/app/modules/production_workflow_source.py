"""从正式 YAML 目录加载并校验 WorkflowDefinition。"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Protocol

import yaml
from pydantic import ValidationError

from app.modules.repo_paths import repo_root
from app.models.external_action import (
    ExternalActionKind,
    missing_external_action_kinds,
)
from app.models.research_orchestration import StageKey
from app.models.workflow_definition import (
    FrozenRegistrySnapshotRef,
    FrozenWorkflowDefinition,
    WorkflowDefinition,
    WorkflowDefinitionV1,
    WorkflowDefinitionV2,
    freeze_workflow_definition,
)
from app.modules.yaml_contract_loader import load_unique_key_yaml


class _ResolvedDefinitionView(Protocol):
    definition_id: str
    definition_version: int
    substage_key: str
    stage_key: str
    required_external_action_kinds: tuple[ExternalActionKind, ...]
    external_action_contract_known: bool


class WorkflowDefinitionAuthority(Protocol):
    """Workflow 身份校验所需的最小只读接口。"""

    def snapshot_ref(self) -> Mapping[str, Any]: ...

    def resolve(
        self,
        definition_id: str,
        definition_version: int | None = None,
    ) -> _ResolvedDefinitionView | None: ...

    def resolve_version(
        self,
        definition_id: str,
        definition_version: int,
    ) -> _ResolvedDefinitionView | None: ...


class WorkflowDefinitionSourceError(ValueError):
    """Workflow 来源校验失败，并保留稳定错误码供上层判断。"""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


class ProductionWorkflowSource:
    """一次加载 Workflow，并绑定一份确定的 Definition snapshot。"""

    def __init__(
        self,
        workflow_directory: Path | str | None = None,
        *,
        definition_authority: WorkflowDefinitionAuthority,
    ) -> None:
        directory = (
            Path(workflow_directory)
            if workflow_directory is not None
            else repo_root() / "schemas" / "workflows"
        )
        self._workflow_directory = directory
        self._registry_snapshot_ref = self._load_registry_snapshot_ref(
            definition_authority
        )
        definitions = self._load(directory)
        self._validate_definition_refs(definitions, definition_authority)
        self._definitions = definitions

        latest_versions: dict[str, int] = {}
        for workflow_id, workflow_version in self._definitions:
            latest_versions[workflow_id] = max(
                workflow_version,
                latest_versions.get(workflow_id, 0),
            )
        self._latest_versions: Mapping[str, int] = MappingProxyType(latest_versions)

    @staticmethod
    def _load_registry_snapshot_ref(
        authority: WorkflowDefinitionAuthority,
    ) -> FrozenRegistrySnapshotRef:
        try:
            return FrozenRegistrySnapshotRef.model_validate(authority.snapshot_ref())
        except (AttributeError, TypeError, ValueError, ValidationError) as error:
            raise WorkflowDefinitionSourceError(
                "registry_snapshot_ref_invalid", str(error)
            ) from error

    @staticmethod
    def _load(
        directory: Path,
    ) -> Mapping[tuple[str, int], WorkflowDefinition]:
        if not directory.is_dir():
            raise WorkflowDefinitionSourceError(
                "workflow_directory_missing", str(directory)
            )
        files = sorted((*directory.glob("*.yaml"), *directory.glob("*.yml")))
        if not files:
            raise WorkflowDefinitionSourceError(
                "workflow_directory_empty", str(directory)
            )

        definitions: dict[tuple[str, int], WorkflowDefinition] = {}
        for path in files:
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                raise WorkflowDefinitionSourceError(
                    "workflow_file_unreadable", f"{path.name}: {error}"
                ) from error
            try:
                payload = load_unique_key_yaml(content)
            except yaml.YAMLError as error:
                raise WorkflowDefinitionSourceError(
                    "workflow_yaml_invalid", f"{path.name}: {error}"
                ) from error
            if not isinstance(payload, Mapping):
                raise WorkflowDefinitionSourceError(
                    "workflow_payload_invalid", path.name
                )
            try:
                schema_version = payload.get("schemaVersion")
                if schema_version == "workflow-definition.v1":
                    definition = WorkflowDefinitionV1.model_validate(dict(payload))
                elif schema_version == "workflow-definition.v2":
                    definition = WorkflowDefinitionV2.model_validate(dict(payload))
                else:
                    raise ValueError(f"unsupported Workflow schemaVersion: {schema_version}")
            except (ValidationError, ValueError) as error:
                raise WorkflowDefinitionSourceError(
                    "workflow_contract_invalid", f"{path.name}: {error}"
                ) from error
            key = (definition.workflow_id, definition.workflow_version)
            if key in definitions:
                raise WorkflowDefinitionSourceError(
                    "workflow_identity_duplicate",
                    f"{definition.workflow_id}@{definition.workflow_version}",
                )
            definitions[key] = definition
        return MappingProxyType(definitions)

    def _validate_definition_refs(
        self,
        definitions: Mapping[tuple[str, int], WorkflowDefinition],
        authority: WorkflowDefinitionAuthority,
    ) -> None:
        for workflow in definitions.values():
            for node in workflow.nodes:
                try:
                    exact_resolver = getattr(authority, "resolve_version", None)
                    resolved = (
                        exact_resolver(node.definition_id, node.definition_version)
                        if exact_resolver is not None
                        else authority.resolve(node.definition_id)
                    )
                    if resolved is None and exact_resolver is not None:
                        resolved = authority.resolve(node.definition_id)
                except (AttributeError, TypeError, ValueError) as error:
                    raise WorkflowDefinitionSourceError(
                        "definition_resolution_failed", node.definition_id
                    ) from error
                if resolved is None:
                    raise WorkflowDefinitionSourceError(
                        "definition_unknown", node.definition_id
                    )
                if resolved.definition_id != node.definition_id:
                    raise WorkflowDefinitionSourceError(
                        "definition_resolution_identity_mismatch", node.definition_id
                    )
                if (
                    isinstance(resolved.definition_version, bool)
                    or not isinstance(resolved.definition_version, int)
                    or resolved.definition_version < 1
                ):
                    raise WorkflowDefinitionSourceError(
                        "definition_resolution_invalid", node.definition_id
                    )
                if resolved.definition_version != node.definition_version:
                    raise WorkflowDefinitionSourceError(
                        "definition_version_mismatch",
                        (
                            f"{node.definition_id}: workflow={node.definition_version}, "
                            f"registry={resolved.definition_version}"
                        ),
                    )
                # 这里只校验稳定合同；分类、发布状态和运行能力
                # 不参与门禁。
                substage_key = getattr(resolved, "substage_key", None)
                if not isinstance(substage_key, str) or not substage_key.strip():
                    raise WorkflowDefinitionSourceError(
                        "definition_substage_identity_invalid", node.definition_id
                    )
                if node.node_key != substage_key:
                    raise WorkflowDefinitionSourceError(
                        "definition_substage_mismatch",
                        f"nodeKey={node.node_key}, substageKey={substage_key}",
                    )

                try:
                    StageKey(resolved.stage_key)
                except (TypeError, ValueError) as error:
                    raise WorkflowDefinitionSourceError(
                        "definition_stage_invalid",
                        f"{node.definition_id}: {resolved.stage_key}",
                    ) from error

                contract_known = getattr(
                    resolved, "external_action_contract_known", False
                )
                if contract_known is not True:
                    raise WorkflowDefinitionSourceError(
                        "definition_external_action_contract_unknown",
                        node.definition_id,
                    )
                required_actions = getattr(
                    resolved, "required_external_action_kinds", None
                )
                if (
                    not isinstance(required_actions, tuple)
                    or any(
                        not isinstance(action, ExternalActionKind)
                        for action in required_actions
                    )
                    or len(set(required_actions)) != len(required_actions)
                ):
                    raise WorkflowDefinitionSourceError(
                        "definition_external_action_contract_invalid",
                        node.definition_id,
                    )
                missing_actions = missing_external_action_kinds(
                    required_actions,
                    workflow.boundary_policy.allowed_external_actions,
                )
                if missing_actions:
                    raise WorkflowDefinitionSourceError(
                        "workflow_external_action_boundary_conflict",
                        (
                            f"{node.definition_id}: missing="
                            + ",".join(sorted(action.value for action in missing_actions))
                        ),
                    )

            if isinstance(workflow, WorkflowDefinitionV2):
                for support in workflow.supporting_work:
                    try:
                        exact_resolver = getattr(authority, "resolve_version", None)
                        resolved = (
                            exact_resolver(support.definition_id, support.definition_version)
                            if exact_resolver is not None
                            else authority.resolve(support.definition_id)
                        )
                        if resolved is None and exact_resolver is not None:
                            resolved = authority.resolve(support.definition_id)
                    except (AttributeError, TypeError, ValueError) as error:
                        raise WorkflowDefinitionSourceError(
                            "definition_resolution_failed", support.definition_id
                        ) from error
                    if resolved is None:
                        raise WorkflowDefinitionSourceError(
                            "definition_unknown", support.definition_id
                        )
                    if (
                        resolved.definition_id != support.definition_id
                        or resolved.definition_version != support.definition_version
                    ):
                        raise WorkflowDefinitionSourceError(
                            "definition_version_mismatch", support.definition_id
                        )
                    contract_known = getattr(resolved, "external_action_contract_known", False)
                    required_actions = getattr(resolved, "required_external_action_kinds", None)
                    if (
                        contract_known is not True
                        or not isinstance(required_actions, tuple)
                        or any(not isinstance(action, ExternalActionKind) for action in required_actions)
                    ):
                        raise WorkflowDefinitionSourceError(
                            "definition_external_action_contract_invalid", support.definition_id
                        )
                    missing_actions = missing_external_action_kinds(
                        required_actions, workflow.boundary_policy.allowed_external_actions
                    )
                    if missing_actions:
                        raise WorkflowDefinitionSourceError(
                            "workflow_external_action_boundary_conflict",
                            (
                                f"{support.definition_id}: missing="
                                + ",".join(sorted(action.value for action in missing_actions))
                            ),
                        )

    @property
    def workflow_directory(self) -> Path:
        return self._workflow_directory

    @property
    def registry_snapshot_ref(self) -> FrozenRegistrySnapshotRef:
        return self._registry_snapshot_ref

    def resolve(
        self,
        workflow_id: str,
        workflow_version: int | None = None,
    ) -> WorkflowDefinition | None:
        if workflow_version is None:
            workflow_version = self._latest_versions.get(workflow_id)
        if workflow_version is None:
            return None
        return self._definitions.get((workflow_id, workflow_version))

    def require(
        self,
        workflow_id: str,
        workflow_version: int | None = None,
    ) -> WorkflowDefinition:
        definition = self.resolve(workflow_id, workflow_version)
        if definition is None:
            suffix = "" if workflow_version is None else f"@{workflow_version}"
            raise WorkflowDefinitionSourceError(
                "workflow_unavailable", f"{workflow_id}{suffix}"
            )
        return definition

    def freeze(
        self,
        workflow_id: str,
        workflow_version: int | None = None,
    ) -> FrozenWorkflowDefinition:
        return freeze_workflow_definition(
            self.require(workflow_id, workflow_version),
            registry_snapshot_ref=self._registry_snapshot_ref,
        )

    def workflow_catalog(self) -> tuple[dict[str, Any], ...]:
        rows: list[dict[str, Any]] = []
        for workflow_id in sorted(self._latest_versions):
            definition = self.require(workflow_id)
            rows.append(
                {
                    "workflowId": definition.workflow_id,
                    "workflowVersion": definition.workflow_version,
                    "nameZh": definition.name_zh,
                    "nameEn": definition.name_en,
                    "description": definition.description,
                    "skillPack": definition.skill_pack,
                    "contentHash": definition.content_hash(),
                }
            )
        return tuple(rows)


__all__ = [
    "ProductionWorkflowSource",
    "WorkflowDefinitionAuthority",
    "WorkflowDefinitionSourceError",
]
