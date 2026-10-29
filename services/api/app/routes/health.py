import os

from fastapi import APIRouter

from app.config import settings
from app.modules.runtime_version import runtime_version

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "authRequired": settings.auth_required,
        "runtimeVersion": runtime_version(),
    }


@router.get("/health/agent")
def health_agent() -> dict[str, str | bool | None]:
    """开发诊断：Claude CLI 解析与执行模式。"""
    cli: str | None = None
    cli_error: str | None = None
    try:
        from app.execution.claude_cli_resolver import resolve_claude_cli_path

        cli = resolve_claude_cli_path()
    except OSError as e:
        cli_error = str(e)

    runtime_boundary = "inline"
    if settings.runtime_type == "worker":
        runtime_boundary = "worker_stub"
    anthropic_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    anthropic_base = (os.environ.get("ANTHROPIC_BASE_URL") or "").strip()
    huiyan_key = (os.environ.get("LLM_PROVIDER_API_KEY") or "").strip()

    try:
        running_as_root = os.getuid() == 0
    except AttributeError:
        running_as_root = False

    return {
        "skillExecMode": settings.skill_exec_mode,
        "runtimeType": settings.runtime_type,
        "runtimeBoundary": runtime_boundary,
        "claudeCliPath": cli,
        "claudeCliError": cli_error,
        "openairClaudeCliPathEnv": os.environ.get("OPENAIR_CLAUDE_CLI_PATH"),
        "claudePermissionMode": settings.claude_permission_mode,
        "claudePermissionModeEnv": os.environ.get("OPENAIR_CLAUDE_PERMISSION_MODE"),
        "runningAsRoot": running_as_root,
        "anthropicApiKeyConfigured": bool(anthropic_key),
        "anthropicBaseUrlConfigured": bool(anthropic_base),
        "huiyanApiKeyConfigured": bool(huiyan_key),
        "openairClaudeModel": (os.environ.get("OPENAIR_CLAUDE_MODEL") or os.environ.get("ANTHROPIC_MODEL") or None),
    }


@router.get("/health/diagnostics")
def health_diagnostics() -> dict:
    """Cloud-07: Runtime diagnostics — config summary, cost counters, and task counts."""
    from app.main import get_cost_snapshot

    cost = get_cost_snapshot()
    task_count = 0
    try:
        from app.modules.task_store import store

        task_count = len(store.list_tasks())
    except Exception:
        pass

    return {
        "env": settings.env,
        "authRequired": settings.auth_required,
        "persistenceBackend": settings.persistence_backend,
        "runtimeType": settings.runtime_type,
        "skillExecMode": settings.skill_exec_mode,
        "logLevel": settings.log_level,
        "logFormat": settings.log_format,
        "costTracking": settings.cost_tracking,
        "taskCount": task_count,
        "costCounters": cost,
        "runtimeVersion": runtime_version(),
    }
