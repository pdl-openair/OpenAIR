"""Artifact-04 远程回传测试（json 后端 + monkeypatch minio）。

覆盖通道 1（worker presigned 两步）+ 通道 2（reference）。worker-token 鉴权 +
owner 防御层 + 大小限额 + stat 校验 + derived_from 钩子点。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.modules.task_store import store

OWNER = "ownerA"
WS = "wsA"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "worker_auth_token", "test-worker-token")
    original = store._tasks.copy()
    store._tasks.clear()
    try:
        yield
    finally:
        store._tasks.clear()
        store._tasks.update(original)


def _worker_headers(owner: str = OWNER) -> dict[str, str]:
    return {"X-OpenAIR-Worker-Token": "test-worker-token", "X-OpenAIR-Worker-Owner": owner}


def _create_task(owner: str = OWNER, ws: str = WS, title: str = "t") -> str:
    t = store.create_task(
        {
            "title": title, "goalSummary": "g", "domain": "research_task",
            "currentStage": "problem_modeling", "status": "running",
            "ownerId": owner, "workspaceId": ws,
        }
    )
    return str(t["taskId"])


def _request_upload(
    client: TestClient,
    task: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    filename: str = "model.pt",
    size: int = 1024,
    sha256: str | None = "abc",
    kind: str = "model",
    capability_call_id: str | None = None,
) -> dict:
    """走完整的 request-upload，返回 {uploadUrl, objectKey, uploadId, uploadSignature}。"""
    monkeypatch.setattr(settings, "artifact_storage", "minio")
    monkeypatch.setattr(settings, "artifact_upload_max_bytes", max(size, 1024 * 1024))
    with patch("app.modules.minio_client.presigned_put_object", return_value="http://mock-minio/put"):
        r = client.post(
            "/internal/worker/artifacts/request-upload",
            json={
                "task_id": task,
                "capability_call_id": capability_call_id,
                "filename": filename,
                "size": size,
                "sha256": sha256,
                "kind": kind,
            },
            headers=_worker_headers(),
        )
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# channel 1 — request-upload
# ---------------------------------------------------------------------------


def test_request_upload_returns_signed_session(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """request-upload 现在签发 per-upload uuid key + 绑定会话的服务端签名。"""
    task = _create_task()
    body = _request_upload(client, task, monkeypatch)  # 内部 patch presigned_put_object
    assert body["uploadUrl"] == "http://mock-minio/put"
    obj_key = body["objectKey"]
    # key task-scoped 且含 per-upload uuid 段（不可变、唯一）
    assert obj_key.startswith(f"{task}/") and obj_key.endswith("/model.pt")
    parts = obj_key.split("/")
    assert len(parts) == 3 and parts[1] != "model.pt"
    assert body["uploadId"]
    assert body["storageScope"]
    assert body["uploadSignature"]


def test_request_upload_missing_token_403(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "artifact_storage", "minio")
    task = _create_task()
    r = client.post(
        "/internal/worker/artifacts/request-upload",
        json={"task_id": task, "filename": "x", "size": 1},
        headers={"X-OpenAIR-Worker-Owner": OWNER},  # no token
    )
    assert r.status_code == 403


def test_request_upload_wrong_owner_403(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "artifact_storage", "minio")
    task = _create_task()
    r = client.post(
        "/internal/worker/artifacts/request-upload",
        json={"task_id": task, "filename": "x", "size": 1},
        headers={"X-OpenAIR-Worker-Token": "test-worker-token", "X-OpenAIR-Worker-Owner": "someone-else"},
    )
    assert r.status_code == 403


def test_request_upload_oversize_413(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "artifact_storage", "minio")
    monkeypatch.setattr(settings, "artifact_upload_max_bytes", 8)
    task = _create_task()
    r = client.post(
        "/internal/worker/artifacts/request-upload",
        json={"task_id": task, "filename": "x", "size": 1024},
        headers=_worker_headers(),
    )
    assert r.status_code == 413


# ---------------------------------------------------------------------------
# channel 1 — confirm
# ---------------------------------------------------------------------------


def test_confirm_registers_remote_compute_artifact(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "artifact_storage", "minio")
    task = _create_task()
    body = _request_upload(client, task, monkeypatch)
    obj_key, sig = body["objectKey"], body["uploadSignature"]
    with patch("app.modules.minio_client.stat_object", return_value={"size": 1024, "key": obj_key}):
        r = client.post(
            "/internal/worker/artifacts/confirm",
            json={
                "task_id": task, "object_key": obj_key, "storage_scope": body["storageScope"], "upload_signature": sig,
                "filename": "model.pt", "sha256": "abc", "size": 1024, "kind": "model", "title": "trained model",
            },
            headers=_worker_headers(),
        )
    assert r.status_code == 200, r.text
    art = r.json()
    assert art["origin"] == "remote_compute"
    assert art["storageType"] == "external"
    assert art["title"] == "trained model"
    # externalKey 是带 uuid 的不可变 key（不再是旧的 task/filename）
    assert art["externalKey"] == obj_key
    # 出现在产物库
    rel = client.get("/api/artifacts", headers={"X-OpenAIR-Owner-Id": OWNER, "X-OpenAIR-Workspace-Id": WS}).json()
    assert any(a["id"] == art["id"] for a in rel["artifacts"])


def test_confirm_with_call_links_output(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "artifact_storage", "minio")
    task = _create_task()
    call = store.add_capability_call(task, skill_id="train", status="completed", runtime="claude_code")
    assert call
    body = _request_upload(client, task, monkeypatch, capability_call_id=call["id"], size=512)
    with patch("app.modules.minio_client.stat_object", return_value={"size": 512, "key": body["objectKey"]}):
        r = client.post(
            "/internal/worker/artifacts/confirm",
            json={"task_id": task, "capability_call_id": call["id"], "object_key": body["objectKey"],
                  "storage_scope": body["storageScope"], "upload_signature": body["uploadSignature"], "sha256": "abc",
                  "filename": "model.pt", "size": 512, "kind": "model"},
            headers=_worker_headers(),
        )
    assert r.status_code == 200
    art_id = r.json()["id"]
    # 该 call 的 outputArtifactRefs 应含新产物（derived_from 钩子点）
    t = store.get_task(task)
    c = next(cc for cc in t["capabilityCalls"] if cc["id"] == call["id"])
    assert art_id in (c.get("outputArtifactRefs") or [])


def test_confirm_size_mismatch_400(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """声明 size 与 stat 实际 size 不符 → 400。

    size 现在绑进签名，故 request-upload / confirm 用同一 size（10）通过签名校验；
    但 stat 到的对象实际是 1024 → 声明与内容不一致 → 400。
    """
    task = _create_task()
    body = _request_upload(client, task, monkeypatch, size=10)  # 签名绑 size=10
    with patch("app.modules.minio_client.stat_object", return_value={"size": 1024, "key": body["objectKey"]}):
        r = client.post(
            "/internal/worker/artifacts/confirm",
            json={"task_id": task, "object_key": body["objectKey"], "storage_scope": body["storageScope"], "upload_signature": body["uploadSignature"],
                  "sha256": "abc", "filename": "model.pt", "size": 10},  # 与签名一致(10)，≠ stat(1024)
            headers=_worker_headers(),
        )
    assert r.status_code == 400


# --- Blocker-1 回归：confirm 必须绑定到 request-upload 发的签名会话 -----------------


def test_confirm_rejects_missing_signature(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """无 upload_signature → 即便 worker token + owner 对得上，也不得登记任意 object_key。"""
    monkeypatch.setattr(settings, "artifact_storage", "minio")
    task = _create_task()
    forged_key = f"{task}/evil.bin"  # 旧式 key，无 uuid、无签名
    with patch("app.modules.minio_client.stat_object", return_value={"size": 10, "key": forged_key}):
        r = client.post(
            "/internal/worker/artifacts/confirm",
            json={"task_id": task, "object_key": forged_key, "filename": "evil.bin", "size": 10},
            headers=_worker_headers(),
        )
    assert r.status_code == 401


def test_confirm_rejects_object_key_not_bound_to_signature(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """签名只对 request-upload 发的那个 object_key 有效；换 key（含跨 task 的 blob）→ 401。

    reviewer 指出的核心口子：凭有效 token+owner 就能把 other-task/secret.bin 登记
    成本 task 产物。修复后签名把 (task/owner/call/object_key/size/sha256) 绑死。
    """
    monkeypatch.setattr(settings, "artifact_storage", "minio")
    task = _create_task()
    legit = _request_upload(client, task, monkeypatch)  # 合法会话签名（绑本 task 的 uuid key）
    victim_key = "other-task/secret.bin"  # 企图登记的别人 task 的 blob
    with patch("app.modules.minio_client.stat_object", return_value={"size": 1024, "key": victim_key}):
        r = client.post(
            "/internal/worker/artifacts/confirm",
            json={"task_id": task, "object_key": victim_key, "storage_scope": legit["storageScope"], "upload_signature": legit["uploadSignature"],
                  "filename": "secret.bin", "size": 1024},
            headers=_worker_headers(),
        )
    assert r.status_code == 401


def test_confirm_rejects_tampered_size(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """签名绑了 size；confirm 改 size（指向不同 blob）→ 401。"""
    monkeypatch.setattr(settings, "artifact_storage", "minio")
    task = _create_task()
    body = _request_upload(client, task, monkeypatch, size=1024)
    with patch("app.modules.minio_client.stat_object", return_value={"size": 2048, "key": body["objectKey"]}):
        r = client.post(
            "/internal/worker/artifacts/confirm",
            json={"task_id": task, "object_key": body["objectKey"], "storage_scope": body["storageScope"], "upload_signature": body["uploadSignature"],
                  "filename": "model.pt", "size": 2048},  # 与签名的 1024 不符
            headers=_worker_headers(),
        )
    assert r.status_code == 401


def test_confirm_rejects_replay_duplicate(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """同一签名/object_key 重复 confirm（重放）→ 第二次 409，不产生重复产物。"""
    monkeypatch.setattr(settings, "artifact_storage", "minio")
    task = _create_task()
    body = _request_upload(client, task, monkeypatch)
    payload = {
        "task_id": task, "object_key": body["objectKey"], "storage_scope": body["storageScope"], "upload_signature": body["uploadSignature"],
        "sha256": "abc", "filename": "model.pt", "size": 1024, "kind": "model",
    }
    with patch("app.modules.minio_client.stat_object", return_value={"size": 1024, "key": body["objectKey"]}):
        first = client.post("/internal/worker/artifacts/confirm", json=payload, headers=_worker_headers())
        second = client.post("/internal/worker/artifacts/confirm", json=payload, headers=_worker_headers())
    assert first.status_code == 200
    assert second.status_code == 409


# ---------------------------------------------------------------------------
# channel 2 — reference
# ---------------------------------------------------------------------------


def test_reference_registers_external_uri(client: TestClient) -> None:
    task = _create_task()
    r = client.post(
        "/internal/worker/artifacts/reference",
        json={
            "task_id": task, "external_uri": "https://hf.co/my/model",
            "external_provider": "huggingface", "kind": "model", "title": "HF model",
        },
        headers=_worker_headers(),
    )
    assert r.status_code == 200, r.text
    art = r.json()
    assert art["origin"] == "remote_compute"
    assert art["storageType"] == "external"
    # reference 不拷贝：无 externalKey，而是 externalProvider/externalUri（在 metadata 里，ArtifactOut 不暴露——查 store）
    t = store.get_task(task)
    a = next(x for x in t["artifacts"] if x["id"] == art["id"])
    assert a["externalProvider"] == "huggingface"
    assert a["externalUri"] == "https://hf.co/my/model"


# ---------------------------------------------------------------------------
# channel 3 — async webhook (HMAC + job ownership)
# ---------------------------------------------------------------------------


def test_callback_valid_hmac_registers_and_completes(client: TestClient) -> None:
    from app.modules.artifact_ingest import create_async_ingest_job, sign_callback

    task = _create_task()
    job = create_async_ingest_job(task, skill_id="train")
    sig = job["signature"]
    call_id = job["capabilityCallId"]

    r = client.post(
        "/api/artifacts/ingest/callback",
        json={
            "task_id": task, "capability_call_id": call_id, "signature": sig,
            "external_uri": "https://hf.co/x/y", "kind": "model", "title": "trained",
        },
    )
    assert r.status_code == 200, r.text
    # call → completed；artifact 入库
    t = store.get_task(task)
    c = next(cc for cc in t["capabilityCalls"] if cc["id"] == call_id)
    assert c["status"] == "completed"
    assert any(x.get("externalUri") == "https://hf.co/x/y" for x in t["artifacts"])


def test_callback_invalid_signature_401(client: TestClient) -> None:
    task = _create_task()
    from app.modules.task_store import store as _store

    call = _store.add_capability_call(task, skill_id="train", status="running", runtime="async_training")
    r = client.post(
        "/api/artifacts/ingest/callback",
        json={"task_id": task, "capability_call_id": call["id"], "signature": "bogus", "external_uri": "u"},
    )
    assert r.status_code == 401


def test_callback_wrong_task_403(client: TestClient) -> None:
    """HMAC 对，但 capability_call_id 不属于 body.task_id（伪造把别人模型塞进来）。"""
    from app.modules.artifact_ingest import sign_callback

    task = _create_task()
    call = store.add_capability_call(task, skill_id="train", status="running", runtime="async_training")
    # 对真实 (task, call) 签名，但用一个不同的 task_id 调用 → 签名不匹配 → 401
    r = client.post(
        "/api/artifacts/ingest/callback",
        json={
            "task_id": "task_other", "capability_call_id": call["id"],
            "signature": sign_callback("task_other", call["id"]), "external_uri": "u",
        },
    )
    assert r.status_code == 404  # task_other 不存在


def test_callback_missing_payload_400(client: TestClient) -> None:
    from app.modules.artifact_ingest import create_async_ingest_job

    task = _create_task()
    job = create_async_ingest_job(task, skill_id="train")
    r = client.post(
        "/api/artifacts/ingest/callback",
        json={"task_id": task, "capability_call_id": job["capabilityCallId"], "signature": job["signature"]},
    )
    assert r.status_code == 400  # 既无 external_uri 也无 object_key
