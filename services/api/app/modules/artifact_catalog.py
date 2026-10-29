"""产物库目录服务（Artifact-01，读侧）。

Approach B：直接从内存 ``store`` 聚合各 task 内嵌的 artifacts，按 owner/workspace
scope 过滤、搜索、分页。不依赖独立表或对象存储——``openair_artifacts`` 表与懒迁移
归 Artifact-02（写侧）。产物在读取时由所属 task 注入 ``sourceTaskId``。
"""

from __future__ import annotations

from typing import Any

from app.auth import RequestIdentity
from app.config import settings
from app.modules.artifact_content import load_artifact_content
from app.modules.artifact_repository import ArtifactRepository
from app.modules.task_store import store

_DEFAULT_ORIGIN = "generated"
_HIDDEN_VISIBILITY = {"internal", "diagnostic", "audit", "developer", "hidden"}


def _iter_scoped_tasks(identity: RequestIdentity) -> list[dict[str, Any]]:
    owner_id = identity["ownerId"]
    workspace_id = identity["workspaceId"]
    return [
        task
        for task in store.list_tasks()
        if task.get("ownerId") == owner_id and task.get("workspaceId") == workspace_id
    ]


def _to_catalog_row(artifact: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    row = dict(artifact)
    row.setdefault("origin", _DEFAULT_ORIGIN)
    row["sourceTaskId"] = task.get("taskId")
    row["sourceTaskTitle"] = task.get("title")
    return row


def is_artifact_user_visible(row: dict[str, Any]) -> bool:
    """Apply the catalog's canonical ordinary-user visibility policy."""

    visibility = row.get("artifactVisibility") or row.get("visibility") or row.get("audience")
    return not (isinstance(visibility, str) and visibility.lower() in _HIDDEN_VISIBILITY)


def list_catalog_artifacts(
    identity: RequestIdentity,
    *,
    kind: str | None = None,
    origin: str | None = None,
    source_task_id: str | None = None,
    related_stage: str | None = None,
    format_: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
    search_inline: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """聚合当前 scope 下全部产物，做精确过滤 + 大小写不敏感搜索 + 分页。返回 (rows, total)。"""
    if settings.persistence_backend == "postgres":
        # Canonical v1.2 lineage objects embedded in a Task are not catalog
        # rows. PostgreSQL catalog reads must use the first-class artifact
        # table, which owns the public metadata projection.
        return ArtifactRepository().list_by_scope(
            identity["ownerId"],
            identity["workspaceId"],
            kind=kind,
            origin=origin,
            source_task_id=source_task_id,
            related_stage=related_stage,
            format=format_,
            q=q,
            page=page,
            page_size=page_size,
            search_inline=search_inline,
        )
    # fallback：json/测试后端，从内存 store 聚合
    rows: list[dict[str, Any]] = []
    for task in _iter_scoped_tasks(identity):
        if source_task_id and task.get("taskId") != source_task_id:
            continue
        for artifact in task.get("artifacts") or []:
            row = _to_catalog_row(artifact, task)
            if is_artifact_user_visible(row):
                rows.append(row)

    if kind is not None:
        rows = [r for r in rows if r.get("kind") == kind]
    if origin is not None:
        rows = [r for r in rows if r.get("origin") == origin]
    if related_stage is not None:
        rows = [r for r in rows if r.get("relatedStage") == related_stage]
    if format_ is not None:
        rows = [r for r in rows if r.get("format") == format_]

    if q:
        needle = q.lower()
        keys = ["title", "summary"]
        if search_inline:
            keys.append("inlineContent")

        def _matches(row: dict[str, Any]) -> bool:
            return any(
                isinstance(row.get(key), str) and needle in row.get(key, "").lower()
                for key in keys
            )

        rows = [r for r in rows if _matches(r)]

    rows.sort(key=lambda r: r.get("createdAt") or "", reverse=True)

    total = len(rows)
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 20
    start = (page - 1) * page_size
    return rows[start : start + page_size], total


def find_artifact_by_id(
    artifact_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """按 id 在全部 task 中查找产物（不预过滤 scope），返回 (task, artifact)。

    调用方随后用 ``ensure_artifact_in_scope`` 做 owner/workspace 校验：命中但越权 → 403；
    完全不存在 → 404。与 task 路由的 scope 语义一致。
    """
    if settings.persistence_backend == "postgres":
        artifact = ArtifactRepository().get_by_id(artifact_id)
        if artifact is None:
            return None, None
        # content/download 仍需所属 task 的 capabilityCalls（replay 解析）
        task = store.get_task(artifact.get("sourceTaskId") or "")
        return task, artifact
    for task in store.list_tasks():
        for artifact in task.get("artifacts") or []:
            if artifact.get("id") == artifact_id:
                return task, artifact
    return None, None


def reuse_artifact(
    source_artifact: dict[str, Any],
    source_task: dict[str, Any],
    target_task_id: str,
    *,
    title: str | None = None,
    summary: str | None = None,
    identity: RequestIdentity | None = None,
) -> dict[str, Any] | None:
    """在目标 task 下复用产物：新建 origin=imported 产物，记 reuses 关系 + reusedFrom。

    Postgres 后端走内容寻址：源产物正文物化进 object_store（sha256 去重），新产物
    ``storage_type="object"`` 指向同一 content_sha256——内容独立于源 task 生命周期，
    跨项目同内容零拷贝。catalog 行 + 关系均经 postgres 原生 repo（``store.add_artifact``
    与 ``store.record_artifact_relation`` 在 postgres 模式被禁用）。
    """
    reused_from = {
        "artifactId": source_artifact.get("id"),
        "taskId": source_task.get("taskId"),
        "artifactTitle": source_artifact.get("title"),
        "taskTitle": source_task.get("title"),
    }
    if settings.persistence_backend == "postgres":
        new_artifact = _reuse_artifact_postgres(
            source_artifact,
            source_task,
            target_task_id,
            title=title,
            summary=summary,
            reused_from=reused_from,
            identity=identity,
        )
    else:
        new_artifact = _reuse_artifact_json(
            source_artifact,
            source_task,
            target_task_id,
            title=title,
            summary=summary,
            reused_from=reused_from,
        )
    if new_artifact is None:
        return None
    # Record the reuses relation via the backend-appropriate repo.
    owner_id = (identity or {}).get("ownerId") or new_artifact.get("ownerId")
    workspace_id = (identity or {}).get("workspaceId") or new_artifact.get("workspaceId")
    if settings.persistence_backend == "postgres":
        from app.modules.artifact_relation_repository import ArtifactRelationRepository

        ArtifactRelationRepository().upsert_relation(
            source_artifact_id=new_artifact["id"],
            target_artifact_id=source_artifact["id"],
            relation_type="reuses",
            owner_id=owner_id,
            workspace_id=workspace_id,
            metadata={"reusedFrom": reused_from},
            created_at=new_artifact.get("createdAt"),
        )
    else:
        store.record_artifact_relation(
            source_artifact_id=new_artifact["id"],
            target_artifact_id=source_artifact["id"],
            relation_type="reuses",
            owner_id=owner_id,
            workspace_id=workspace_id,
            metadata={"reusedFrom": reused_from},
        )
    return new_artifact


def _reuse_artifact_json(
    source_artifact: dict[str, Any],
    source_task: dict[str, Any],
    target_task_id: str,
    *,
    title: str | None,
    summary: str | None,
    reused_from: dict[str, Any],
) -> dict[str, Any] | None:
    """JSON/dev backend reuse: inline-copy content, add via TaskStore."""
    storage_type = source_artifact.get("storageType")
    inline_content = source_artifact.get("inlineContent")
    path = source_artifact.get("path")
    # A local/replay path is relative to its source task. Copy its resolved
    # content into the imported artifact so the target task can still open it
    # after handoff; provenance remains explicit in ``reusedFrom``.
    if storage_type != "external" and not inline_content:
        try:
            inline_content, _source_mode = load_artifact_content(
                source_task["taskId"],
                source_artifact,
                source_task.get("capabilityCalls") or [],
            )
            storage_type = "inline"
            path = None
        except FileNotFoundError:
            # The target still receives a traceable imported artifact, but the
            # existing unavailable-artifact UI must disclose the missing file.
            pass
    return store.add_artifact(
        target_task_id,
        kind=source_artifact.get("kind") or "imported_artifact",
        title=title or source_artifact.get("title") or "reused artifact",
        summary=summary if summary is not None else source_artifact.get("summary"),
        path=path,
        format=source_artifact.get("format"),
        related_stage=source_artifact.get("relatedStage"),
        related_skill_id=source_artifact.get("relatedSkillId"),
        storage_type=storage_type,
        inline_content=inline_content,
        extra_fields={
            "origin": "imported",
            "artifactState": source_artifact.get("artifactState") or "usable",
            "metadata": {"reusedFrom": reused_from},
        },
    )


def _reuse_artifact_postgres(
    source_artifact: dict[str, Any],
    source_task: dict[str, Any],
    target_task_id: str,
    *,
    title: str | None,
    summary: str | None,
    reused_from: dict[str, Any],
    identity: RequestIdentity | None,
) -> dict[str, Any] | None:
    """Postgres reuse: content-addressed blob + native catalog/relation repos."""
    from datetime import datetime, timezone
    from uuid import uuid4

    from app.modules import object_store
    from app.modules.artifact_content import load_artifact_binary

    # 1. Source content bytes (covers inline / external / local / object).
    blob, _mode = load_artifact_binary(
        source_task["taskId"],
        source_artifact,
        source_task.get("capabilityCalls") or [],
    )
    # 2. Materialise into the content store (dedup; key = sha256).
    content_sha256 = object_store.put_content(blob)

    from app.modules.task_repository import TaskRepository

    target_task = TaskRepository().get_task(target_task_id) or {}
    owner_id = target_task.get("ownerId") or (identity or {}).get("ownerId")
    workspace_id = target_task.get("workspaceId") or (identity or {}).get("workspaceId")
    now = datetime.now(timezone.utc).isoformat()
    new_artifact = {
        "id": f"art_{uuid4().hex}",
        "ownerId": owner_id,
        "workspaceId": workspace_id,
        "createdBy": target_task.get("createdBy") or owner_id,
        "kind": source_artifact.get("kind") or "imported_artifact",
        "origin": "imported",
        "title": title or source_artifact.get("title") or "reused artifact",
        "summary": summary if summary is not None else source_artifact.get("summary"),
        "format": source_artifact.get("format"),
        "storageType": "object",
        "contentSha256": content_sha256,
        "contentSize": len(blob),
        "relatedStage": source_artifact.get("relatedStage") or source_artifact.get("stage"),
        "relatedSkillId": source_artifact.get("relatedSkillId"),
        "sourceTaskId": target_task_id,
        "artifactState": source_artifact.get("artifactState") or "usable",
        "metadata": {"reusedFrom": reused_from},
        "createdAt": now,
        "updatedAt": now,
    }
    ArtifactRepository().upsert_from_dict(
        target_task_id, new_artifact, task_title=target_task.get("title")
    )
    return new_artifact
