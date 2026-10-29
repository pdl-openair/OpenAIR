"""Runtime configuration (env vars). No fixture paths in defaults."""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from typing import Mapping
from urllib.parse import unquote, urlparse

from app.modules.runtime_source_version import runtime_source_version

# 加载仓库根目录 .env（uvicorn 从 services/api 启动时不会自动读取）
# Worker 容器内路径层级不同，需要安全回退
try:
    from dotenv import load_dotenv

    _config_file = Path(__file__).resolve()
    _repo_env_candidates = [
        # API server: services/api/app/config.py → parents[3]
        _config_file.parents[3] / ".env" if len(_config_file.parents) > 3 else None,
        # Worker container: /app/app/config.py → parents[1] (repo root = /app)
        _config_file.parents[1] / ".env",
    ]
    for _repo_env in _repo_env_candidates:
        if _repo_env is not None and _repo_env.is_file():
            load_dotenv(_repo_env, override=False)
            break
except ImportError:
    pass


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _captcha_enabled() -> bool:
    """Image captcha for public registration.

    ``OPENAIR_CAPTCHA_ENABLED`` overrides. If unset, production is on and
    other environments stay off so local tests / harness keep working.
    """
    raw = os.environ.get("OPENAIR_CAPTCHA_ENABLED")
    if raw is not None and raw.strip() != "":
        return raw.strip().lower() in ("1", "true", "yes", "on")
    env = (os.environ.get("OPENAIR_ENV") or "development").strip().lower()
    return env == "production"


def _postgres_connection_config(environ: Mapping[str, str] | None = None) -> tuple[str, int, str, str, str]:
    """Resolve PostgreSQL settings with explicit variables taking precedence.

    Zeabur exposes a standard ``DATABASE_URL`` for its managed PostgreSQL
    service.  Older OpenAIR deployment templates used that value even though
    repositories consume the individual ``POSTGRES_*`` settings.  Supporting
    it here keeps existing Zeabur deployments usable while retaining explicit
    values as the authoritative configuration.
    """
    values = environ if environ is not None else os.environ
    parsed = urlparse(str(values.get("DATABASE_URL") or ""))
    if not parsed.scheme.startswith("postgres"):
        parsed = urlparse("")

    host = str(values.get("POSTGRES_HOST") or parsed.hostname or "localhost").strip() or "localhost"
    raw_port = str(values.get("POSTGRES_PORT") or "").strip()
    if raw_port:
        port = int(raw_port)
    else:
        port = parsed.port or 5432
    user = str(
        values.get("POSTGRES_USER")
        or values.get("POSTGRES_USERNAME")
        or parsed.username
        or "root"
    ).strip() or "root"
    password = str(values.get("POSTGRES_PASSWORD") or (unquote(parsed.password) if parsed.password else "password"))
    database = str(values.get("POSTGRES_DATABASE") or unquote(parsed.path.lstrip("/")) or "openair").strip() or "openair"
    return host, port, user, password, database


_POSTGRES_CONFIG = _postgres_connection_config()


def _default_dev_seed() -> bool:
    """本地开发默认灌演示数据；生产环境默认关闭。"""
    env = (os.environ.get("OPENAIR_ENV") or "development").strip().lower()
    return env not in ("production", "prod")


def _skill_exec_mode() -> str:
    raw = (os.environ.get("SKILL_EXEC_MODE") or "live").strip().lower()
    return raw if raw in ("live", "replay") else "live"


def _running_as_root() -> bool:
    try:
        return os.getuid() == 0
    except AttributeError:
        return False


def _claude_permission_mode() -> str:
    """Claude Agent SDK permission_mode。无头 API 默认 bypassPermissions，避免交互授权超时。"""
    raw = (os.environ.get("OPENAIR_CLAUDE_PERMISSION_MODE") or "bypassPermissions").strip()
    allowed = {
        "default",
        "acceptEdits",
        "plan",
        "bypassPermissions",
        "dontAsk",
        "auto",
    }
    mode = raw if raw in allowed else "bypassPermissions"
    # Claude Code CLI 禁止 root 使用 bypassPermissions（--dangerously-skip-permissions）
    if mode == "bypassPermissions" and _running_as_root():
        return "acceptEdits"
    return mode


def _runtime_type() -> str:
    """Execution runtime type.

    - ``inline`` (default): API process invokes Claude CLI directly.
      Suitable for local development and simple single-node deployments.
    - ``container``: Launch a Docker worker container per task execution.
      Provides per-task isolation, layered skills, and multi-user safety.
    - ``worker``: Submit to an external worker/runtime service.
      Phase-1 stub only; full implementation is out of Phase-1 scope.
    """
    raw = (os.environ.get("OPENAIR_RUNTIME_TYPE") or "inline").strip().lower()
    return raw if raw in ("inline", "container", "worker") else "inline"


def _workunit_execution_mode() -> str:
    """Canonical Research WorkUnit executor ownership mode.

    This is intentionally independent from ``OPENAIR_RUNTIME_TYPE``.  The
    latter controls the legacy conversational execution boundary, while this
    setting controls who is allowed to claim canonical research dispatches.

    Invalid values fail safe to ``worker`` so an operator typo can never
    silently enable API-side execution.
    """
    raw = (os.environ.get("OPENAIR_WORKUNIT_EXECUTION_MODE") or "worker").strip().lower()
    return raw if raw in ("worker", "inline", "worker_with_fallback") else "worker"


def _workunit_fallback_after_seconds() -> int:
    raw = (os.environ.get("OPENAIR_WORKUNIT_FALLBACK_AFTER_SECONDS") or "30").strip()
    try:
        value = int(raw)
    except ValueError:
        return 30
    return value if value >= 1 else 30


