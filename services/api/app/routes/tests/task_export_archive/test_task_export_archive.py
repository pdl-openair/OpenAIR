"""Task-level export contract: zip packs verified artifact bytes plus a manifest.

The export must never silently drop content: skipped artifacts are recorded in
manifest.json with a skipReason, and a task without artifacts fails closed.
"""

import hashlib
import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules.task_export_archive import TaskExportError, build_task_export_zip
from app.routes import tasks as routes


def inline_artifact(artifact_id: str, title: str, content: str, fmt: str = "markdown"):
    return {
        "id": artifact_id,
        "title": title,
        "format": fmt,
        "kind": "research",
        "relatedStage": "execution_validation",
        "storageType": "inline",
        "inlineContent": content,
        "contentSha256": hashlib.sha256(content.encode()).hexdigest(),
        "contentSize": len(content.encode()),
    }


def task_with(artifacts):
    return {
        "taskId": "task-export-1",
        "title": "导出测试项目",
        "artifacts": artifacts,
    }


def read_zip(blob: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_zip_packs_artifact_bodies_and_manifest():
    blob, zip_name = build_task_export_zip(
        task=task_with(
            [inline_artifact("a1", "研究报告", "# 报告"), inline_artifact("a2", "数据表", "a,b", fmt="csv")]
        )
    )
    files = read_zip(blob)
    manifest = json.loads(files["manifest.json"])
    assert manifest["taskId"] == "task-export-1"
    assert manifest["artifactCount"] == 2
    assert manifest["packedCount"] == 2
    assert manifest["truncated"] is False
    assert files["研究报告.md"] == "# 报告".encode()
    assert files["数据表.csv"] == b"a,b"
    assert zip_name.startswith("openair_")
    assert zip_name.endswith(".zip")


def test_unavailable_content_is_skipped_and_recorded():
    broken = inline_artifact("a-broken", "缺失正文", "内容")
    broken.pop("inlineContent")
    blob, _ = build_task_export_zip(task=task_with([inline_artifact("a1", "正常", "ok"), broken]))
    files = read_zip(blob)
    manifest = json.loads(files["manifest.json"])
    assert manifest["packedCount"] == 1
    skipped = next(item for item in manifest["artifacts"] if item["artifactId"] == "a-broken")
    assert skipped["skipped"] is True
    assert skipped["skipReason"] == "content_unavailable"
    assert "正常.md" in files


def test_size_limit_truncates_and_marks_manifest(monkeypatch):
    from app.modules import task_export_archive

    monkeypatch.setattr(task_export_archive, "_MAX_EXPORT_BYTES", 1)
    blob, _ = build_task_export_zip(task=task_with([inline_artifact("a1", "超限", "内容超限")]))
    manifest = json.loads(read_zip(blob)["manifest.json"])
    assert manifest["truncated"] is True
    assert manifest["packedCount"] == 0


def test_duplicate_titles_get_unique_filenames():
    blob, _ = build_task_export_zip(
        task=task_with([inline_artifact("a1", "同名", "一"), inline_artifact("a2", "同名", "二")])
    )
    files = read_zip(blob)
    names = [name for name in files if name != "manifest.json"]
    assert len(names) == 2
    assert len(set(names)) == 2


def test_task_without_artifacts_fails_closed():
    with pytest.raises(TaskExportError):
        build_task_export_zip(task=task_with([]))


def test_route_exports_scoped_task(monkeypatch):
    monkeypatch.setattr(routes, "settings", SimpleNamespace(persistence_backend="json"))
    monkeypatch.setattr(
        routes.store,
        "get_task",
        lambda task_id: task_with([inline_artifact("a1", "研究报告", "# 报告")]),
    )
    monkeypatch.setattr(
        routes,
        "ensure_task_in_scope",
        lambda task, identity: dict(task, ownerId="u1", workspaceId="w1"),
    )
    response = routes.export_task("task-export-1", identity=SimpleNamespace())
    assert response.media_type == "application/zip"
    assert "attachment" in response.headers["content-disposition"]
    assert "研究报告.md" in read_zip(response.body)


def test_route_404_when_task_has_no_artifacts(monkeypatch):
    monkeypatch.setattr(routes, "settings", SimpleNamespace(persistence_backend="json"))
    empty = dict(task_with([]), ownerId="u1", workspaceId="w1")
    monkeypatch.setattr(routes.store, "get_task", lambda task_id: empty)
    monkeypatch.setattr(routes, "ensure_task_in_scope", lambda task, identity: task)
    with pytest.raises(HTTPException) as excinfo:
        routes.export_task("task-export-1", identity=SimpleNamespace())
    assert excinfo.value.status_code == 404
