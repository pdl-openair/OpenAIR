"""OpenAIR API — 须在创建 event loop 之前设置 Windows 子进程策略（Claude Agent SDK）。"""
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import logging
import os
import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.modules.task_dev_seed import seed_store_from_fixtures
from app.modules.intent_session_store import intent_session_store
from app.modules.repo_paths import repo_root
from app.modules.task_repository import TaskRepository
from app.modules.task_store import TaskStorePostgresMutationForbidden, store
from app.modules.user_repository import get_user_repository
from app.modules.user_dev_seed import ADMIN_EMAIL, DEV_EMAIL, seed_admin_user, seed_dev_user
from app.modules.admin_bootstrap import ensure_bootstrap_admin
from app.routes import admin, admin_quota, admin_users, agent, agent_sessions, artifacts, audit as audit_routes, auth as auth_routes, collaboration, community, contributions, controller_commands, execution_runs, health, internal, model_jobs, oauth as oauth_routes, platform_inventory, pvd_feedback, quota as quota_routes, recovery, research_packages, research_start, resource_saves, resources, skills as skill_routes, starters, tasks
from app.modules.rate_limit import RateLimitMiddleware
from app.routes.audit import ExceptionAuditMiddleware

# ---------------------------------------------------------------------------
# Structured logging setup (Cloud-07)
# ---------------------------------------------------------------------------

