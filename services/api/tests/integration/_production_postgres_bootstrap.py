"""Production-consistent PostgreSQL bootstrap helpers for integration tests.

These helpers create only the tables that API startup creates before the ordered
migration runner begins. Full-chain PostgreSQL integration tests should reuse
this module instead of maintaining partial bootstrap sequences independently.
"""

from __future__ import annotations

from typing import Any

from app.modules.audit_repository import _TABLE_SQL as AUDIT_TABLE_SQL
from app.modules.contribution_repository import _TABLE_SQL as CONTRIBUTION_TABLE_SQL
from app.modules.password_reset_repository import _TABLE_SQL as PASSWORD_RESET_TABLE_SQL
from app.modules.postgres_migrations import apply_postgres_migrations
from app.modules.task_repository import TASKS_TABLE_SQL
from app.modules.user_repository import _USERS_TABLE_SQL, _WORKSPACES_TABLE_SQL


def bootstrap_production_migration_prerequisites(cursor: Any) -> None:
    """Create the same migration prerequisites that API startup creates first."""

    cursor.execute(_USERS_TABLE_SQL)
    cursor.execute(_WORKSPACES_TABLE_SQL)
    cursor.execute(AUDIT_TABLE_SQL)
    cursor.execute(PASSWORD_RESET_TABLE_SQL)
    cursor.execute(CONTRIBUTION_TABLE_SQL)
    cursor.execute(TASKS_TABLE_SQL)


def bootstrap_production_postgres_schema(cursor: Any) -> None:
    """Create production prerequisites, then run the ordered migrations."""

    bootstrap_production_migration_prerequisites(cursor)
    apply_postgres_migrations(cursor)
