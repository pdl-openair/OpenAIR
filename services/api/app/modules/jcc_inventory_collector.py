"""云际计算平台（JCC）算力清单采集器。

移植自 2026-10-15 一次性采集脚本（.tmp/read_jcc_inventory.py +
.tmp/prepare_jcc_frontend.py），改为常驻后端能力：

- 每轮采集先 `jcc login` 换新 token（token 寿命数天，不能复用陈旧值）
- 17 项只读查询并发执行（ThreadPoolExecutor(4)，单项 25s，subprocess 35s 兜底）
- 逐项失败不整轮失败；归一化为前端 jccSnapshot.json 同构形状（合同不变）
- 内存缓存 + seed 兜底；刷新在后台 daemon 线程执行，绝不阻塞请求路径
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.modules.repo_paths import repo_root

SEED_PATH = Path(__file__).resolve().parents[1] / "data" / "jcc_inventory_seed.json"
_TRAIN_CARD_TYPES = ["GPU", "NPU", "MLU", "GCU", "DCU", "ILUVATAR-GPGPU", "METAX-GPGPU"]
_TOTAL_COLLECTION_TIMEOUT_SECONDS = 240
# 失败退避：上一轮自动补采失败后（CLI 版本不符 / 凭据失效等）短窗口内不再重试，
# 避免「过期 seed + 永败采集」组合在每个 GET 上都真实 login 一次。手动刷新不受限。
_FAILURE_BACKOFF_SECONDS = 600.0


class JccCollectionError(RuntimeError):
    """JCC 采集失败（登录失败或全部查询失败）。"""


# ---------------------------------------------------------------- CLI 基础


def _cli_path() -> Path:
    raw = os.environ.get("JCC_CLI_PATH", "").strip()
    return Path(raw) if raw else repo_root() / "skills" / "materials" / "jcc" / "jcc.exe"


def collector_available() -> dict[str, Any]:
    """采集可用性预检：CLI 是否存在、凭据是否配置。只读，不发请求。"""
    cli = _cli_path()
    username = os.environ.get("JCC_USERNAME", "").strip()
    password = os.environ.get("JCC_PASSWORD", "").strip()
    return {
        "cliPresent": cli.is_file(),
        "credentialsPresent": bool(username and password),
    }


def _decode_output(value: bytes) -> str:
    try:
        return value.decode("utf-8-sig")
    except UnicodeDecodeError:
        return value.decode("gb18030", errors="replace")


def _scrub(value: str) -> str:
    """把可能混入错误信息的凭据打码。"""
    for name in ("JCC_PASSWORD", "JCC_TOKEN"):
        secret = os.environ.get(name, "")
        if secret:
            value = value.replace(secret, "[redacted]")
    return value


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    # 本机继承的代理对 jcc.jointcloud.net 不可达，显式直连（与采集范本一致）
    no_proxy = env.get("NO_PROXY", "").strip()
    host = "jcc.jointcloud.net"
    if host not in [h.strip() for h in no_proxy.split(",") if h.strip()]:
        no_proxy = (no_proxy + "," + host) if no_proxy else host
    env["NO_PROXY"] = no_proxy
    env["no_proxy"] = no_proxy
    return env


def _invoke(cli: Path, arguments: list[str], env: dict[str, str]) -> dict[str, Any]:
    command = [str(cli), *arguments, "--output", "json", "--timeout", "25s"]
    try:
        result = subprocess.run(
            command, env=env, capture_output=True, timeout=35, check=False
        )
    except subprocess.TimeoutExpired:
        return {"exitCode": 124, "payload": None, "error": "CLI request timed out"}
    except OSError as exc:
        return {"exitCode": 125, "payload": None, "error": _scrub(str(exc))}
    try:
        payload = json.loads(_decode_output(result.stdout))
    except json.JSONDecodeError:
        payload = None
    return {
        "exitCode": result.returncode,
        "payload": payload,
        "error": _scrub(_decode_output(result.stderr))[:1000] if result.returncode else "",
    }


def _login(cli: Path, env: dict[str, str]) -> dict[str, str]:
    """每轮采集前重新登录，返回带新鲜 JCC_TOKEN/JCC_USER_ID 的 env。"""
    username = env.get("JCC_USERNAME", "").strip()
    password = env.get("JCC_PASSWORD", "").strip()
    if not username or not password:
        raise JccCollectionError("JCC credentials are not configured (JCC_USERNAME/JCC_PASSWORD)")
    result = subprocess.run(
        [str(cli), "login", "--username", username, "--password", password, "--timeout", "25s"],
        env=env,
        capture_output=True,
        timeout=35,
        check=False,
    )
    if result.returncode:
        raise JccCollectionError(
            "JCC login failed: %s" % _scrub(_decode_output(result.stderr))[:700]
        )
    try:
        login = json.loads(_decode_output(result.stdout))
        token = login["token"]
        user_id = str(login["userID"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise JccCollectionError("JCC login returned unexpected payload") from exc
    env = dict(env)
    env["JCC_TOKEN"] = token
    env["JCC_USER_ID"] = user_id
    return env


# ---------------------------------------------------------------- 采集与归一化


def _query_plan() -> dict[str, list[str]]:
    queries = {
        "trainQuota": ["aitrain", "cardtype"],
        "inferQuota": ["aiinfer", "cardtype"],
        "hpcVasp": ["hpc", "cluster-list", "--apptype", "vasp"],
        "cloudCpu": ["cloudinstance", "cluster-list", "--type", "CPU"],
        "containersCpu": ["cloudcontainer", "cluster-list", "--type", "CPU"],
        "trainTasks": ["aitrain", "query", "--page-num", "1", "--page-size", "100"],
        "inferTasks": ["aiinfer", "query", "--page-num", "1", "--page-size", "100"],
        "containers": ["cloudcontainer", "query", "--page-num", "1", "--page-size", "100"],
        "instances": ["cloudinstance", "query", "--page-num", "1", "--page-size", "100"],
        "images": ["image", "list", "--page", "1", "--size", "100"],
    }
    for resource_type in _TRAIN_CARD_TYPES:
        queries["train" + resource_type] = ["aitrain", "cluster-list", "--type", resource_type]
    return queries


def collect_raw_inventory() -> dict[str, Any]:
    """执行一轮完整采集，返回 {observedAt, queries}（原始、未归一化）。"""
    cli = _cli_path()
    if not cli.is_file():
        raise JccCollectionError("jcc CLI not found at %s" % cli)
    env = _login(cli, _subprocess_env())
    queries = _query_plan()
    observations: dict[str, Any] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_invoke, cli, arguments, env): key for key, arguments in queries.items()
        }
        for future in concurrent.futures.as_completed(futures):
            key = futures[future]
            try:
                observations[key] = future.result()
            except Exception as exc:  # noqa: BLE001 - 单项异常不应炸整轮
                observations[key] = {"exitCode": 126, "payload": None, "error": _scrub(str(exc))}
    if all(entry.get("exitCode") not in (0, None) for entry in observations.values()):
        first_error = next((e.get("error") for e in observations.values() if e.get("error")), "")
        raise JccCollectionError("all JCC queries failed: %s" % first_error[:500])
    return {
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "queries": observations,
    }


def _query_payload(queries: dict[str, Any], key: str) -> dict[str, Any]:
    entry = queries.get(key) or {}
    payload = entry.get("payload")
    return payload if isinstance(payload, dict) else {}


def normalize_inventory(raw: dict[str, Any]) -> dict[str, Any]:
    """把原始采集结果投影为前端 jccSnapshot.json 同构形状（白名单字段、逐段失败软化）。"""
    queries = raw.get("queries") or {}
    partial_errors: list[str] = []
    rows: list[dict[str, Any]] = []

    train_groups = [(f"train{t}", "train") for t in _TRAIN_CARD_TYPES]
    for key, group in train_groups + [("cloudCpu", "vm"), ("containersCpu", "container")]:
        try:
            for row in _query_payload(queries, key).get("data", {}).get("resource", []) or []:
                if not isinstance(row, dict):
                    continue
                details = [
                    {dk: spec.get(dk) for dk in ("name", "totalValue", "totalUnit")}
                    for spec in row.get("baseResourceSpecs") or []
                    if isinstance(spec, dict)
                ]
                rows.append(
                    {
                        "id": f"{group}-{row.get('id')}",
                        "specId": str(row.get("id") or ""),
                        "group": group,
                        "name": row.get("name"),
                        "type": row.get("type"),
                        "clusterId": str(row.get("clusterId") or ""),
                        "region": row.get("region") or "未取得",
                        "availableCount": row.get("availableCount"),
                        "totalCount": row.get("totalCount"),
                        "details": details,
                    }
                )
        except (AttributeError, TypeError) as exc:
            partial_errors.append(f"{key}: {exc}")

    try:
        for row in _query_payload(queries, "hpcVasp").get("data", {}).get("list", []) or []:
            if not isinstance(row, dict):
                continue
            rows.append(
                {
                    "id": f"hpc-{row.get('id')}",
                    "specId": "",
                    "group": "hpc",
                    "name": row.get("name"),
                    "type": "VASP",
                    "clusterId": str(row.get("id") or ""),
                    "region": row.get("region"),
                    "availableCount": None,
                    "totalCount": None,
                    "details": [],
                }
            )
    except (AttributeError, TypeError) as exc:
        partial_errors.append(f"hpcVasp: {exc}")

    def ranges(key: str) -> list[dict[str, Any]]:
        try:
            return [
                {rk: row.get(rk) for rk in ("type", "cpu", "gpuNumber", "memory", "storage")}
                for row in _query_payload(queries, key).get("data", {}).get("resourceRanges", []) or []
                if isinstance(row, dict)
            ]
        except (AttributeError, TypeError) as exc:
            partial_errors.append(f"{key}: {exc}")
            return []

    try:
        tasks = [
            {tk: row.get(tk) for tk in ("id", "name", "status", "createdTime", "startTime", "endTime")}
            for row in _query_payload(queries, "trainTasks").get("data", {}).get("list", []) or []
            if isinstance(row, dict)
        ]
    except (AttributeError, TypeError) as exc:
        partial_errors.append(f"trainTasks: {exc}")
        tasks = []

    try:
        images = [
            {
                ik: row.get(ik)
                for ik in ("id", "name", "arch", "computeType", "workloadKind", "availabilityStatus")
            }
            for row in _query_payload(queries, "images").get("items", []) or []
            if isinstance(row, dict)
        ]
        image_total = _query_payload(queries, "images").get("total")
    except (AttributeError, TypeError) as exc:
        partial_errors.append(f"images: {exc}")
        images, image_total = [], None

    for key, entry in queries.items():
        if isinstance(entry, dict) and entry.get("error"):
            partial_errors.append(f"{key}: {_scrub(str(entry['error']))[:200]}")

    return {
        "observedAt": raw.get("observedAt"),
        "resources": rows,
        "trainQuota": ranges("trainQuota"),
        "inferQuota": ranges("inferQuota"),
        "tasks": tasks,
        "images": images,
        "imageTotal": image_total,
        "partialErrors": dedupe(partial_errors),
    }


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


# ---------------------------------------------------------------- 缓存状态机


class _CollectorState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.payload: dict[str, Any] | None = None
        self.source: str = "seed"
        self.collecting: bool = False
        self.lastAttemptAt: str | None = None
        self.lastError: str | None = None

    def snapshot_meta(self) -> dict[str, Any]:
        with self.lock:
            return {
                "source": self.source,
                "observedAt": (self.payload or {}).get("observedAt"),
                "collecting": self.collecting,
                "lastAttemptAt": self.lastAttemptAt,
                "lastError": self.lastError,
            }


_state = _CollectorState()
_seed_loaded = False
_seed_lock = threading.Lock()


def _load_seed() -> dict[str, Any]:
    """首次访问时用 seed 初始化缓存。只补空位：若后台采集已先写入 cache
    （如 POST refresh 先于任何 GET 到达），不得用 seed 覆盖已采数据。"""
    global _seed_loaded
    with _seed_lock:
        if not _seed_loaded:
            if _state.payload is None:
                try:
                    with SEED_PATH.open("r", encoding="utf-8-sig") as fh:
                        _state.payload = json.load(fh)
                    _state.source = "seed"
                except (OSError, ValueError):
                    _state.payload = None
            _seed_loaded = True
        return _state.payload or {}


def _ttl_seconds() -> float:
    raw = os.environ.get("JCC_INVENTORY_TTL_SECONDS", "86400").strip()
    try:
        return max(float(raw), 0.0)
    except ValueError:
        return 86400.0


def _observed_age_seconds(payload: dict[str, Any]) -> float | None:
    observed = payload.get("observedAt")
    if not isinstance(observed, str) or not observed:
        return None
    try:
        observed_dt = datetime.fromisoformat(observed)
    except ValueError:
        return None
    if observed_dt.tzinfo is None:
        observed_dt = observed_dt.replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - observed_dt).total_seconds(), 0.0)


def _run_collection() -> None:
    with _state.lock:
        if _state.collecting:
            return
        _state.collecting = True
        _state.lastAttemptAt = datetime.now(timezone.utc).isoformat()
    try:
        raw = collect_raw_inventory()
        payload = normalize_inventory(raw)
        with _state.lock:
            _state.payload = payload
            _state.source = "cache"
            _state.lastError = None if not payload.get("partialErrors") else "; ".join(
                payload["partialErrors"][:3]
            )
    except (JccCollectionError, OSError) as exc:
        with _state.lock:
            _state.lastError = _scrub(str(exc))[:500]
    finally:
        with _state.lock:
            _state.collecting = False


def start_collection_in_background() -> bool:
    """触发后台采集（单飞）。返回是否真正启动了新一轮。"""
    with _state.lock:
        if _state.collecting:
            return False
    thread = threading.Thread(target=_run_collection, name="jcc-inventory-collector", daemon=True)
    thread.start()
    return True


def get_jcc_inventory() -> dict[str, Any]:
    """读取缓存清单（必要时加载 seed）；过期时触发后台补采（stale-while-revalidate）。"""
    _load_seed()
    maybe_refresh_if_stale()
    with _state.lock:
        payload = _state.payload
        meta = {
            "source": _state.source,
            "observedAt": (payload or {}).get("observedAt"),
            "collecting": _state.collecting,
            "lastAttemptAt": _state.lastAttemptAt,
            "lastError": _state.lastError,
        }
    return {"meta": meta, "payload": payload}


def maybe_refresh_if_stale() -> bool:
    payload = _state.payload
    if payload is None:
        return False
    availability = collector_available()
    if not (availability["cliPresent"] and availability["credentialsPresent"]):
        return False
    age = _observed_age_seconds(payload)
    if age is None or age <= _ttl_seconds():
        return False
    if _state.lastError and _state.lastAttemptAt:
        try:
            since_attempt = (
                datetime.now(timezone.utc) - datetime.fromisoformat(_state.lastAttemptAt)
            ).total_seconds()
        except ValueError:
            since_attempt = _FAILURE_BACKOFF_SECONDS + 1.0
        if since_attempt < _FAILURE_BACKOFF_SECONDS:
            return False
    return start_collection_in_background()


def reset_collector_state_for_tests(payload: dict[str, Any] | None = None, source: str = "seed") -> None:
    """测试辅助：清空内存态，可选注入初始 payload。"""
    global _seed_loaded
    with _state.lock:
        _state.payload = payload
        _state.source = source
        _state.collecting = False
        _state.lastAttemptAt = None
        _state.lastError = None
    with _seed_lock:
        _seed_loaded = payload is not None
