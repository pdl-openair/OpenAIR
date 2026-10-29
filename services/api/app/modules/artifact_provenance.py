"""Artifact-03 来源链：关系写入逻辑（读侧不碰）。

``record_relations_for_call`` 在 ``task_store.add_artifact`` 的写入漏斗里被调用，把一次
capability call 的 input→output 显式化为 ``derived_from`` 关系。

**R1**：chat 流里 ``inputArtifactRefs`` 常被硬编码为 ``[]``；输入实际经 ``inputCallRefs``
到达 → 这里展开各前序 call 的 ``outputArtifactRefs`` 作为输入，否则 derived_from 近空。

约定：``derived_from(source=out, target=in)`` ——「产物 out derived_from 输入 in」。
对 out：outgoing(derived_from) = 它的来源；对 in：incoming(derived_from) = 用了它的产物。
"""

from __future__ import annotations

from typing import Any


def record_relations_for_call(task: dict[str, Any], call: dict[str, Any], store: Any) -> None:
    """derived_from：该 call 的每个输出产物 ↔ 每个输入产物。幂等（repo upsert）。"""
    outputs = call.get("outputArtifactRefs") or []
    if not outputs:
        return
    owner = task.get("ownerId")
    workspace = task.get("workspaceId")
    if not owner or not workspace:
        return

    inputs: set[str] = set(call.get("inputArtifactRefs") or [])
    calls = task.get("capabilityCalls") or []
    by_id = {c.get("id"): c for c in calls}
    for prev_id in (call.get("inputCallRefs") or []):
        prev = by_id.get(prev_id)
        if prev:
            inputs.update(prev.get("outputArtifactRefs") or [])
    if not inputs:
        return

    cap_id = call.get("id")
    for out in outputs:
        for inp in inputs:
            if not inp or not out or inp == out:
                continue
            store.record_artifact_relation(
                source_artifact_id=out,
                target_artifact_id=inp,
                relation_type="derived_from",
                owner_id=owner,
                workspace_id=workspace,
                capability_call_id=cap_id,
            )
