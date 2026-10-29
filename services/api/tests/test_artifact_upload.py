"""Artifact-02 上传 + presigned 下载测试（json 后端，storage=local，无真 MinIO）。

覆盖：用户上传（inline 嵌入 + origin=user_upload）、scope 403、超限 413、
external 下载返回 presigned url（mock）、inline 下载 200、
ArtifactWriteService 在 minio 模式下尊重 inline 阈值（mock upload_bytes）。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.modules.artifact_write import ArtifactStorageError, artifact_write_service
from app.modules.task_store import store

OWNER_A = "ownerA"
WS_A = "wsA"
OWNER_B = "ownerB"
WS_B = "wsB"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolated_store():
    original = store._tasks.copy()
    store._tasks.clear()
    try:
        yield
    finally:
        store._tasks.clear()
        store._tasks.update(original)


def _headers(owner: str, ws: str) -> dict[str, str]:
    return {"X-OpenAIR-Owner-Id": owner, "X-OpenAIR-Workspace-Id": ws}


def _create_task(owner: str, ws: str, title: str = "task") -> str:
    task = store.create_task(
        {
            "title": title,
            "goalSummary": "upload test",
            "domain": "research_task",
            "currentStage": "problem_modeling",
            "status": "running",
            "ownerId": owner,
            "workspaceId": ws,
        }
    )
    return str(task["taskId"])


def _set_updated_at(task_id: str, value: str) -> None:
    store._tasks[task_id]["updatedAt"] = value


# ---------------------------------------------------------------------------
# upload
# ---------------------------------------------------------------------------


def test_upload_inline_appears_in_catalog(client: TestClient) -> None:
    t = _create_task(OWNER_A, WS_A)
    r = client.post(
        "/api/artifacts/upload",
        files={"file": ("note.md", b"# hello world", "text/markdown")},
        data={"targetTaskId": t, "kind": "report", "title": "我的上传"},
        headers=_headers(OWNER_A, WS_A),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["origin"] == "user_upload"
    assert body["storageType"] == "inline"
    assert body["ownerId"] == OWNER_A

    # 出现在目录里，且可按 origin=user_upload 过滤
    lst = client.get("/api/artifacts?origin=user_upload", headers=_headers(OWNER_A, WS_A)).json()
    assert any(a["id"] == body["id"] for a in lst["artifacts"])


def test_upload_to_others_task_forbidden(client: TestClient) -> None:
    t_b = _create_task(OWNER_B, WS_B)
    r = client.post(
        "/api/artifacts/upload",
        files={"file": ("note.md", b"x", "text/markdown")},
        data={"targetTaskId": t_b},
        headers=_headers(OWNER_A, WS_A),
    )
    assert r.status_code == 403


def test_upload_oversized_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    t = _create_task(OWNER_A, WS_A)
    monkeypatch.setattr(settings, "artifact_upload_max_bytes", 8)
    r = client.post(
        "/api/artifacts/upload",
        files={"file": ("big.bin", b"x" * 50, "application/octet-stream")},
        data={"targetTaskId": t},
        headers=_headers(OWNER_A, WS_A),
    )
    assert r.status_code == 413


def test_upload_filename_path_traversal_sanitized(client: TestClient) -> None:
    t = _create_task(OWNER_A, WS_A)
    r = client.post(
        "/api/artifacts/upload",
        files={"file": ("../../etc/evil.md", b"# x", "text/markdown")},
        data={"targetTaskId": t},
        headers=_headers(OWNER_A, WS_A),
    )
    assert r.status_code == 200
    assert r.json()["title"] == "evil.md"  # basename only


def test_material_seed_note_upload_defaults_to_latest_task_and_creates_memory(client: TestClient) -> None:
    old_ope = _create_task(OWNER_A, WS_A, "Old OPE task")
    material_task = _create_task(OWNER_A, WS_A, "Materials evidence task")
    _set_updated_at(old_ope, "2026-07-14T02:00:00Z")
    _set_updated_at(material_task, "2026-07-14T01:00:00Z")

    seed_note = b"""# Seed Notes: sulfide SSE / Li metal interface

