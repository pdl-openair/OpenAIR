"""Worker → API artifact ingest client (Artifact-04).

Two ways for a worker-side skill/training script to回传 产物到用户产物库：

- ``upload_artifact`` — 通道 1：presigned 两步直传（request-upload → PUT MinIO → confirm），
  worker 不持 MinIO root key。
- ``register_reference`` — 通道 2：登记外部引用（external_uri，不拷贝）。

复用 ``callback.py`` 的 httpx + ``OPENAIR_WORKER_CALLBACK_URL``/``AUTH_TOKEN`` 上下文，
并附 ``X-OpenAIR-Worker-Owner``（= ``OPENAIR_OWNER_ID``）做 owner 防御层。
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_CALLBACK_URL = os.environ.get("OPENAIR_WORKER_CALLBACK_URL", "http://host.docker.internal:8000")
_WORKER_TOKEN = os.environ.get("OPENAIR_WORKER_AUTH_TOKEN", "")
_OWNER_ID = os.environ.get("OPENAIR_OWNER_ID", "")

_TIMEOUT_SECONDS = 60


def _headers(*, json: bool = True) -> dict[str, str]:
    h: dict[str, str] = {}
    if json:
        h["Content-Type"] = "application/json"
    if _WORKER_TOKEN:
        h["X-OpenAIR-Worker-Token"] = _WORKER_TOKEN
    if _OWNER_ID:
        h["X-OpenAIR-Worker-Owner"] = _OWNER_ID
    return h


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{_CALLBACK_URL}{path}"
    with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
        resp = client.post(url, json=payload, headers=_headers())
    if resp.status_code >= 400:
        raise RuntimeError(f"ingest POST {path} → {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def upload_artifact(
    task_id: str,
    file_path: str | Path,
    *,
    kind: str = "model",
    capability_call_id: str | None = None,
    title: str | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    """通道 1：presigned 两步直传。返回登记后的 artifact dict。"""
    path = Path(file_path)
    data = path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    filename = path.name

    # 1) 申请 scoped presigned PUT（服务端 mint uuid key + 签发会话签名）
    req = _post(
        "/internal/worker/artifacts/request-upload",
        {
            "task_id": task_id,
            "capability_call_id": capability_call_id,
            "filename": filename,
            "size": len(data),
            "sha256": sha256,
            "kind": kind,
        },
    )
    upload_url = req["uploadUrl"]
    object_key = req["objectKey"]
    upload_signature = req["uploadSignature"]
    storage_scope = req["storageScope"]

    # 2) 直传 MinIO（字节不过 API）
    with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
        put_resp = client.put(upload_url, content=data)
    if put_resp.status_code >= 300:
        raise RuntimeError(f"ingest PUT {object_key} → {put_resp.status_code}: {put_resp.text[:200]}")
    logger.info("ingest: uploaded %s (%d bytes)", object_key, len(data))

    # 3) confirm → 登记（回传会话签名，size/sha256 与 request-upload 保持一致）
    return _post(
        "/internal/worker/artifacts/confirm",
        {
            "task_id": task_id,
            "capability_call_id": capability_call_id,
            "object_key": object_key,
            "storage_scope": storage_scope,
            "upload_signature": upload_signature,
            "filename": filename,
            "sha256": sha256,
            "size": len(data),
            "kind": kind,
            "title": title,
            "summary": summary,
        },
    )


def register_reference(
    task_id: str,
    external_uri: str,
    *,
    external_provider: str = "url",
    kind: str = "model",
    capability_call_id: str | None = None,
    title: str | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    """通道 2：登记外部引用（不拷贝）。返回登记后的 artifact dict。"""
    return _post(
        "/internal/worker/artifacts/reference",
        {
            "task_id": task_id,
            "capability_call_id": capability_call_id,
            "external_uri": external_uri,
            "external_provider": external_provider,
            "kind": kind,
            "title": title,
            "summary": summary,
        },
    )
