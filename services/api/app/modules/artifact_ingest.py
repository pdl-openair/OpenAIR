"""Artifact-04 通道3：异步回传 webhook 的 HMAC 签发/校验 + 异步 job 创建 helper。

外部训练服务完成时回调 ``POST /api/artifacts/ingest/callback``，带 ``signature``。
签名 = HMAC-SHA256(secret, "{task_id}|{capability_call_id}")，绑定回调到具体 job。
未配 ``OPENAIR_INGEST_CALLBACK_SECRET`` 时回退 ``worker_auth_token``（dev 可跑）。
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from app.config import settings


def _secret() -> str:
    return settings.ingest_callback_secret or settings.worker_auth_token or "openair-dev"


def sign_callback(task_id: str, capability_call_id: str) -> str:
    msg = f"{task_id}|{capability_call_id}".encode("utf-8")
    return hmac.new(_secret().encode("utf-8"), msg, hashlib.sha256).hexdigest()


def verify_callback(task_id: str, capability_call_id: str, signature: str | None) -> bool:
    if not signature:
        return False
    return hmac.compare_digest(sign_callback(task_id, capability_call_id), signature)


# ---------------------------------------------------------------------------
# Channel-1 presigned two-step: server-issued upload session (Blocker-1 fix)
# ---------------------------------------------------------------------------

def sign_upload(
    task_id: str,
    owner_id: str,
    capability_call_id: str | None,
    object_key: str,
    size: int | None,
    sha256: str | None,
    storage_scope: str | None = None,
) -> str:
    """HMAC over the upload session, binding it to a single request-upload.

    ``confirm`` must replay these exact fields. Crucially the signature is over a
    *server-minted* ``object_key`` (``{task}/{uuid}/{filename}``) — so a worker with a
    valid token+owner cannot swap in another task's object key
    (e.g. ``other-task/secret.bin``) and register it as this task's artifact.
    """
    msg = "|".join(
        [
            "upload",
            str(task_id),
            str(owner_id),
            capability_call_id or "",
            storage_scope or "",
            object_key,
            "" if size is None else str(size),
            sha256 or "",
        ]
    ).encode("utf-8")
    return hmac.new(_secret().encode("utf-8"), msg, hashlib.sha256).hexdigest()


def verify_upload(
    task_id: str,
    owner_id: str,
    capability_call_id: str | None,
    object_key: str,
    size: int | None,
    sha256: str | None,
    storage_scope: str | None,
    signature: str | None,
) -> bool:
    if not signature:
        return False
    expected = sign_upload(
        task_id, owner_id, capability_call_id, object_key, size, sha256, storage_scope
    )
    return hmac.compare_digest(expected, signature)


def create_async_ingest_job(task_id: str, *, skill_id: str) -> dict[str, Any]:
    """创建 running CapabilityCall 并返回回调所需信息（供外部训练服务回调）。

    提交 UI/机制作为 follow-up；本 helper 提供通道3 的「发牌」端。
    """
    from app.modules.task_store import store

    call = store.add_capability_call(task_id, skill_id=skill_id, status="running", runtime="async_training")
    if not call:
        raise RuntimeError(f"cannot create capability call on task {task_id}")
    return {
        "capabilityCallId": call["id"],
        "callbackUrl": "/api/artifacts/ingest/callback",
        "signature": sign_callback(task_id, call["id"]),
    }