def _setup_logging() -> logging.Logger:
    """Configure structured logging based on OPENAIR_LOG_FORMAT and OPENAIR_LOG_LEVEL."""
    logger = logging.getLogger("openair")
    level_name = settings.log_level
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)

        if settings.log_format == "json":
            try:
                import json as _json

                class JsonFormatter(logging.Formatter):
                    def format(self, record: logging.LogRecord) -> str:
                        entry = {
                            "ts": self.formatTime(record, self.default_time_format),
                            "level": record.levelname,
                            "logger": record.name,
                            "msg": record.getMessage(),
                        }
                        if record.exc_info and record.exc_info[0]:
                            entry["exc"] = self.formatException(record.exc_info)
                        return _json.dumps(entry, ensure_ascii=False)

                handler.setFormatter(JsonFormatter())
            except Exception:
                # Fallback to text if json formatter setup fails
                handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
        else:
            fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
            handler.setFormatter(logging.Formatter(fmt))

        logger.addHandler(handler)

    # Quiet down noisy third-party loggers
    for noisy in ("uvicorn.access", "httpx", "httpcore", "urllib3", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return logger


log = _setup_logging()


async def _artifact_cleanup_reconciliation_loop(stop_event: asyncio.Event) -> None:
    """Periodically consume durable cleanup receipts in every API process."""
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
            continue
        except TimeoutError:
            pass
        try:
            from app.modules.artifact_cleanup import ArtifactCleanupService

            cleanup = await asyncio.to_thread(
                ArtifactCleanupService().reconcile_pending,
                batch_size=32,
            )
            if cleanup.claimed:
                log.info(
                    "[recovery] artifact cleanup claimed=%d completed=%d retried=%d failed=%d",
                    cleanup.claimed,
                    cleanup.completed,
                    cleanup.retried,
                    cleanup.failed,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("[recovery] artifact cleanup reconciliation failed: %s", exc)


# ---------------------------------------------------------------------------
# Request logging middleware (Cloud-07)
# ---------------------------------------------------------------------------

class RequestLoggingMiddleware:
    """Log every API request with method, path, status, duration and owner scope."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http",):
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "?")
        path = scope.get("path", "?")
        start = time.monotonic()

        # Capture status from response
        status_code = 0

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.monotonic() - start) * 1000)
            # Extract owner from headers if available
            headers = dict(scope.get("headers", []))
            owner = headers.get(b"x-openair-owner-id", b"-").decode("utf-8", errors="replace")
            # Durable-worker empty polls hit /internal/durable-runs/claim about
            # once per second. Keep them at DEBUG so real failures are visible.
            quiet_ok = (
                method == "POST"
                and path.rstrip("/") == "/internal/durable-runs/claim"
                and status_code == 200
            )
            log_fn = log.debug if quiet_ok else log.info
            log_fn(
                "request %s %s → %s (%dms) owner=%s",
                method,
                path,
                status_code,
                duration_ms,
                owner,
            )


# ---------------------------------------------------------------------------
# Cost tracking helpers (Cloud-07)
# ---------------------------------------------------------------------------

_cost_counters: dict[str, float] = {
    "claude_api_calls": 0,
    "task_executions": 0,
    "artifact_bytes_written": 0,
}


def record_cost(metric: str, value: float = 1) -> None:
    """Increment a cost counter. Only records when OPENAIR_COST_TRACKING=1."""
    if not settings.cost_tracking:
        return
    _cost_counters[metric] = _cost_counters.get(metric, 0) + value


def get_cost_snapshot() -> dict[str, float]:
    """Return current cost counter snapshot."""
    return dict(_cost_counters)


# ---------------------------------------------------------------------------
# Startup helpers
# ---------------------------------------------------------------------------

def _warn_port_conflict():
    """检测 0.0.0.0 上是否有其他进程占用同一端口（Windows 特有问题）。"""
    import socket

    port = int(os.environ.get("OPENAIR_API_PORT", "8000"))
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.bind(("0.0.0.0", port))
    except OSError:
        log.warning("端口 %s 在 0.0.0.0 上已被占用，前端请求可能被拦截", port)


def _print_startup_banner():
    """Print a structured startup banner with key config."""
    lines = [
        f"env={settings.env}",
        f"auth={'required' if settings.auth_required else 'open'}",
        f"persistence={settings.persistence_backend}",
        f"runtime={settings.runtime_type}",
        f"workunit_execution={settings.workunit_execution_mode}",
        f"workunit_fallback_after={settings.workunit_fallback_after_seconds}s",
        f"skill_exec={settings.skill_exec_mode}",
        f"log_level={settings.log_level}",
        f"log_format={settings.log_format}",
        f"cost_tracking={'on' if settings.cost_tracking else 'off'}",
        f"workspace_root={settings.workspace_root or '<repo_root>/workspace'}",
    ]
    if settings.persistence_backend == "postgres":
        lines.append(f"pg={settings.postgres_host}:{settings.postgres_port}/{settings.postgres_database}")
        lines.append(
            f"migrations_auto_apply={'true' if settings.postgres_migrations_auto_apply else 'false'}"
        )
    for line in lines:
        log.info("[startup] %s", line)


def _bootstrap_postgres_repositories(*, auto_apply_migrations: bool) -> None:
    """API postgres startup: baseline repo bootstrap, then optional migration apply.

    ``auto_apply_migrations`` is the only gate for ``apply_postgres_migrations`` /
    migration directory scan. Callers must not fake disable via empty dirs.
    """
    from app.modules.artifact_relation_repository import ArtifactRelationRepository
    from app.modules.artifact_repository import ArtifactRepository
    from app.modules.durable_execution import DurableExecutionRepository

    repository = TaskRepository()
    repository.bootstrap()
    # TaskStore is a JSON/development cache only.  PostgreSQL mutations
    # must be issued by API-owned repositories/services, never mirrored
    # from an in-process TaskStore snapshot.
    # Artifact-03: 一等实体表 + 关系表（catalog 读源 / 来源链）
    # The ordered migration runner is the only DDL/backfill authority.
    DurableExecutionRepository().bootstrap(auto_apply_migrations)
    artifact_repo = ArtifactRepository()
    artifact_repo.bootstrap()
    if settings.artifact_storage == "minio":
        from app.modules.minio_client import current_minio_storage_scope

        artifact_repo.configure_external_storage_scope(current_minio_storage_scope())
    relation_repo = ArtifactRelationRepository()
    relation_repo.bootstrap()
    persisted = store.load_persisted_tasks()
    if persisted:
        log.info("persisted tasks: %d task(s) loaded from PostgreSQL", persisted)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    artifact_cleanup_stop: asyncio.Event | None = None
    artifact_cleanup_task: asyncio.Task[None] | None = None
    workunit_executor_stop: asyncio.Event | None = None
    workunit_executor_task: asyncio.Task[None] | None = None
    # Fail-fast: refuse to boot production on known/default secrets (Cloud-02/07).
    if settings.is_production:
        errors = settings.production_secrets_errors()
        if errors:
            for err in errors:
                log.error("[startup] FATAL: %s", err)
            raise SystemExit(
                f"Refusing to start: insecure production config ({len(errors)} error(s)); see logs above."
            )
    _warn_port_conflict()
    _print_startup_banner()

    seeded = seed_store_from_fixtures(store) if settings.persistence_backend != "postgres" else 0
    if seeded:
        log.info("dev fixture seed: %d task(s) loaded into memory store", seeded)

    # Users/workspaces bootstrap must run BEFORE postgres migrations
    # (DurableExecutionRepository.bootstrap below): quota migrations reference
    # openair_users via FK (Sprint-03, migrations/20260731_08_user_token_quota.sql).
    user_repo = get_user_repository()
    user_repo.bootstrap()
    bootstrapped = ensure_bootstrap_admin(user_repo)
    if bootstrapped:
        log.info("bootstrap admin ready: %s", bootstrapped.get("email"))

    # S04-01 governance stores (audit / password-reset / contributions)
    from app.modules.audit_repository import get_audit_repository
    from app.modules.contribution_repository import get_contribution_repository
    from app.modules.password_reset_repository import get_password_reset_repository

    get_audit_repository().bootstrap()
    get_password_reset_repository().bootstrap()
    get_contribution_repository().bootstrap()

    try:
        from app.modules.resource_catalog import warm_resource_catalog_cache

        warmed = warm_resource_catalog_cache()
        log.info("[startup] resource catalog cache warmed: %d resource(s)", warmed)
    except Exception as exc:
        # Catalog remains lazy-built on first request; do not block API boot.
        log.warning("[startup] resource catalog cache warm failed: %s", exc)

    if settings.persistence_backend == "postgres":
        _bootstrap_postgres_repositories(
            auto_apply_migrations=settings.postgres_migrations_auto_apply
        )

    # Provider readiness is real external I/O and therefore must run only after
    # the physical-attempt Authority is available. This also keeps startup
    # probes under the same durable truth as runtime Provider submissions.
    from app.modules.provider_registry import (
        ProviderRegistryError,
        is_transient_provider_error,
        resolve_model_binding,
    )
    try:
        bindings = [resolve_model_binding(role) for role in ("planner", "execution")]
        log.info("MODEL_BINDING_VALIDATION=PASS bindings=%s", [(b.role, b.provider, b.model) for b in bindings])
    except ProviderRegistryError as exc:
        if is_transient_provider_error(exc):
            # Provider availability is runtime state; do not prevent the API
            # from serving health/configuration endpoints when upstream is
            # temporarily unreachable. Runtime request paths retain their
            # normal provider error handling and will report the outage.
            log.warning("MODEL_BINDING_VALIDATION=DEFERRED provider temporarily unavailable: %s", exc)
        else:
            log.error("MODEL_BINDING_VALIDATION=FAIL %s", exc)
            raise SystemExit(str(exc)) from exc

    # MinIO artifact storage initialization
    if settings.artifact_storage == "minio":
        try:
            from app.modules.minio_client import ensure_minio_bucket

            ensure_minio_bucket()
            log.info("[startup] artifact storage: minio (%s/%s)", settings.minio_endpoint, settings.minio_bucket)
        except Exception as exc:
            log.warning("[startup] MinIO initialization failed: %s — artifact storage will use local fallback", exc)
    else:
        log.info("[startup] artifact storage: local")

    if settings.persistence_backend == "postgres":
        try:
            from app.modules.artifact_cleanup import ArtifactCleanupService

            cleanup = ArtifactCleanupService().reconcile_pending(batch_size=32)
            if cleanup.claimed:
                log.info(
                    "[startup] artifact cleanup reconciled claimed=%d completed=%d retried=%d failed=%d",
                    cleanup.claimed,
                    cleanup.completed,
                    cleanup.retried,
                    cleanup.failed,
                )
        except Exception as exc:
            # Durable claims make this recoverable; storage cleanup must not
            # prevent API startup.
            log.warning("[startup] artifact cleanup reconciliation failed: %s", exc)
        artifact_cleanup_stop = asyncio.Event()
        artifact_cleanup_task = asyncio.create_task(
            _artifact_cleanup_reconciliation_loop(artifact_cleanup_stop),
            name="artifact-cleanup-reconciliation",
        )

    if settings.persistence_backend == "postgres":
        from app.execution.research_dispatch_api_executor import (
            api_workunit_executor_enabled,
            run_api_workunit_executor,
        )

        if api_workunit_executor_enabled():
            workunit_executor_stop = asyncio.Event()
            workunit_executor_task = asyncio.create_task(
                run_api_workunit_executor(workunit_executor_stop),
                name="research-workunit-api-executor",
            )

    if settings.persistence_backend != "postgres":
        store.enable_persistence(repo_root() / "data" / "tasks")
        persisted = store.load_persisted_tasks()
        if persisted:
            log.info("persisted tasks: %d task(s) loaded from data/tasks/", persisted)
        intent_session_store.enable_persistence(repo_root() / "data" / "intent_sessions")
        persisted_sessions = intent_session_store.load_persisted_sessions()
        if persisted_sessions:
            log.info("persisted intent sessions: %d session(s) loaded from data/intent_sessions/", persisted_sessions)

    if not settings.is_production:
        created = seed_dev_user(user_repo)
        if created:
            log.info("dev seed: test user %s created", DEV_EMAIL)
        admin_created = seed_admin_user(user_repo)
        if admin_created:
            log.info("dev seed: admin user %s created", ADMIN_EMAIL)

        if settings.dev_seed_fixtures and settings.persistence_backend != "postgres":
            dev_user = user_repo.get_user_by_email(DEV_EMAIL)
            if dev_user:
                workspaces = user_repo.get_user_workspaces(dev_user["user_id"])
                if workspaces:
                    rebinded = store.rebind_legacy_fixture_ownership(
                        owner_id=dev_user["user_id"],
                        workspace_id=workspaces[0]["workspace_id"],
                    )
                    if rebinded:
                        log.info(
                            "dev fixture rebind: %d task(s) bound to dev user %s / ws %s",
                            rebinded,
                            dev_user["user_id"],
                            workspaces[0]["workspace_id"],
                        )

    if os.environ.get("SKILL_EXEC_MODE", "live").lower() != "replay":
        try:
            from app.execution.claude_cli_resolver import resolve_claude_cli_path

            cli = resolve_claude_cli_path()
            log.info("claude code cli: %s", cli)
        except OSError as e:
            log.warning("claude cli not resolved at startup: %s", e)
        from app.execution.claude_code_runner import log_headless_allowed_tools

        log_headless_allowed_tools()

    # Worker keepalive reaper (container runtime): reconcile orphan containers
    # left by a previous/crashed API process, then start the periodic idle-sweep
    # + per-user cap enforcement.
    if settings.runtime_type == "container":
        try:
            from app.execution.reaper import get_reaper

            reaper = get_reaper()
            removed = reaper.reconcile_on_startup()
            if removed:
                log.info("[startup] reconciled %d orphan worker container(s)", removed)
            await reaper.start()
        except Exception as exc:
            log.warning("[startup] worker reaper init failed: %s", exc)
    yield

    if workunit_executor_stop is not None and workunit_executor_task is not None:
        workunit_executor_stop.set()
        try:
            await workunit_executor_task
        except asyncio.CancelledError:
            pass

    if artifact_cleanup_stop is not None and artifact_cleanup_task is not None:
        artifact_cleanup_stop.set()
        try:
            await artifact_cleanup_task
        except asyncio.CancelledError:
            pass

    # Shutdown: stop the reaper loop.
    if settings.runtime_type == "container":
        try:
            from app.execution.reaper import get_reaper

            await get_reaper().stop()
        except Exception as exc:
            log.warning("[shutdown] worker reaper stop failed: %s", exc)


app = FastAPI(title="OpenAIR API", version="0.0.0", lifespan=lifespan)


@app.exception_handler(TaskStorePostgresMutationForbidden)
async def taskstore_postgres_mutation_forbidden(_request: Request, exc: TaskStorePostgresMutationForbidden) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


async def quota_exceeded_handler(_request: Request, exc: Exception) -> JSONResponse:
    """HTTP 402 + structured body when a user is out of quota (S03-14-03)."""
    from app.models.account_schemas import QuotaExceededError

    payload = QuotaExceededError(
        message=str(exc),
        tier=getattr(exc, "tier", "free"),
        remaining=int(getattr(exc, "remaining", 0)),
        resetAt=getattr(exc, "reset_at", None),
    )
    return JSONResponse(status_code=402, content=payload.model_dump())


def _register_quota_exception_handler() -> None:
    """Register the QuotaExceeded handler by class (deferred import to avoid cycles)."""
    from app.modules.quota_service import QuotaExceeded

    app.add_exception_handler(QuotaExceeded, quota_exceeded_handler)


_register_quota_exception_handler()


_LEGACY_TASKSTORE_PREFIXES = ("/api/tasks/", "/api/artifacts", "/api/starters")


def _postgres_legacy_mutation_allowed(method: str, path: str) -> bool:
    """Whether a POST/PATCH/PUT/DELETE is an explicit repository-backed mutation
    that must NOT be rejected by the legacy-taskstore guard in postgres mode.

    Extracted to a pure function so the allowlist is unit-testable (the JSON
    TestClient can't exercise the postgres guard, which is how the clone/reuse
    and artifact-edit 503 regressions slipped through). Any new repo-backed
    write route under the legacy prefixes MUST be added here, or it 503s.
    """
    return (
        (method == "POST" and path == "/api/tasks")
        # Draft creation persists its task and initial artifact through the
        # PostgreSQL repositories; it does not mutate the legacy TaskStore.
        or (method == "POST" and path == "/api/tasks/draft")
        # Task Board / Task List deletion is an API-repository command, not a
        # JSON TaskStore mutation.  Exact one-segment path only — nested DELETE
        # routes under /api/tasks/... remain fail-closed.
        or (method == "DELETE" and re.fullmatch(r"/api/tasks/[^/]+", path) is not None)
        or (method == "POST" and path.startswith("/api/tasks/") and path.endswith("/confirm-plan"))
        or (method == "POST" and path.startswith("/api/tasks/") and path.endswith("/update-plan"))
        # Canonical ResearchDesign agent revisions call ResearchDesignService,
        # which appends a new immutable version through
        # ResearchOrchestrationRepository.  They never mutate TaskStore.
        or (method == "POST" and re.fullmatch(
            r"/api/tasks/[^/]+/research-design/agent-revision", path,
        ) is not None)
        # PVD experiment feedback writes through ResearchOrchestrationRepository
        # (scientific-state append + reactivation) and ModelJobQueueRepository,
        # never the JSON TaskStore.
        or (method == "POST" and re.fullmatch(
            r"/api/tasks/[^/]+/pvd/experiment-feedback", path,
        ) is not None)
        or (method == "POST" and path.startswith("/api/tasks/") and path.endswith("/start"))
        or (method == "POST" and path.startswith("/api/tasks/") and path.endswith("/controller-commands"))
        or (method == "POST" and re.fullmatch(
            r"/api/tasks/[^/]+/(?:stop-run|resume-run)", path,
        ) is not None)
        or (method == "PATCH" and re.fullmatch(r"/api/tasks/[^/]+", path) is not None)
        or (method == "DELETE" and re.fullmatch(r"/api/tasks/[^/]+", path) is not None)
        or (method == "POST" and re.fullmatch(
            r"/api/tasks/[^/]+/(?:revise-plan|cancel-draft|clone)", path,
        ) is not None)
        or (method == "POST" and path == "/api/artifacts/upload")
        or (method == "DELETE" and re.fullmatch(r"/api/artifacts/[^/]+", path) is not None)
        or (method == "POST" and re.fullmatch(
            r"/api/tasks/[^/]+/collaboration/decisions/[^/]+", path,
        ) is not None)
        or (method == "POST" and re.fullmatch(
            r"/api/tasks/[^/]+/work-units/[^/]+/external-result/(?:reconcile|retry)", path,
        ) is not None)
        # Explicit collaboration remains a user-triggered chat path.  Its
        # PostgreSQL implementation writes through TaskRepository CAS methods,
        # not TaskStore, and never dispatches a durable first-execution run.
        or (method == "POST" and path.startswith("/api/tasks/") and path.endswith("/agent/chat/stream"))
        or (method in {"PATCH", "DELETE"} and re.fullmatch(
            r"/api/tasks/[^/]+", path,
        ) is not None)
        or (method == "PATCH" and path.startswith("/api/tasks/")
            and (path.endswith("/execution-mode") or path.endswith("/execution-policy")))
        # Artifact review/scope/reuse routes write through ArtifactRepository
        # (reuse = content-addressed object-store put + catalog upsert, no
        # TaskStore mutation) and commit the matching Task revision in the same
        # PostgreSQL transaction.
        or (method == "POST" and re.fullmatch(
            r"/api/artifacts/[^/]+/(review|confirm-scope-expansion|reuse)", path,
        ) is not None)
        # Artifact content edit / version restore write through ArtifactRepository
        # (version append into metadata->versions; trigger syncs storage refs).
        or (method == "PATCH" and re.fullmatch(
            r"/api/artifacts/[^/]+/content", path,
        ) is not None)
        or (method == "POST" and re.fullmatch(
            r"/api/artifacts/[^/]+/versions/[^/]+/restore", path,
        ) is not None)
        or path.startswith("/internal/durable-runs/")
        # Worker control/data-plane protocol (startup-contract, claim, result,
        # stream-event, artifacts) is repository-backed, not TaskStore.
        or path.startswith("/internal/worker/")
        # Startup-contract is a read-only control-plane contract check; allow it explicitly.
        or (method == "POST" and path == "/internal/worker/startup-contract")
    )


@app.middleware("http")
async def reject_legacy_taskstore_mutations_in_postgres(request: Request, call_next):
    """Reject legacy JSON-store HTTP commands before they can have side effects.

    The compatibility routes still exist for the JSON development backend.
    In PostgreSQL mode their TaskStore mutation cannot be allowed to start an
    upload, an agent background task, or another external effect and then fail
    only at the final in-memory write.  The durable task create/start/stop and
    worker protocol routes are API-repository commands and remain available.
    """
    if settings.persistence_backend != "postgres" or request.method not in {"POST", "PATCH", "PUT", "DELETE"}:
        return await call_next(request)
    path = request.url.path.rstrip("/") or "/"
    if request.method == "POST" and (
        re.fullmatch(r"/api/tasks/[^/]+/recovery-requests", path) is not None
        or re.fullmatch(r"/api/tasks/[^/]+/recovery-requests/[^/]+/confirm", path) is not None
    ):
        return JSONResponse(
            status_code=410,
            content={"detail": {"code": "legacy_recovery_read_only"}},
        )
    if (
        not _postgres_legacy_mutation_allowed(request.method, path)
        and path.startswith(_LEGACY_TASKSTORE_PREFIXES)
    ):
        return JSONResponse(status_code=503, content={"detail": "taskstore_mutation_forbidden_in_postgres"})
    return await call_next(request)

# Request logging middleware — add before CORS so we log all requests
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(ExceptionAuditMiddleware)
app.add_middleware(RateLimitMiddleware)

def _build_cors_config() -> tuple[list[str], str | None]:
    """Build CORS allow_origins and allow_origin_regex.

    Priority:
    1. OPENAIR_CORS_ORIGINS (explicit comma-separated list) — used in cloud deployments
    2. Development mode — auto-allow localhost
    3. Fallback — allow all origins with regex (should not happen in production)
    """
    explicit = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if explicit:
        return explicit, None

    if not settings.is_production:
        return (
            ["http://localhost:5173", "http://127.0.0.1:5173"],
            r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        )

    # Production without explicit CORS origins — log a warning
    log.warning(
        "OPENAIR_CORS_ORIGINS not set in production mode; "
        "frontend may not be able to reach the API. "
        "Set OPENAIR_CORS_ORIGINS to the frontend URL(s)."
    )
    return [], None


_cors_origins, _cors_regex = _build_cors_config()

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_cors_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

app.include_router(health.router)
app.include_router(tasks.router, prefix="/api")
app.include_router(pvd_feedback.router, prefix="/api")
app.include_router(controller_commands.router, prefix="/api")
app.include_router(recovery.router, prefix="/api")
app.include_router(collaboration.router, prefix="/api")
app.include_router(execution_runs.router, prefix="/api")
app.include_router(starters.router, prefix="/api")
app.include_router(research_start.router, prefix="/api")
app.include_router(research_start.resource_catalog_router, prefix="/api")
app.include_router(artifacts.router, prefix="/api")
app.include_router(research_packages.router, prefix="/api")
app.include_router(resources.router, prefix="/api")
app.include_router(resource_saves.router, prefix="/api")
app.include_router(platform_inventory.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(agent_sessions.router, prefix="/api")
app.include_router(model_jobs.router, prefix="/api")
app.include_router(auth_routes.router, prefix="/api")
app.include_router(oauth_routes.router, prefix="/api")
app.include_router(quota_routes.router, prefix="/api")
app.include_router(internal.router)
app.include_router(skill_routes.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(admin_quota.router, prefix="/api")
app.include_router(admin_users.router, prefix="/api")
app.include_router(contributions.router, prefix="/api")
app.include_router(community.router, prefix="/api")
app.include_router(audit_routes.router, prefix="/api")