_EXECUTION_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_EXECUTION_NAMESPACE_FILE = ".openair_execution_namespace"


def _validate_execution_namespace(value: str) -> str:
    namespace = value.strip()
    if not _EXECUTION_NAMESPACE_RE.fullmatch(namespace):
        raise RuntimeError("OPENAIR_EXECUTION_NAMESPACE must match [A-Za-z0-9._:-]{1,128}")
    return namespace


def _execution_namespace() -> str:
    """Resolve one durable execution namespace shared by API and worker.

    A configured value wins.  Compose deployments otherwise persist a random
    namespace in the shared workspace volume, which is project-scoped by
    Docker Compose and therefore survives restarts without colliding with
    parallel dev/test stacks.  Production without either source fails closed.
    """
    explicit = (os.environ.get("OPENAIR_EXECUTION_NAMESPACE") or "").strip()
    if explicit:
        return _validate_execution_namespace(explicit)

    workspace_root = (os.environ.get("OPENAIR_WORKSPACE_ROOT") or "").strip()
    if workspace_root:
        root = Path(workspace_root)
        root.mkdir(parents=True, exist_ok=True)
        namespace_path = root / _EXECUTION_NAMESPACE_FILE
        try:
            fd = os.open(namespace_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            namespace = f"workspace-{secrets.token_hex(16)}"
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(namespace + "\n")
        try:
            return _validate_execution_namespace(namespace_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise RuntimeError("execution namespace file is unreadable") from exc

    env = (os.environ.get("OPENAIR_ENV") or "development").strip().lower()
    if env in {"production", "prod"}:
        raise RuntimeError(
            "production requires OPENAIR_EXECUTION_NAMESPACE or OPENAIR_WORKSPACE_ROOT"
        )
    return "local-development"


def _workspace_root() -> Path | None:
    """Task workspace root directory.

    Default ``None`` → falls back to ``repo_root() / "workspace"``.
    Cloud deployments should set this to a dedicated volume / scratch path
    so the API process does not share the repository filesystem.
    """
    raw = os.environ.get("OPENAIR_WORKSPACE_ROOT")
    if raw:
        p = Path(raw)
        if p.exists() or _env_truthy("OPENAIR_ALLOW_CREATE_WORKSPACE_ROOT", default=True):
            return p
    return None


class Settings:
    """
    OPENAIR_ENV — production | development
    OPENAIR_DEV_SEED_FIXTURES — 开发灌库开关（见 README）
    SKILL_EXEC_MODE — live | replay（Agent 默认 live，调用本机 Claude Code）
    OPENAIR_DEFAULT_SKILL_ID — 快速执行默认 Skill（默认 literature_review）
    OPENAIR_CLAUDE_PERMISSION_MODE — Claude Code 权限模式（默认 bypassPermissions，无头运行必需）
    OPENAIR_AUTH_REQUIRED — 是否开启最小认证边界（默认开发环境关闭）
    OPENAIR_AUTH_TOKEN — Bearer token 模式下的固定开发凭证
    OPENAIR_DEFAULT_OWNER_ID / OPENAIR_DEFAULT_WORKSPACE_ID / OPENAIR_DEFAULT_CREATED_BY —
        无头或开发模式下的默认请求身份
    OPENAIR_PERSISTENCE_BACKEND — json | postgres（默认 postgres）
    OPENAIR_RESEARCH_ORCHESTRATION_V1 — 研究编排 v1.2 写入开关（默认关闭）
    POSTGRES_HOST / POSTGRES_PORT / POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DATABASE —
        PostgreSQL 持久化配置
    OPENAIR_POSTGRES_MIGRATIONS_AUTO_APPLY — API 启动时是否扫描并 apply migration SQL
        （默认 True；false 时仍建连并做基础 schema bootstrap，但不调用 migration apply）
    OPENAIR_RUNTIME_TYPE — inline | worker（默认 inline）
    OPENAIR_WORKSPACE_ROOT — 任务工作区根目录（默认 repo_root/workspace）
    OPENAIR_ALLOW_LOCAL_CLI_IN_PROD — 生产环境是否允许本地 CLI 执行（默认 False）
    OPENAIR_MMP_MCP_URL — 统一 mmp-platform MCP server 地址（HTTP）；设置后注入 Claude Agent SDK
        的 mcp_servers，供 mmp-resources / mmp-training-workflow / mmp-deploy-app 三个 skill 调用平台工具
    OPENAIR_LOG_LEVEL — 日志级别：DEBUG | INFO | WARNING | ERROR（默认 INFO）
    OPENAIR_LOG_FORMAT — 日志格式：text | json（默认 text；生产建议 json）
    OPENAIR_COST_TRACKING — 是否记录粗粒度成本指标（默认 False）
    """

    env: str = (os.environ.get("OPENAIR_ENV") or "development").strip().lower()
    dev_seed_fixtures: bool = (
        _env_truthy("OPENAIR_DEV_SEED_FIXTURES", default=False)
        if os.environ.get("OPENAIR_DEV_SEED_FIXTURES") is not None
        else _default_dev_seed()
    )
    dev_fixtures_dir: str | None = os.environ.get("OPENAIR_DEV_FIXTURES_DIR") or None
    skill_exec_mode: str = _skill_exec_mode()
    default_skill_id: str = os.environ.get("OPENAIR_DEFAULT_SKILL_ID", "literature_review").strip()
    claude_permission_mode: str = _claude_permission_mode()
    auth_required: bool = _env_truthy("OPENAIR_AUTH_REQUIRED", default=False)
    auth_token: str = os.environ.get("OPENAIR_AUTH_TOKEN", "openair-dev-token").strip() or "openair-dev-token"
    # Static-token auth bypass is dev-only. When auth_required=1, the legacy static
    # token fallback is gated behind this flag (default off) so production never
    # accepts a publicly known token + X-OpenAIR-Owner-Id header to impersonate any owner.
    allow_static_token_in_prod: bool = _env_truthy("OPENAIR_ALLOW_STATIC_TOKEN_IN_PROD", default=False)
    default_owner_id: str = os.environ.get("OPENAIR_DEFAULT_OWNER_ID", "user_demo_owner").strip() or "user_demo_owner"
    default_workspace_id: str = (
        os.environ.get("OPENAIR_DEFAULT_WORKSPACE_ID", "ws_demo_default").strip() or "ws_demo_default"
    )
    default_created_by: str = (
        os.environ.get("OPENAIR_DEFAULT_CREATED_BY", "openair_system").strip() or "openair_system"
    )
    persistence_backend: str = (os.environ.get("OPENAIR_PERSISTENCE_BACKEND") or "postgres").strip().lower()
    research_orchestration_v1: bool = _env_truthy("OPENAIR_RESEARCH_ORCHESTRATION_V1", default=False)
    postgres_host: str = _POSTGRES_CONFIG[0]
    postgres_port: int = _POSTGRES_CONFIG[1]
    postgres_user: str = _POSTGRES_CONFIG[2]
    postgres_password: str = _POSTGRES_CONFIG[3]
    postgres_database: str = _POSTGRES_CONFIG[4]
    # When false: connect + baseline schema only; never scan/apply migration SQL.
    # Unset defaults to True (existing auto-apply behavior).
    postgres_migrations_auto_apply: bool = _env_truthy(
        "OPENAIR_POSTGRES_MIGRATIONS_AUTO_APPLY", default=True
    )
    runtime_type: str = _runtime_type()
    workunit_execution_mode: str = _workunit_execution_mode()
    workunit_fallback_after_seconds: int = _workunit_fallback_after_seconds()
    execution_namespace: str = _execution_namespace()
    runtime_source_version: str = runtime_source_version()
    workspace_root: Path | None = _workspace_root()
    allow_local_cli_in_prod: bool = _env_truthy("OPENAIR_ALLOW_LOCAL_CLI_IN_PROD", default=False)
    # Unified mmp-platform MCP server (HTTP). When set, injected into Claude Agent SDK
    # mcp_servers so the mmp-* skills can call platform tools (login/model_query/etc.).
    mmp_mcp_url: str | None = (os.environ.get("OPENAIR_MMP_MCP_URL") or "").strip() or None

    # --- User auth (JWT + bcrypt) ---
    jwt_secret_key: str = os.environ.get("OPENAIR_JWT_SECRET_KEY", "").strip()
    jwt_access_token_expire_minutes: int = int(
        (os.environ.get("OPENAIR_JWT_TOKEN_EXPIRE_MINUTES") or "1440").strip() or "1440"
    )
    # Production first-admin bootstrap. Empty = skip (dev seed still creates
    # admin@openair.dev locally). Both email and password must be set together.
    bootstrap_admin_email: str = (os.environ.get("OPENAIR_BOOTSTRAP_ADMIN_EMAIL") or "").strip()
    bootstrap_admin_password: str = os.environ.get("OPENAIR_BOOTSTRAP_ADMIN_PASSWORD") or ""
    bootstrap_admin_username: str = (os.environ.get("OPENAIR_BOOTSTRAP_ADMIN_USERNAME") or "").strip()
    bootstrap_admin_sync_password: bool = _env_truthy(
        "OPENAIR_BOOTSTRAP_ADMIN_SYNC_PASSWORD", default=False
    )
    # Self-hosted image captcha for public registration (Python ``captcha`` lib).
    captcha_enabled: bool = _captcha_enabled()

    # --- Sprint-03: unified user quota (S03-14-01 规则母 spec) ---
    # Default token quotas per tier; provider-neutral (tokens, not cost).
    # admin tier is unlimited and needs no limit setting.
    quota_trial_limit: int = int(os.environ.get("OPENAIR_QUOTA_TRIAL_LIMIT", "2000000") or "2000000")
    quota_free_limit: int = int(os.environ.get("OPENAIR_QUOTA_FREE_LIMIT", "200000") or "200000")
    quota_pro_limit: int = int(os.environ.get("OPENAIR_QUOTA_PRO_LIMIT", "50000000") or "50000000")
    quota_internal_limit: int = int(os.environ.get("OPENAIR_QUOTA_INTERNAL_LIMIT", "50000000") or "50000000")

    # --- Artifact storage (MinIO) ---
    artifact_storage: str = (os.environ.get("OPENAIR_ARTIFACT_STORAGE") or "local").strip().lower()
    minio_endpoint: str = os.environ.get("OPENAIR_MINIO_ENDPOINT", "localhost:9000").strip()
    minio_access_key: str = os.environ.get("OPENAIR_MINIO_ACCESS_KEY", "").strip()
    minio_secret_key: str = os.environ.get("OPENAIR_MINIO_SECRET_KEY", "").strip()
    minio_bucket: str = os.environ.get("OPENAIR_MINIO_BUCKET", "openair-artifacts").strip()
    minio_secure: bool = _env_truthy("OPENAIR_MINIO_SECURE", default=False)
    # Worker/browser-facing endpoint for presigned URLs (may differ from the API-internal
    # minio_endpoint, e.g. minio:9000 inside docker vs localhost:9000 for the browser).
    minio_worker_endpoint: str = os.environ.get("OPENAIR_MINIO_WORKER_ENDPOINT", "").strip()
    # Exact persisted scope -> reachable endpoint; bucket and TLS identity stay fixed.
    minio_scope_endpoint_overrides: str = os.environ.get(
        "OPENAIR_MINIO_SCOPE_ENDPOINT_OVERRIDES", ""
    ).strip()
    # Artifact-02: inline threshold / upload cap / presigned-URL TTL.
    artifact_inline_max_bytes: int = int(os.environ.get("OPENAIR_ARTIFACT_INLINE_MAX_BYTES", "102400") or "102400")
    artifact_upload_max_bytes: int = int(os.environ.get("OPENAIR_ARTIFACT_UPLOAD_MAX_BYTES", "1073741824") or "1073741824")
    artifact_presign_ttl_seconds: int = int(os.environ.get("OPENAIR_ARTIFACT_PRESIGN_TTL_SECONDS", "3600") or "3600")
    # --- User skills (Phase-1 hardening): upload caps + manifest delivery cap ---
    # Delivery is object-scoped presigned GET (no root key reaches the worker); these
    # caps bound per-upload cost and the size of the manifest baked into worker env.
    # Default 200: repo skills peak ~44 files; 50 was too tight once users pick a
    # folder that includes scripts + assets (or leftover __pycache__/node_modules).
    user_skill_max_files: int = int(os.environ.get("OPENAIR_USER_SKILL_MAX_FILES", "200") or "200")
    user_skill_max_file_bytes: int = int(os.environ.get("OPENAIR_USER_SKILL_MAX_FILE_BYTES", "1048576") or "1048576")  # 1 MiB/file
    user_skill_max_total_bytes: int = int(os.environ.get("OPENAIR_USER_SKILL_MAX_TOTAL_BYTES", "10485760") or "10485760")  # 10 MiB/skill
    user_skill_manifest_max_skills: int = int(os.environ.get("OPENAIR_USER_SKILL_MANIFEST_MAX_SKILLS", "20") or "20")
    # Hard caps on the serialized manifest baked into worker env (OPENAIR_USER_SKILLS_MANIFEST).
    # An unbounded single env var can blow past Docker/process env limits and break worker
    # startup. On overflow the manifest build raises → _serialise_env logs and skips the
    # manifest (turn degrades to no user skills) instead of shipping an oversized env var.
    user_skill_manifest_max_files: int = int(os.environ.get("OPENAIR_USER_SKILL_MANIFEST_MAX_FILES", "500") or "500")
    user_skill_manifest_max_bytes: int = int(os.environ.get("OPENAIR_USER_SKILL_MANIFEST_MAX_BYTES", "32768") or "32768")

    # --- Cloud-07: Observability ---
    log_level: str = (os.environ.get("OPENAIR_LOG_LEVEL") or "INFO").strip().upper()
    log_format: str = (os.environ.get("OPENAIR_LOG_FORMAT") or "text").strip().lower()
    cost_tracking: bool = _env_truthy("OPENAIR_COST_TRACKING", default=False)

    # --- CORS ---
    # Comma-separated explicit origins (e.g. "https://app.example.com,https://staging.example.com")
    # When empty, dev mode auto-allows localhost; cloud mode should set this.
    cors_origins: str = (os.environ.get("OPENAIR_CORS_ORIGINS") or "").strip()

    # --- Container Runtime (Plan B+) ---
    worker_image: str = os.environ.get("OPENAIR_WORKER_IMAGE", "openair-worker:latest").strip()
    worker_auth_token: str = os.environ.get("OPENAIR_WORKER_AUTH_TOKEN", "").strip()
    # Durable-run leases are API-owned and intentionally independent from the
    # legacy warm-worker pool lease.
    durable_run_lease_seconds: int = int(os.environ.get("OPENAIR_DURABLE_RUN_LEASE_SECONDS", "30").strip())
    # Research dispatch liveness: every pre-submission failure is either retried on a
    # bounded schedule or terminalized. These limits are shared by resolver/provider
    # failures and therefore must be identical in API and durable-worker deployments.
    research_dispatch_max_attempts: int = int(
        os.environ.get("OPENAIR_RESEARCH_DISPATCH_MAX_ATTEMPTS", "5").strip() or "5"
    )
    # Continuation starts only after its Dispatch result is durable, so its
    # internal-error recovery budget is independent of Provider retries.
    research_dispatch_continuation_internal_error_max_attempts: int = int(
        os.environ.get(
            "OPENAIR_RESEARCH_DISPATCH_CONTINUATION_INTERNAL_ERROR_MAX_ATTEMPTS",
            "3",
        ).strip()
        or "3"
    )
    # Bounded retention for continuations that repeatedly prove no successor/user
    # responsibility exists. Prevents ancient rows from starving the claim loop.
    research_dispatch_continuation_no_responsibility_proof_max_attempts: int = int(
        os.environ.get(
            "OPENAIR_RESEARCH_DISPATCH_CONTINUATION_NO_RESPONSIBILITY_PROOF_MAX_ATTEMPTS",
            "10",
        ).strip()
        or "10"
    )
    research_dispatch_continuation_zombie_claim_deprioritize_attempts: int = int(
        os.environ.get(
            "OPENAIR_RESEARCH_DISPATCH_CONTINUATION_ZOMBIE_CLAIM_DEPRIORITIZE_ATTEMPTS",
            "3",
        ).strip()
        or "3"
    )
    # Initial resolution plus at most one controlled re-resolution. Provider-call
    # retries remain governed separately by research_dispatch_max_attempts.
    research_dispatch_reresolve_max_attempts: int = int(
        os.environ.get("OPENAIR_RESEARCH_DISPATCH_RERESOLVE_MAX_ATTEMPTS", "2").strip() or "2"
    )
    research_dispatch_retry_base_seconds: int = int(
        os.environ.get("OPENAIR_RESEARCH_DISPATCH_RETRY_BASE_SECONDS", "15").strip() or "15"
    )
    research_dispatch_retry_max_seconds: int = int(
        os.environ.get("OPENAIR_RESEARCH_DISPATCH_RETRY_MAX_SECONDS", "300").strip() or "300"
    )
    # Phase-2 #3: per-task workspace isolation. On, the API (running as root) chowns each
    # task dir to root:<task_gid> 0770 and the worker is launched with that gid as a
    # supplementary group, so a worker can only read its OWN task dir on the shared volume
    # (not sibling tasks / other owners). Off ⇒ legacy behaviour (zero change). Requires
    # API-root + the worker entrypoint's conditional chown + Docker e2e validation to enable.
    worker_workspace_isolation: bool = _env_truthy("OPENAIR_WORKER_WORKSPACE_ISOLATION", default=False)
    # Phase-2 #1: mint an execution-scoped HMAC callback token bound to
    # task_id+owner_id+execution_id+execution_epoch instead of injecting the global shared
    # secret into user-controlled worker env. Off ⇒ legacy global-token behaviour.
    worker_per_task_token: bool = _env_truthy("OPENAIR_WORKER_PER_TASK_TOKEN", default=False)
    # Execution callback token lifetime (seconds). The token carries an explicit expiry and
    # is also checked against durable callback authority. It must exceed one worker turn.
    worker_token_ttl_seconds: int = int(os.environ.get("OPENAIR_WORKER_TOKEN_TTL_SECONDS", "3600") or "3600")
    # Artifact-04 通道3：异步回传 webhook 的 HMAC 密钥；未设时回退 worker_auth_token（dev 可跑）
    ingest_callback_secret: str = os.environ.get("OPENAIR_INGEST_CALLBACK_SECRET", "").strip()
    worker_callback_url: str = os.environ.get(
        "OPENAIR_WORKER_CALLBACK_URL", "http://host.docker.internal:8000"
    ).strip()
    worker_cpu_limit: float = float(os.environ.get("OPENAIR_WORKER_CPU_LIMIT", "1.0").strip())
    worker_memory_limit: str = os.environ.get("OPENAIR_WORKER_MEMORY_LIMIT", "2g").strip()
    worker_timeout_seconds: int = int(os.environ.get("OPENAIR_WORKER_TIMEOUT_SECONDS", "600").strip())
    worker_max_concurrent: int = int(os.environ.get("OPENAIR_WORKER_MAX_CONCURRENT", "4").strip())
    # Compose 部署填命名网络（如 "openair-net"），worker 入网后按服务名解析 api/minio。
    # 留空则回退默认 bridge，靠 host.docker.internal 回宿主机（本地开发场景）。
    worker_network: str = os.environ.get("OPENAIR_WORKER_NETWORK", "").strip()
    # Worker 工作区共享命名卷（compose/cloud 用）。DinD 下 API 容器通过 docker.sock 起
    # worker，bind mount 路径会被 daemon 按"它自己文件系统"的路径解析，≠ API 容器内路径，
    # 于是 worker 写的产物 API 永远读不到。命名卷按名字共享，是这里唯一正确的共享方式。
    # 留空则回退 bind mount（本地开发：API 与 docker daemon 同一宿主文件系统）。
    worker_workspace_volume: str = os.environ.get("OPENAIR_WORKER_WORKSPACE_VOLUME", "").strip()

    # --- Worker keepalive / 池化（多租户：per-task keepalive + 紧 reap + 每用户上限）---
    # worker_keepalive：开启后 worker 以"长驻 server 模式"运行，首轮跑完后继续 poll 同 task
    #   的后续 turn，避免每轮重建容器/进程；同一 task 的连续追问命中热 worker。关闭则退化为
    #   原先的 per-turn 创建即销毁（稳定默认）。仅在 OPENAIR_RUNTIME_TYPE=container 下生效。
    # NOTE: 默认关闭。keepalive 的 offer/claim 交接在集成环境里仍存在待硬化时序竞态
    #（worker poll 拿不到 turn、API 卡在探测 submit）。代码与单测保留，作为 opt-in 实验
    # 特性；待联调定位后再默认开启。OPENAIR_WORKER_RECONCILE_ON_STARTUP 仍默认开（对
    # one-shot 也清理孤儿容器）。
    worker_keepalive: bool = _env_truthy("OPENAIR_WORKER_KEEPALIVE", default=False)
    # 空闲 worker 保留窗口（秒）：超过则被 reaper 清掉（紧 reap，默认 3 min，避免 B 爆炸）。
    worker_keepalive_seconds: int = int(os.environ.get("OPENAIR_WORKER_KEEPALIVE_SECONDS", "180").strip())
    # 每用户（owner）最多保留的 idle 热 worker 数：多租户公平性闸门，防单用户占满资源。
    worker_max_per_user: int = int(os.environ.get("OPENAIR_WORKER_MAX_PER_USER", "2").strip())
    # reaper 周期清扫间隔（秒）。
    worker_reaper_interval_seconds: int = int(os.environ.get("OPENAIR_WORKER_REAPER_INTERVAL_SECONDS", "30").strip())
    # 复用热 worker 时，等待 worker 取走 turn 的宽限期（秒）：超时判定 worker 已死，驱逐后重启。
    worker_claim_grace_seconds: int = int(os.environ.get("OPENAIR_WORKER_CLAIM_GRACE_SECONDS", "10").strip())
    # worker 长 poll /internal/worker/claim 的服务端 hold 时长（秒）。
    worker_poll_timeout_seconds: int = int(os.environ.get("OPENAIR_WORKER_POLL_TIMEOUT_SECONDS", "25").strip())
    # 启动时对账：清理上次崩溃残留的孤儿 worker 容器（按 openair.managed 标签）。
    worker_reconcile_on_startup: bool = _env_truthy("OPENAIR_WORKER_RECONCILE_ON_STARTUP", default=True)

    # --- S04-01: API rate limit + heavy-task queue (invited-user scale) ---
    rate_limit_enabled: bool = _env_truthy("OPENAIR_RATE_LIMIT_ENABLED", default=True)
    rate_limit_api_per_minute: int = int(os.environ.get("OPENAIR_RATE_LIMIT_API_PER_MINUTE", "120") or "120")
    rate_limit_auth_per_minute: int = int(os.environ.get("OPENAIR_RATE_LIMIT_AUTH_PER_MINUTE", "20") or "20")
    heavy_task_queue_enabled: bool = _env_truthy("OPENAIR_HEAVY_TASK_QUEUE_ENABLED", default=True)
    heavy_task_global_limit: int = int(os.environ.get("OPENAIR_HEAVY_TASK_GLOBAL_LIMIT", "8") or "8")
    heavy_task_per_user_limit: int = int(os.environ.get("OPENAIR_HEAVY_TASK_PER_USER_LIMIT", "8") or "8")
    heavy_task_queue_wait_seconds: float = float(
        os.environ.get("OPENAIR_HEAVY_TASK_QUEUE_WAIT_SECONDS", "30") or "30"
    )
    # Password-reset: in development the raw token is returned in the API response
    # (no mailer). Production should set a mailer or out-of-band delivery.
    password_reset_ttl_minutes: int = int(os.environ.get("OPENAIR_PASSWORD_RESET_TTL_MINUTES", "60") or "60")

    # --- S04-02: GitHub OAuth (optional additive login; currently product-paused) ---
    # Prefer OPENAIR_GITHUB_OAUTH_*; accept OAUTH_CLIENT_ID/SECRET as aliases for local .env.
    # Login UI /start stay off while routes.oauth.GITHUB_OAUTH_TEMPORARILY_UNAVAILABLE is True,
    # even if credentials and OPENAIR_GITHUB_OAUTH_ENABLED are set.
    oauth_github_client_id: str = (
        os.environ.get("OPENAIR_GITHUB_OAUTH_CLIENT_ID")
        or os.environ.get("OAUTH_CLIENT_ID")
        or ""
    ).strip()
    oauth_github_client_secret: str = (
        os.environ.get("OPENAIR_GITHUB_OAUTH_CLIENT_SECRET")
        or os.environ.get("OAUTH_CLIENT_SECRET")
        or ""
    ).strip()
    oauth_github_redirect_uri: str = os.environ.get(
        "OPENAIR_GITHUB_OAUTH_REDIRECT_URI",
        "http://localhost:8000/api/auth/oauth/github/callback",
    ).strip()
    oauth_github_scopes: str = os.environ.get(
        "OPENAIR_GITHUB_OAUTH_SCOPES",
        "read:user user:email",
    ).strip()
    oauth_success_redirect_url: str = os.environ.get(
        "OPENAIR_OAUTH_SUCCESS_REDIRECT_URL",
        "http://localhost:8080/auth/oauth/callback",
    ).strip()
    # Do not auto-enable from credentials alone while GitHub support is paused.
    # Reopening later: flip the product pause flag, then set ENABLED=true with credentials.
    oauth_github_enabled: bool = _env_truthy(
        "OPENAIR_GITHUB_OAUTH_ENABLED",
        default=False,
    )

    # --- OpenI OAuth (Gitea provider; optional, independent of GitHub) ---
    # Do NOT reuse OAUTH_CLIENT_ID/SECRET — those stay GitHub aliases for local .env.
    oauth_openi_base_url: str = os.environ.get(
        "OPENAIR_OPENI_OAUTH_BASE_URL",
        "https://openi.pcl.ac.cn",
    ).strip()
    oauth_openi_client_id: str = os.environ.get("OPENAIR_OPENI_OAUTH_CLIENT_ID", "").strip()
    oauth_openi_client_secret: str = os.environ.get("OPENAIR_OPENI_OAUTH_CLIENT_SECRET", "").strip()
    oauth_openi_redirect_uri: str = os.environ.get(
        "OPENAIR_OPENI_OAUTH_REDIRECT_URI",
        "http://localhost:8000/api/auth/oauth/openi/callback",
    ).strip()
    # OpenI/Gitea: leave empty unless the application UI requires scopes.
    oauth_openi_scopes: str = os.environ.get("OPENAIR_OPENI_OAUTH_SCOPES", "").strip()
    oauth_openi_enabled: bool = _env_truthy(
        "OPENAIR_OPENI_OAUTH_ENABLED",
        default=bool(
            os.environ.get("OPENAIR_OPENI_OAUTH_CLIENT_ID", "").strip()
            and os.environ.get("OPENAIR_OPENI_OAUTH_CLIENT_SECRET", "").strip()
        ),
    )

    # --- GitLink OAuth (optional; independent of GitHub / OpenI) ---
    # Prefer OPENAIR_GITLINK_OAUTH_*; accept OPENAIR_OAUTH_GITLINK_* (legacy branch) aliases.
    oauth_gitlink_client_id: str = (
        os.environ.get("OPENAIR_GITLINK_OAUTH_CLIENT_ID")
        or os.environ.get("OPENAIR_OAUTH_GITLINK_CLIENT_ID")
        or os.environ.get("OPENAIR_GITLINK_CLIENT_ID")
        or ""
    ).strip()
    oauth_gitlink_client_secret: str = (
        os.environ.get("OPENAIR_GITLINK_OAUTH_CLIENT_SECRET")
        or os.environ.get("OPENAIR_OAUTH_GITLINK_CLIENT_SECRET")
        or os.environ.get("OPENAIR_GITLINK_CLIENT_SECRET")
        or ""
    ).strip()
    oauth_gitlink_redirect_uri: str = os.environ.get(
        "OPENAIR_GITLINK_OAUTH_REDIRECT_URI",
        os.environ.get(
            "OPENAIR_OAUTH_REDIRECT_URI",
            "http://localhost:8000/api/auth/oauth/gitlink/callback",
        ),
    ).strip()
    oauth_gitlink_authorize_url: str = (
        os.environ.get("OPENAIR_GITLINK_OAUTH_AUTHORIZE_URL")
        or os.environ.get("OPENAIR_OAUTH_GITLINK_AUTHORIZE_URL")
        or "https://www.gitlink.org.cn/oauth/authorize"
    ).strip()
    oauth_gitlink_token_url: str = (
        os.environ.get("OPENAIR_GITLINK_OAUTH_TOKEN_URL")
        or os.environ.get("OPENAIR_OAUTH_GITLINK_TOKEN_URL")
        or "https://www.gitlink.org.cn/oauth/token"
    ).strip()
    oauth_gitlink_userinfo_url: str = (
        os.environ.get("OPENAIR_GITLINK_OAUTH_USERINFO_URL")
        or os.environ.get("OPENAIR_OAUTH_GITLINK_USERINFO_URL")
        or "https://www.gitlink.org.cn/api/users/me.json"
    ).strip()
    oauth_gitlink_scopes: str = (
        os.environ.get("OPENAIR_GITLINK_OAUTH_SCOPES")
        or os.environ.get("OPENAIR_OAUTH_GITLINK_SCOPES")
        or ""
    ).strip()
    oauth_gitlink_enabled: bool = _env_truthy(
        "OPENAIR_GITLINK_OAUTH_ENABLED",
        default=bool(
            (
                os.environ.get("OPENAIR_GITLINK_OAUTH_CLIENT_ID")
                or os.environ.get("OPENAIR_OAUTH_GITLINK_CLIENT_ID")
                or os.environ.get("OPENAIR_GITLINK_CLIENT_ID")
                or ""
            ).strip()
            and (
                os.environ.get("OPENAIR_GITLINK_OAUTH_CLIENT_SECRET")
                or os.environ.get("OPENAIR_OAUTH_GITLINK_CLIENT_SECRET")
                or os.environ.get("OPENAIR_GITLINK_CLIENT_SECRET")
                or ""
            ).strip()
        ),
    )

    # 资源广场「设备资源 / 算力资源」平台清单（详见 app/modules/flux_platform_client.py
    # 与 app/modules/jcc_inventory_collector.py；FLUX_*/JCC_* 与 skill 侧变量名保持一致）
    flux_base_url: str = os.environ.get("FLUX_BASE_URL", "").strip()
    flux_api_key: str = os.environ.get("FLUX_API_KEY", "").strip()
    flux_timeout_seconds: float = float(os.environ.get("FLUX_TIMEOUT", "10") or "10")
    jcc_cli_path: str = os.environ.get("JCC_CLI_PATH", "").strip()
    jcc_username: str = os.environ.get("JCC_USERNAME", "").strip()
    jcc_password: str = os.environ.get("JCC_PASSWORD", "").strip()
    jcc_inventory_ttl_seconds: float = float(
        os.environ.get("JCC_INVENTORY_TTL_SECONDS", "86400") or "86400"
    )

    @property
    def is_production(self) -> bool:
        return self.env in ("production", "prod")

    def execution_startup_contract(self) -> dict[str, object]:
        """Non-secret API/worker settings that must match in production."""
        return {
            "contractVersion": 4,
            "runtimeSourceVersion": self.runtime_source_version,
            "authRequired": self.auth_required,
            "researchOrchestrationV1": self.research_orchestration_v1,
            "persistenceBackend": self.persistence_backend,
            "runtimeType": self.runtime_type,
            "workUnitExecutionMode": self.workunit_execution_mode,
            "workUnitFallbackAfterSeconds": self.workunit_fallback_after_seconds,
            "executionNamespace": self.execution_namespace,
            "skillExecMode": self.skill_exec_mode,
            "dispatchMaxAttempts": self.research_dispatch_max_attempts,
            "dispatchContinuationInternalErrorMaxAttempts": (
                self.research_dispatch_continuation_internal_error_max_attempts
            ),
            "dispatchContinuationNoResponsibilityProofMaxAttempts": (
                self.research_dispatch_continuation_no_responsibility_proof_max_attempts
            ),
            "dispatchContinuationZombieClaimDeprioritizeAttempts": (
                self.research_dispatch_continuation_zombie_claim_deprioritize_attempts
            ),
            "dispatchReresolveMaxAttempts": self.research_dispatch_reresolve_max_attempts,
            "dispatchRetryBaseSeconds": self.research_dispatch_retry_base_seconds,
            "dispatchRetryMaxSeconds": self.research_dispatch_retry_max_seconds,
            "workspaceIsolation": self.worker_workspace_isolation,
            "perTaskToken": self.worker_per_task_token,
            "workerTokenTtlSeconds": self.worker_token_ttl_seconds,
            "workerKeepalive": self.worker_keepalive,
            "durableClaimNotBefore": (
                os.environ.get("OPENAIR_DURABLE_CLAIM_NOT_BEFORE") or ""
            ).strip(),
        }

    def production_secrets_errors(self) -> list[str]:
        """Return fatal config errors for production deployments (empty = OK).

        Fail-fast guard (Cloud-02/07): production must not run on well-known or
        default secrets. Checked at startup in ``app.main.lifespan``.
        """
        errors: list[str] = []
        raw_runtime_type = (os.environ.get("OPENAIR_RUNTIME_TYPE") or "").strip().lower()
        if not self.auth_required:
            errors.append("production requires OPENAIR_AUTH_REQUIRED=1")
        if not self.research_orchestration_v1:
            errors.append("production requires OPENAIR_RESEARCH_ORCHESTRATION_V1=true")
        if self.persistence_backend != "postgres":
            errors.append("production canonical orchestration requires PostgreSQL persistence")
        if raw_runtime_type not in {"inline", "container", "worker"}:
            errors.append("production requires an explicit supported OPENAIR_RUNTIME_TYPE")
        if self.research_dispatch_max_attempts <= 0:
            errors.append("OPENAIR_RESEARCH_DISPATCH_MAX_ATTEMPTS must be positive")
        if self.research_dispatch_continuation_internal_error_max_attempts <= 0:
            errors.append(
                "OPENAIR_RESEARCH_DISPATCH_CONTINUATION_INTERNAL_ERROR_MAX_ATTEMPTS "
                "must be positive"
            )
        if self.research_dispatch_continuation_no_responsibility_proof_max_attempts <= 0:
            errors.append(
                "OPENAIR_RESEARCH_DISPATCH_CONTINUATION_NO_RESPONSIBILITY_PROOF_MAX_ATTEMPTS "
                "must be positive"
            )
        if self.research_dispatch_continuation_zombie_claim_deprioritize_attempts <= 0:
            errors.append(
                "OPENAIR_RESEARCH_DISPATCH_CONTINUATION_ZOMBIE_CLAIM_DEPRIORITIZE_ATTEMPTS "
                "must be positive"
            )
        if not 1 <= self.research_dispatch_reresolve_max_attempts <= 2:
            errors.append(
                "OPENAIR_RESEARCH_DISPATCH_RERESOLVE_MAX_ATTEMPTS must be 1 or 2"
            )
        if (
            self.research_dispatch_retry_base_seconds <= 0
            or self.research_dispatch_retry_max_seconds < self.research_dispatch_retry_base_seconds
        ):
            errors.append("dispatch retry bounds are invalid")
        if raw_runtime_type == "container" and not (
            self.worker_workspace_isolation and self.worker_per_task_token
        ):
            errors.append(
                "container production requires workspace isolation and per-task worker tokens"
            )
        if raw_runtime_type == "container" and self.worker_per_task_token:
            if self.worker_token_ttl_seconds <= self.worker_timeout_seconds:
                errors.append(
                    "OPENAIR_WORKER_TOKEN_TTL_SECONDS must exceed OPENAIR_WORKER_TIMEOUT_SECONDS"
                )
            if self.worker_keepalive:
                errors.append(
                    "execution-scoped worker tokens require OPENAIR_WORKER_KEEPALIVE=0"
                )
        if self.auth_required:
            if not self.jwt_secret_key:
                errors.append(
                    "OPENAIR_AUTH_REQUIRED=1 requires OPENAIR_JWT_SECRET_KEY "
                    "(set a strong random key, e.g. `openssl rand -hex 32`)."
                )
            elif self.jwt_secret_key == "change-me-in-production":
                errors.append(
                    "OPENAIR_JWT_SECRET_KEY is the known default 'change-me-in-production' — override it."
                )
            if self.allow_static_token_in_prod and self.auth_token == "openair-dev-token":
                errors.append(
                    "OPENAIR_AUTH_TOKEN is the public default 'openair-dev-token' — "
                    "override it before enabling OPENAIR_ALLOW_STATIC_TOKEN_IN_PROD."
                )
        if self.postgres_password in {"", "password", "openair123456"}:
            errors.append(
                "POSTGRES_PASSWORD is a default/weak value — override it in production."
            )
        bootstrap_email = (self.bootstrap_admin_email or "").strip()
        bootstrap_password = self.bootstrap_admin_password or ""
        if bootstrap_email or bootstrap_password:
            if not bootstrap_email or not bootstrap_password:
                errors.append(
                    "bootstrap admin requires both OPENAIR_BOOTSTRAP_ADMIN_EMAIL and "
                    "OPENAIR_BOOTSTRAP_ADMIN_PASSWORD"
                )
            elif len(bootstrap_password) < 8:
                errors.append(
                    "OPENAIR_BOOTSTRAP_ADMIN_PASSWORD must be at least 8 characters in production"
                )
            elif bootstrap_password in {"admin123456", "password", "12345678"}:
                errors.append(
                    "OPENAIR_BOOTSTRAP_ADMIN_PASSWORD is a known/weak value — use a unique secret"
                )
        return errors


settings = Settings()
