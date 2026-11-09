"""Runtime LLM gateway settings: an admin-editable overlay on process env.

Scope: the planner / structured-skill OpenAI-compatible gateway and the
Anthropic-protocol skill gateway. Values persist to ``data/llm_settings.json``
and are applied to ``os.environ`` at boot and after each admin save, so
API-side runtimes that read env per call (planner, structured runners, inline
skill execution) pick them up without a container restart. The durable worker
is a separate process — gateway changes there still require a worker restart;
callers must surface that caveat in the admin UI.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from app.modules.repo_paths import repo_root

logger = logging.getLogger(__name__)

_SECRET_FIELDS = ("anthropicApiKey", "anthropicAuthToken", "providerApiKey")

# field name -> process env var applied on save/boot
_FIELD_ENV = {
    "anthropicBaseUrl": "ANTHROPIC_BASE_URL",
    "anthropicApiKey": "ANTHROPIC_API_KEY",
    "anthropicAuthToken": "ANTHROPIC_AUTH_TOKEN",
    "claudeModel": "OPENAIR_CLAUDE_MODEL",
    "providerBaseUrl": "LLM_PROVIDER_BASE_URL",
    "providerApiKey": "LLM_PROVIDER_API_KEY",
    "providerModel": "LLM_PROVIDER_MODEL",
    "plannerModel": "OPENAIR_RESEARCH_START_PLANNER_LLM_MODEL",
}

_ALL_FIELDS = frozenset(_FIELD_ENV)


def settings_path() -> Path:
    return repo_root() / "data" / "llm_settings.json"


def load_overlay() -> dict[str, str]:
    """Read persisted values; corrupt files degrade to no overlay."""
    path = settings_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("llm settings overlay unreadable: %s", path)
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): str(value).strip()
        for key, value in payload.items()
        if key in _ALL_FIELDS and isinstance(value, str) and value.strip()
    }


def apply_to_environ(environ: dict[str, str] | None = None) -> list[str]:
    """Overlay persisted values onto the process env; returns applied vars."""
    target = os.environ if environ is None else environ
    applied: list[str] = []
    for field, value in load_overlay().items():
        target[_FIELD_ENV[field]] = value
        applied.append(_FIELD_ENV[field])
    if applied:
        logger.info("llm settings overlay applied: %s", ", ".join(sorted(applied)))
    return applied


def masked_view() -> dict[str, Any]:
    """Admin-safe view: plain non-secrets, secrets reduced to a preview."""
    overlay = load_overlay()

    def effective_env(field: str) -> str:
        env_key = _FIELD_ENV[field]
        return overlay.get(field) or os.environ.get(env_key, "")

    def secret_preview(value: str) -> dict[str, Any]:
        if not value:
            return {"configured": False, "preview": ""}
        tail = value[-4:] if len(value) > 8 else ""
        return {"configured": True, "preview": f"••••{tail}" if tail else "••••"}

    view: dict[str, Any] = {"workerRestartRequired": True}
    for field in sorted(_ALL_FIELDS):
        if field in _SECRET_FIELDS:
            view[field] = secret_preview(effective_env(field))
        else:
            view[field] = effective_env(field)
    return view


def _validate(values: Mapping[str, str]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for field, value in values.items():
        if field not in _ALL_FIELDS:
            continue
        text = str(value).strip()
        if field.endswith("BaseUrl") and text:
            parsed = urlsplit(text if "://" in text else f"https://{text}")
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"invalid base URL for {field}: {text}")
        cleaned[field] = text
    return cleaned


def save(values: Mapping[str, Any]) -> dict[str, str]:
    """Merge, validate and persist the overlay, then apply it to this process.

    Blank string clears a field (falling back to env/compose defaults).
    """
    if not isinstance(values, Mapping):
        raise ValueError("settings payload must be an object")
    normalized = {str(key): str(value) for key, value in values.items()}
    cleaned = _validate(normalized)
    current = load_overlay()
    changed: dict[str, str] = {}
    for field in _ALL_FIELDS:
        new_value = cleaned.get(field)
        if new_value is None:
            continue
        if new_value == "":
            if field in current:
                del current[field]
                changed[field] = ""
        elif current.get(field) != new_value:
            current[field] = new_value
            changed[field] = new_value
    if not changed:
        return {}
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if current:
        path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif path.is_file():
        path.unlink()
    apply_to_environ()
    return changed


def _probe_provider_gateway(env: Mapping[str, str]) -> dict[str, Any]:
    """Probe the OpenAI-compatible gateway: model list + planner model presence."""
    from app.modules.provider_registry import (
        ProviderRegistryError,
        discover_provider_models,
        load_provider_registry,
        resolve_provider_runtime_config,
    )

    result: dict[str, Any] = {
        "role": "provider",
        "configured": bool(env.get("LLM_PROVIDER_BASE_URL") and env.get("LLM_PROVIDER_API_KEY")),
    }
    try:
        registry = load_provider_registry()
        config = resolve_provider_runtime_config(
            registry.defaultProviderId, environ=env, registry=registry
        )
    except ProviderRegistryError as exc:
        return {**result, "ok": False, "status": "配置不完整", "detail": str(exc)}
    try:
        models = discover_provider_models(config)
    except ProviderRegistryError as exc:
        return {**result, "ok": False, "status": "连接失败", "detail": str(exc)}
    planner_model = (
        env.get("OPENAIR_RESEARCH_START_PLANNER_LLM_MODEL")
        or env.get("LLM_PROVIDER_MODEL")
        or ""
    ).strip()
    detail = f"发现 {len(models)} 个模型"
    model_available: bool | None = None
    if planner_model:
        model_available = planner_model in models
        detail += f"；规划模型 {planner_model} " + ("在列表中" if model_available else "不在列表中")
    return {
        **result,
        "ok": True,
        "status": "连接正常",
        "modelsFound": len(models),
        "modelAvailable": model_available,
        "detail": detail,
    }


def _probe_anthropic_gateway(env: Mapping[str, str]) -> dict[str, Any]:
    """Probe the Anthropic-protocol gateway with a 1-token messages request."""
    import httpx

    base = (env.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
    api_key = env.get("ANTHROPIC_API_KEY") or ""
    auth_token = env.get("ANTHROPIC_AUTH_TOKEN") or ""
    model = env.get("OPENAIR_CLAUDE_MODEL") or ""
    result: dict[str, Any] = {
        "role": "anthropic",
        "configured": bool(base and (api_key or auth_token)),
    }
    if not result["configured"]:
        return {**result, "ok": False, "status": "未配置", "detail": "缺少 base URL 或密钥"}
    headers = {"anthropic-version": "2023-06-01", "content-type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    payload = {"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "OK"}]}
    try:
        response = httpx.post(
            f"{base}/v1/messages",
            headers=headers,
            json=payload,
            timeout=20.0,
        )
    except httpx.TimeoutException:
        return {**result, "ok": False, "status": "连接超时", "detail": "网关在 20 秒内未响应"}
    except httpx.HTTPError as exc:
        return {**result, "ok": False, "status": "连接失败", "detail": f"传输错误：{exc.__class__.__name__}"}
    if response.status_code in (200, 400):
        # 400 typically means the gateway understood us but rejected the tiny
        # probe payload (e.g. unknown model) — auth and routing already work.
        return {**result, "ok": True, "status": "连接正常", "detail": f"HTTP {response.status_code}"}
    if response.status_code in (401, 403):
        return {**result, "ok": False, "status": "认证失败", "detail": "密钥无效或无权限"}
    if response.status_code == 404:
        return {**result, "ok": False, "status": "端点不存在", "detail": "检查 base URL 是否为 Anthropic 兼容路径"}
    return {
        **result,
        "ok": False,
        "status": "网关错误",
        "detail": f"HTTP {response.status_code}",
    }


def test_connection(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Probe both gateways using env + persisted overlay + caller overrides."""
    env = dict(os.environ)
    for field, value in load_overlay().items():
        env[_FIELD_ENV[field]] = value
    for field, value in (overrides or {}).items():
        if field in _ALL_FIELDS:
            text = str(value).strip()
            if text:
                env[_FIELD_ENV[field]] = text
    return {
        "provider": _probe_provider_gateway(env),
        "anthropic": _probe_anthropic_gateway(env),
    }
