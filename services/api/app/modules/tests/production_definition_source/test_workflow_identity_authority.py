from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from app.modules.production_definition_source import ProductionDefinitionSource
from app.modules.production_workflow_source import ProductionWorkflowSource


def test_production_definition_source_implements_workflow_identity_authority(
    tmp_path: Path,
) -> None:
    workflow = {
        "schemaVersion": "workflow-definition.v1",
        "workflowId": "cross_pack_workflow",
        "workflowVersion": 1,
        "nameZh": "跨分类流程",
        "nameEn": "Cross-pack Workflow",
        "description": "验证正式 Definition authority 与 Workflow loader 的接口。",
        "skillPack": "pvd",
        "nodes": [
            {
                "nodeKey": "understand",
                "definitionId": "shared:understand",
                "definitionVersion": 1,
                "requirement": "required",
                "displayOrder": 10,
            }
        ],
        "dependencies": [],
        "boundaryPolicy": {
            "allowedExternalActions": [],
            "confirmationRequiredActions": [],
            "prohibitedActions": [],
        },
    }
    (tmp_path / "workflow.yaml").write_text(
        yaml.safe_dump(workflow, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    source = ProductionDefinitionSource.__new__(ProductionDefinitionSource)
    source._snapshot = SimpleNamespace(
        schema_version=1,
        content_hash="sha256:" + "a" * 64,
    )
    source._definitions = {
        "shared:understand": {
            "definitionId": "shared:understand",
            "definitionVersion": 1,
            "stage": "problem_modeling",
            "substageKey": "understand",
            "externalActionContract": {
                "status": "known",
                "requiredActionKinds": [],
            },
        }
    }
    source._agents = {}
    source._agent_bindings = ()

    workflows = ProductionWorkflowSource(
        tmp_path,
        definition_authority=source,
    )

    assert workflows.require("cross_pack_workflow", 1).nodes[0].definition_id == (
        "shared:understand"
    )