User intent:
- Compare interface stability against lithium metal.
- Separate computational predictions from experimental evidence.

Missing inputs:
- Seed review papers.
- Raw EIS spectra.
"""
    r = client.post(
        "/api/artifacts/upload",
        files={"file": ("materials_probe_seed_note_20260714.md", seed_note, "text/markdown")},
        data={"kind": "materials_probe_seed_note"},
        headers=_headers(OWNER_A, WS_A),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["taskId"] == material_task
    assert body["sourceTaskId"] == material_task
    assert body["linkedMaterialIds"]
    material_id = body["linkedMaterialIds"][0]
    assert body["materialManifest"][0]["materialId"] == material_id
    assert body["materialManifest"][0]["kind"] == "materials_probe_seed_note"

    old = store.get_task(old_ope)
    target = store.get_task(material_task)
    assert old is not None and all(a["id"] != body["id"] for a in old["artifacts"])
    assert target is not None
    assert [m["materialId"] for m in target["materials"]] == [material_id]
    memory_kinds = {m["kind"] for m in target["memoryRecords"]}
    assert {"user_fact", "agent_inference", "artifact_summary"} <= memory_kinds

    detail = client.get(f"/api/artifacts/{body['id']}", headers=_headers(OWNER_A, WS_A))
    assert detail.status_code == 200
    assert detail.json()["linkedMaterialIds"] == [material_id]
    content = client.get(f"/api/artifacts/{body['id']}/content", headers=_headers(OWNER_A, WS_A))
    assert content.status_code == 200
    assert content.json()["linkedMaterialIds"] == [material_id]

    pack = store.build_material_context_pack(material_task, [material_id])
    assert pack is not None
    assert pack["materials"]["mustUse"] == [material_id]
    assert pack["materials"]["selectedExcerpts"][0]["materialId"] == material_id


def test_cross_task_upload_requires_confirmation_then_links_material(client: TestClient) -> None:
    source = _create_task(OWNER_A, WS_A, "source task")
    target = _create_task(OWNER_A, WS_A, "target task")

    first = client.post(
        "/api/artifacts/upload",
        files={"file": ("note.md", b"# note", "text/markdown")},
        data={"targetTaskId": target, "currentTaskId": source},
        headers=_headers(OWNER_A, WS_A),
    )
    assert first.status_code == 409
    detail = first.json()["detail"]
    assert detail["code"] == "ownership_confirmation_required"
    assert detail["reason"] == "cross_task"
    assert detail["sourceTaskId"] == source
    assert detail["targetTaskId"] == target

    confirmed = client.post(
        "/api/artifacts/upload",
        files={"file": ("note.md", b"# note", "text/markdown")},
        data={"targetTaskId": target, "currentTaskId": source, "ownershipConfirmed": "true"},
        headers=_headers(OWNER_A, WS_A),
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["taskId"] == target
    assert body["linkedMaterialIds"]


def test_uploaded_artifact_ownership_can_be_corrected_with_confirmation(client: TestClient) -> None:
    wrong = _create_task(OWNER_A, WS_A, "wrong task")
    correct = _create_task(OWNER_A, WS_A, "correct materials task")
    upload = client.post(
        "/api/artifacts/upload",
        files={"file": ("materials_probe_seed_note_20260714.md", b"# Seed note", "text/markdown")},
        data={"targetTaskId": wrong, "kind": "materials_probe_seed_note"},
        headers=_headers(OWNER_A, WS_A),
    )
    assert upload.status_code == 200
    artifact_id = upload.json()["id"]
    material_id = upload.json()["linkedMaterialIds"][0]
    sibling = store.add_artifact(
        wrong,
        kind="note_summary",
        title="Sibling artifact",
        artifact_id="sibling_artifact",
        storage_type="inline",
        inline_content="# sibling",
    )
    assert sibling is not None
    store.update_artifact_material_binding(wrong, sibling["id"], [material_id])

    blocked = client.patch(
        f"/api/artifacts/{artifact_id}/ownership",
        json={"targetTaskId": correct, "currentTaskId": wrong},
        headers=_headers(OWNER_A, WS_A),
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "ownership_confirmation_required"

    moved = client.patch(
        f"/api/artifacts/{artifact_id}/ownership",
        json={
            "targetTaskId": correct,
            "currentTaskId": wrong,
            "ownershipConfirmed": True,
            "note": "Corrected after upload.",
        },
        headers=_headers(OWNER_A, WS_A),
    )
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert body["sourceTaskId"] == correct
    assert body["linkedMaterialIds"] == [material_id]

    wrong_task = store.get_task(wrong)
    correct_task = store.get_task(correct)
    assert wrong_task is not None and all(a["id"] != artifact_id for a in wrong_task["artifacts"])
    assert [m["materialId"] for m in wrong_task["materials"]] == [material_id]
    assert wrong_task["materials"][0]["linkedArtifactIds"] == [sibling["id"]]
    assert correct_task is not None
    assert any(a["id"] == artifact_id for a in correct_task["artifacts"])
    assert [m["materialId"] for m in correct_task["materials"]] == [material_id]
    assert correct_task["materials"][0]["linkedArtifactIds"] == [artifact_id]


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------


def test_download_inline_returns_file(client: TestClient) -> None:
    t = _create_task(OWNER_A, WS_A)
    store.add_artifact(
        t, kind="report", title="a", format="markdown",
        storage_type="inline", inline_content="# data", artifact_id="a1",
    )
    r = client.get("/api/artifacts/a1/download", headers=_headers(OWNER_A, WS_A))
    assert r.status_code == 200


def test_download_external_returns_presigned_url(client: TestClient) -> None:
    t = _create_task(OWNER_A, WS_A)
    store.add_artifact(
        t, kind="model", title="m", format="safetensors",
        storage_type="external", artifact_id="a1",
        extra_fields={"externalKey": "task_x/obj"},
    )
    with patch(
        "app.modules.minio_client.presigned_get_object",
        return_value="http://mock-minio/task_x/obj?sig=abc",
    ):
        r = client.get("/api/artifacts/a1/download", headers=_headers(OWNER_A, WS_A))
    assert r.status_code == 200
    assert r.json()["url"].startswith("http://mock-minio/")


def test_download_external_404_when_no_key(client: TestClient) -> None:
    t = _create_task(OWNER_A, WS_A)
    store.add_artifact(
        t, kind="model", title="m", storage_type="external", artifact_id="a1",
    )  # no externalKey
    r = client.get("/api/artifacts/a1/download", headers=_headers(OWNER_A, WS_A))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# ArtifactWriteService: inline threshold respected in minio mode
# ---------------------------------------------------------------------------


def test_write_service_inlines_small_text_in_minio_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """minio 模式下，小文本仍走 inline（不再全传 MinIO）。"""
    monkeypatch.setattr(settings, "artifact_storage", "minio")
    monkeypatch.setattr(settings, "artifact_inline_max_bytes", 102400)
    with patch("app.modules.minio_client.upload_bytes") as mock_upload:
        storage, inline, external_key = artifact_write_service.decide_and_store(
            "task_x", None, "# tiny"
        )
    assert (storage, inline, external_key) == ("inline", "# tiny", None)
    mock_upload.assert_not_called()


def test_write_service_uploads_large_in_minio_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """minio 模式下，超阈值内容上传 MinIO 返回 external。"""
    monkeypatch.setattr(settings, "artifact_storage", "minio")
    monkeypatch.setattr(settings, "artifact_inline_max_bytes", 8)
    big = "x" * 1000
    with patch(
        "app.modules.minio_client.upload_bytes",
        return_value="task_x/inline-00000.md",
    ) as mock_upload:
        storage, inline, external_key = artifact_write_service.decide_and_store(
            "task_x", None, big
        )
    assert storage == "external"
    assert inline is None
    assert external_key == "task_x/inline-00000.md"
    mock_upload.assert_called_once()


# ---------------------------------------------------------------------------
# delete (Artifact-02 follow-up)
# ---------------------------------------------------------------------------


def test_delete_own_artifact(client: TestClient) -> None:
    t = _create_task(OWNER_A, WS_A)
    store.add_artifact(
        t, kind="report", title="a", storage_type="inline", inline_content="# x", artifact_id="a1",
    )
    r = client.delete("/api/artifacts/a1", headers=_headers(OWNER_A, WS_A))
    assert r.status_code == 200
    assert r.json() == {"deleted": True}
    lst = client.get("/api/artifacts", headers=_headers(OWNER_A, WS_A)).json()
    assert all(a["id"] != "a1" for a in lst["artifacts"])


def test_delete_others_artifact_forbidden(client: TestClient) -> None:
    t = _create_task(OWNER_B, WS_B)
    store.add_artifact(
        t, kind="report", title="b", storage_type="inline", inline_content="# x", artifact_id="b1",
    )
    r = client.delete("/api/artifacts/b1", headers=_headers(OWNER_A, WS_A))
    assert r.status_code == 403


def test_delete_external_cleans_minio(client: TestClient) -> None:
    t = _create_task(OWNER_A, WS_A)
    store.add_artifact(
        t, kind="model", title="m", storage_type="external", artifact_id="a1",
        extra_fields={"externalKey": "task_x/obj"},
    )
    with patch("app.modules.minio_client.remove_object") as mock_rm:
        r = client.delete("/api/artifacts/a1", headers=_headers(OWNER_A, WS_A))
    assert r.status_code == 200
    mock_rm.assert_called_once_with("task_x/obj")


# ---------------------------------------------------------------------------
# Blocker-2 回归：object key / local path 必须带 per-upload uuid（产物不可变）
# 同 task 上传同名文件两次 → 两条产物，各自独立 blob/路径，互不覆盖。
# ---------------------------------------------------------------------------


class _FakeMinioClient:
    """记录 put_object 收到的 object key，模拟 MinIO（不连真服务）。"""

    def __init__(self) -> None:
        self.keys: list[str] = []

    def put_object(self, bucket: str, key: str, stream, length: int, content_type: str | None = None, **kw) -> None:  # noqa: ANN001
        self.keys.append(key)


def test_minio_object_key_has_per_upload_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    """minio: 同 task 同名文件两次上传 → 两个不同的 {task}/{uuid}/{filename} key。"""
    from app.modules import minio_client

    fake = _FakeMinioClient()
    monkeypatch.setattr(minio_client, "get_minio_client", lambda: fake)
    k1 = minio_client.upload_bytes("taskA", "a.txt", b"AAAA")
    k2 = minio_client.upload_bytes("taskA", "a.txt", b"BBBB")
    assert k1 != k2
    for k in (k1, k2):
        parts = k.split("/")
        assert parts == ["taskA", parts[1], "a.txt"]
        assert parts[1] != "a.txt"  # 中间段是 uuid，不是裸 filename
    assert k1.split("/")[1] != k2.split("/")[1]  # 两次 uuid 不同
    assert fake.keys == [k1, k2]


def test_local_store_uploaded_bytes_path_has_uuid(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """local 回退：同名文件两次上传 → 两个不同本地路径，内容互不覆盖。"""
    monkeypatch.setattr(settings, "artifact_storage", "local")
    monkeypatch.setattr(settings, "artifact_inline_max_bytes", 4)  # 8B 数据 > 阈值 → 不 inline
    import app.execution.execution_context as ec

    monkeypatch.setattr(ec, "get_task_workspace_dir", lambda task_id: tmp_path)

    st1, _, _, path1 = artifact_write_service.store_uploaded_bytes("taskA", "a.txt", b"AAAAAAAA")
    st2, _, _, path2 = artifact_write_service.store_uploaded_bytes("taskA", "a.txt", b"BBBBBBBB")
    assert st1 == "local" and st2 == "local"
    assert path1 != path2  # uuid 前缀，不再裸 filename
    assert (tmp_path / path1).read_bytes() == b"AAAAAAAA"
    assert (tmp_path / path2).read_bytes() == b"BBBBBBBB"


def test_durable_local_storage_never_calls_minio(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(settings, "artifact_storage", "local")
    import app.execution.execution_context as ec
    from app.modules import minio_client

    monkeypatch.setattr(ec, "get_task_workspace_dir", lambda _task_id: tmp_path)
    monkeypatch.setattr(
        minio_client,
        "upload_bytes",
        lambda *_args, **_kwargs: pytest.fail("local durable storage called MinIO"),
    )
    content = b"verified durable artifact"

    stored = artifact_write_service.store_durable_bytes(
        "task-local",
        "durable/run-local/upload-local",
        content,
        "text/plain",
    )

    assert stored.storage_type == "local"
    assert stored.storage_ref == "durable/run-local/upload-local"
    assert (tmp_path / "durable" / "run-local" / "upload-local").read_bytes() == content
    assert artifact_write_service.read_durable_bytes(
        "task-local", stored.storage_type, stored.storage_ref
    ) == content


def test_durable_local_storage_encodes_nonportable_logical_segments(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(settings, "artifact_storage", "local")
    import app.execution.execution_context as ec

    monkeypatch.setattr(ec, "get_task_workspace_dir", lambda _task_id: tmp_path)
    storage_ref = "durable/research-dispatch/dispatch:abc/result.json"
    content = b"portable durable artifact"

    stored = artifact_write_service.store_durable_bytes(
        "task-local", storage_ref, content, "application/json"
    )

    assert stored.storage_ref == storage_ref
    assert (
        tmp_path / "durable" / "research-dispatch" / "dispatch%3Aabc" / "result.json"
    ).read_bytes() == content
    assert artifact_write_service.read_durable_bytes(
        "task-local", stored.storage_type, stored.storage_ref
    ) == content


def test_durable_minio_failure_is_not_silently_downgraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "artifact_storage", "minio")
    from app.modules import minio_client

    def fail_upload(*_args, **_kwargs):
        raise RuntimeError("simulated object storage rejection")

    monkeypatch.setattr(minio_client, "upload_bytes", fail_upload)

    with pytest.raises(ArtifactStorageError, match="artifact_storage_write_failed"):
        artifact_write_service.store_durable_bytes(
            "task-minio",
            "durable/run-minio/upload-minio",
            b"verified durable artifact",
            "text/plain",
        )


def test_upload_same_filename_twice_distinct_keys(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端：用户上传同名文件两次 → 两条独立产物，externalKey 不同，旧内容不被覆盖。"""
    monkeypatch.setattr(settings, "artifact_storage", "minio")
    monkeypatch.setattr(settings, "artifact_inline_max_bytes", 4)
    from app.modules import minio_client

    fake = _FakeMinioClient()
    monkeypatch.setattr(minio_client, "get_minio_client", lambda: fake)
    t = _create_task(OWNER_A, WS_A)
    r1 = client.post(
        "/api/artifacts/upload",
        files={"file": ("a.txt", b"AAAAAAAA", "text/plain")},
        data={"targetTaskId": t},
        headers=_headers(OWNER_A, WS_A),
    )
    r2 = client.post(
        "/api/artifacts/upload",
        files={"file": ("a.txt", b"BBBBBBBB", "text/plain")},
        data={"targetTaskId": t},
        headers=_headers(OWNER_A, WS_A),
    )
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] != r2.json()["id"]
    # 两次上传落到不同的 object key（不再互相覆盖）
    assert len(fake.keys) == 2 and fake.keys[0] != fake.keys[1]
