"""
Optional dev-only bootstrap: map fixture recorded runs into the in-memory task store.

Not used at runtime in production. Enable with OPENAIR_DEV_SEED_FIXTURES=1.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config import settings

if TYPE_CHECKING:
    from app.modules.task_store import TaskStore

TaskDict = dict[str, Any]


from app.modules.repo_paths import repo_root


def _default_recorded_runs_root() -> Path:
    return repo_root() / "fixtures" / "recorded_runs"


def seed_store_from_fixtures(store: TaskStore) -> int:
    """Load fixture tasks into store when dev seed is enabled. Returns count seeded."""
    if not settings.dev_seed_fixtures:
        return 0

    from app.modules.recorded_run_adapter import load_all_recorded_run_tasks

    runs_root = Path(settings.dev_fixtures_dir) if settings.dev_fixtures_dir else _default_recorded_runs_root()
    mocks_dir = repo_root() / "mocks" / "tasks"
    tasks = load_all_recorded_run_tasks(runs_root=runs_root, mocks_dir=mocks_dir)
    store.register_fixture_seed_task_ids(set(tasks.keys()))
    return store.seed_tasks(tasks)
