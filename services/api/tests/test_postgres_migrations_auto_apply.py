"""Directed tests for OPENAIR_POSTGRES_MIGRATIONS_AUTO_APPLY.

Never connects to a real PostgreSQL instance and never starts API/worker.
"""

from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest

from app.config import Settings, _env_truthy
from app.modules.durable_execution import DurableExecutionRepository

MAIN_PATH = Path(__file__).resolve().parents[1] / "app" / "main.py"


class MigrationGuardError(RuntimeError):
    """Hard failure if migration apply or directory scan is invoked."""


class _BlockedMigrationsDir:
    def __init__(self, real: Any, calls: dict[str, int]) -> None:
        self._real = real
        self._calls = calls

    def glob(self, pattern: str):
        self._calls["glob"] += 1
        raise MigrationGuardError(f"migration directory scan must not run (glob {pattern!r})")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


@contextmanager
def migration_hard_fail_spies() -> Iterator[dict[str, int]]:
    """Replace apply + MIGRATIONS_DIR.glob with hard failures."""
    import app.modules.postgres_migrations as postgres_migrations

    calls = {"apply": 0, "glob": 0}
    original_apply = postgres_migrations.apply_postgres_migrations
    original_dir = postgres_migrations.MIGRATIONS_DIR

    def _blocked_apply(*_args: Any, **_kwargs: Any) -> None:
        calls["apply"] += 1
        raise MigrationGuardError("apply_postgres_migrations must not be called")

    postgres_migrations.apply_postgres_migrations = _blocked_apply  # type: ignore[assignment]
    postgres_migrations.MIGRATIONS_DIR = _BlockedMigrationsDir(original_dir, calls)
    try:
        yield calls
    finally:
        postgres_migrations.apply_postgres_migrations = original_apply  # type: ignore[assignment]
        postgres_migrations.MIGRATIONS_DIR = original_dir


@contextmanager
def fake_pg_connection(repo: DurableExecutionRepository) -> Iterator[MagicMock]:
    """Stub _connect so bootstrap never opens a real database."""
    cursor = MagicMock(name="cursor")
    conn = MagicMock(name="connection")
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = None

    @contextmanager
    def _connect() -> Iterator[Any]:
        yield conn

    original = repo._connect
    repo._connect = _connect  # type: ignore[method-assign]
    try:
        yield cursor
    finally:
        repo._connect = original  # type: ignore[method-assign]


def test_env_truthy_unset_defaults_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAIR_POSTGRES_MIGRATIONS_AUTO_APPLY", raising=False)
    assert _env_truthy("OPENAIR_POSTGRES_MIGRATIONS_AUTO_APPLY", default=True) is True


def test_settings_unset_defaults_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings wires through `_env_truthy(..., default=True)` (class-body eval)."""
    monkeypatch.delenv("OPENAIR_POSTGRES_MIGRATIONS_AUTO_APPLY", raising=False)
    assert _env_truthy("OPENAIR_POSTGRES_MIGRATIONS_AUTO_APPLY", default=True) is True
    assert hasattr(Settings, "postgres_migrations_auto_apply")
    assert isinstance(Settings().postgres_migrations_auto_apply, bool)


def test_settings_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAIR_POSTGRES_MIGRATIONS_AUTO_APPLY", "false")
    assert _env_truthy("OPENAIR_POSTGRES_MIGRATIONS_AUTO_APPLY", default=True) is False


def test_settings_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAIR_POSTGRES_MIGRATIONS_AUTO_APPLY", "true")
    assert _env_truthy("OPENAIR_POSTGRES_MIGRATIONS_AUTO_APPLY", default=True) is True


def test_bootstrap_false_skips_apply_but_still_connects(monkeypatch: pytest.MonkeyPatch) -> None:
    connected: list[bool] = []
    cursor_entered: list[bool] = []

    def _hard_fail_apply(*_args: Any, **_kwargs: Any) -> None:
        raise MigrationGuardError("apply_postgres_migrations must not be called")

    monkeypatch.setattr(
        "app.modules.durable_execution.apply_postgres_migrations",
        _hard_fail_apply,
    )

    repo = DurableExecutionRepository()
    cursor = MagicMock(name="cursor")
    conn = MagicMock(name="connection")

    @contextmanager
    def _cursor_cm():
        cursor_entered.append(True)
        yield cursor

    conn.cursor.side_effect = lambda: _cursor_cm()

    @contextmanager
    def _connect() -> Iterator[Any]:
        connected.append(True)
        yield conn

    repo._connect = _connect  # type: ignore[method-assign]

    with migration_hard_fail_spies() as scanner_calls:
        repo.bootstrap(False)

    assert connected == [True]
    assert cursor_entered == [True]
    assert scanner_calls == {"apply": 0, "glob": 0}
    cursor.execute.assert_not_called()


def test_default_bootstrap_still_applies_migrations(monkeypatch: pytest.MonkeyPatch) -> None:
    apply_calls: list[Any] = []
    monkeypatch.setattr(
        "app.modules.durable_execution.apply_postgres_migrations",
        lambda cur: apply_calls.append(cur),
    )

    repo = DurableExecutionRepository()
    with fake_pg_connection(repo) as cursor:
        repo.bootstrap()  # default auto_apply_migrations=True

    assert apply_calls == [cursor]


def test_explicit_true_applies_migrations(monkeypatch: pytest.MonkeyPatch) -> None:
    apply_calls: list[Any] = []
    monkeypatch.setattr(
        "app.modules.durable_execution.apply_postgres_migrations",
        lambda cur: apply_calls.append(cur),
    )

    repo = DurableExecutionRepository()
    with fake_pg_connection(repo) as cursor:
        repo.bootstrap(True)

    assert apply_calls == [cursor]


def test_main_source_registers_auto_apply_banner_and_helper_wiring() -> None:
    """Source contract for main.py without importing full FastAPI app (env may lack jose)."""
    source = MAIN_PATH.read_text(encoding="utf-8")
    assert "migrations_auto_apply=" in source
    assert "postgres_migrations_auto_apply" in source
    assert "def _bootstrap_postgres_repositories" in source
    assert "DurableExecutionRepository().bootstrap(auto_apply_migrations)" in source
    assert "_bootstrap_postgres_repositories(" in source
    assert "auto_apply_migrations=settings.postgres_migrations_auto_apply" in source
    assert "password=" not in source.split("migrations_auto_apply=")[1][:120].lower()

    tree = ast.parse(source)
    helper_names = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_bootstrap_postgres_repositories"
    }
    assert helper_names == {"_bootstrap_postgres_repositories"}


def test_config_source_defaults_auto_apply_true() -> None:
    config_path = Path(__file__).resolve().parents[1] / "app" / "config.py"
    source = config_path.read_text(encoding="utf-8")
    assert 'OPENAIR_POSTGRES_MIGRATIONS_AUTO_APPLY", default=True' in source
    assert "postgres_migrations_auto_apply" in source
