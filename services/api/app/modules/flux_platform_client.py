"""云实验平台（flux / CrystalFlux）open API 只读客户端。

数据范围：实验模板目录、模板参数 schema、PVD 工艺子配方字典（2026-10 实测的三个
已验证 GET 端点）。仅做白名单字段投影，不透传平台原始响应。

配置解析顺序与 skill 侧 `skills/materials/flux-experiment/scripts/flux_api.py` 保持一致：
环境变量 FLUX_BASE_URL / FLUX_API_KEY / FLUX_TIMEOUT 优先，回落 skill 的
config.local.json（本地开发便利；容器内不存在则纯环境变量）。
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.modules.repo_paths import repo_root

DEFAULT_BASE_URL = "http://<flux-base-url>"
SUCCESS_CODES = {200, 2000, "200", "2000"}
_PARAM_DEFAULT_VALUE_LIMIT = 32_768

# 短 TTL 内存缓存：页面在总览/详情间来回切换时避免每次都打平台 7 个请求。
_CACHE_TTL_SECONDS = 60.0


class FluxPlatformError(RuntimeError):
    """flux 平台不可达或返回异常（前缀可识别，路由层转 503）。"""


def _skill_config_path() -> Path:
    return repo_root() / "skills" / "materials" / "flux-experiment" / "config.local.json"


def _load_skill_config() -> dict[str, Any]:
    try:
        with _skill_config_path().open("r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def _base_url() -> str:
    raw = os.environ.get("FLUX_BASE_URL") or _load_skill_config().get("baseUrl") or DEFAULT_BASE_URL
    return str(raw).rstrip("/")


def _api_key() -> str:
    return str(os.environ.get("FLUX_API_KEY") or _load_skill_config().get("apiKey") or "")


def _timeout_seconds() -> float:
    raw = os.environ.get("FLUX_TIMEOUT") or _load_skill_config().get("timeout") or 10
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 10.0
    return max(value, 1.0)


def _http_json(path: str, *, attempts: int = 2) -> Any:
    """GET 一个 flux open api 端点，返回解析后的 JSON；失败抛 FluxPlatformError。

    平台偶发慢响应（实测单请求偶超 10s），对网络类失败做一次有界重试。
    """
    url = _base_url() + path
    key = _api_key()
    last_network_error: Exception | None = None
    for attempt in range(max(attempts, 1)):
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        if key:
            req.add_header("X-Api-Key", key)
        try:
            with urllib.request.urlopen(req, timeout=_timeout_seconds()) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                status = resp.status
        except urllib.error.HTTPError as exc:
            raise FluxPlatformError("flux platform http error: %s %s" % (exc.code, path)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_network_error = exc
            continue
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except ValueError as exc:
            raise FluxPlatformError("flux platform returned non-json payload for %s" % path) from exc
        if not isinstance(parsed, dict):
            raise FluxPlatformError("flux platform returned unexpected payload shape for %s" % path)
        code = parsed.get("code")
        if status >= 400 or (code is not None and code not in SUCCESS_CODES and status != 200):
            raise FluxPlatformError(
                "flux platform rejected %s: http=%s code=%s msg=%s"
                % (path, status, code, parsed.get("msg"))
            )
        return parsed.get("data")
    raise FluxPlatformError("flux platform unreachable: %s" % last_network_error)


def _clip(value: Any, limit: int = _PARAM_DEFAULT_VALUE_LIMIT) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…（超长截断）"
    return value


def _project_template(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    return {
        "id": str(row.get("id") or ""),
        "templateName": row.get("templateName"),
        "taskType": row.get("taskType"),
        "labId": str(row.get("labId") or "") if row.get("labId") is not None else None,
        "labName": row.get("labName"),
        "experimentDesc": row.get("experimentDesc"),
        "estimateDays": row.get("estimateDays"),
        "priority": row.get("priority"),
    }


def _project_param(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None

    def project(node: Any) -> dict[str, Any] | None:
        if not isinstance(node, dict):
            return None
        children = [c for c in (project(x) for x in node.get("children") or []) if c]
        return {
            "paramName": node.get("paramName"),
            "displayName": node.get("displayName"),
            "dataType": node.get("dataType"),
            "isRequired": node.get("isRequired"),
            "paramOrder": node.get("paramOrder"),
            "valueSource": node.get("valueSource"),
            "jsonTemplate": _clip(node.get("jsonTemplate")),
            "defaultValue": _clip(node.get("defaultValue")),
            "children": children,
        }

    return project(row)


def _project_sub_recipe(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    return {
        "id": row.get("id"),
        "subRecipeTypeId": row.get("subRecipeTypeId"),
        "subRecipeId": row.get("subRecipeId"),
        "subRecipeName": row.get("subRecipeName"),
        "groupName": row.get("groupName"),
        "enabled": row.get("enabled"),
        "sortOrder": row.get("sortOrder"),
        "remark": row.get("remark"),
    }


def _collect_uncached() -> dict[str, Any]:
    templates_raw = _http_json("/open/api/v1/templates")
    templates = [t for t in (_project_template(r) for r in templates_raw or []) if t]
    if not templates:
        raise FluxPlatformError("flux platform returned no templates (账号可见范围为空或鉴权失效)")

    template_ids = [t["id"] for t in templates if t["id"]]

    def fetch_params(template_id: str) -> tuple[str, list[dict[str, Any]]]:
        params_raw = _http_json("/open/api/v1/templates/%s/params" % template_id)
        params = [p for p in (_project_param(r) for r in params_raw or []) if p]
        return template_id, params

    template_params: dict[str, list[dict[str, Any]]] = {}
    sub_recipes: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(fetch_params, tid) for tid in template_ids]
        sub_recipe_future = executor.submit(_http_json, "/open/api/v1/pvd/dict/subRecipe")
        for future in futures:
            try:
                tid, params = future.result()
                template_params[tid] = params
            except FluxPlatformError as exc:
                errors.append(str(exc))
        try:
            sub_recipes = [r for r in (_project_sub_recipe(x) for x in sub_recipe_future.result() or []) if r]
        except FluxPlatformError as exc:
            errors.append(str(exc))

    return {
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "templates": templates,
        "templateParams": template_params,
        "pvdSubRecipes": sub_recipes,
        "partialErrors": errors,
    }


_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"payload": None, "fetchedAt": 0.0}


def fetch_flux_inventory(*, force_refresh: bool = False) -> dict[str, Any]:
    """拉取 flux 平台清单（白名单投影）。带 60s 内存缓存，避免导航抖动反复打平台。"""
    if not force_refresh:
        with _cache_lock:
            payload = _cache["payload"]
            if payload is not None and (time.monotonic() - _cache["fetchedAt"]) < _CACHE_TTL_SECONDS:
                return payload
    payload = _collect_uncached()
    with _cache_lock:
        _cache["payload"] = payload
        _cache["fetchedAt"] = time.monotonic()
    return payload


def clear_flux_inventory_cache() -> None:
    with _cache_lock:
        _cache["payload"] = None
        _cache["fetchedAt"] = 0.0
