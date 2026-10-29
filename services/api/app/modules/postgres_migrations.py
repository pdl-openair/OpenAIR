"""Small, ordered PostgreSQL migration runner for API-owned schema changes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.modules.repo_paths import api_service_root


MIGRATIONS_DIR = api_service_root() / "migrations"


def apply_postgres_migrations(cur: Any) -> None:
    cur.execute(
        """CREATE TABLE IF NOT EXISTS openair_schema_migrations (
           version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
    )
    cur.execute("SELECT version FROM openair_schema_migrations")
    applied = {row["version"] for row in cur.fetchall()}
    # Down migrations are versioned alongside their up migration but are never
    # auto-applied at application startup.
    for path in sorted(path for path in MIGRATIONS_DIR.glob("*.sql") if not path.name.endswith("_down.sql")):
        if path.name in applied:
            continue
        cur.execute(path.read_text(encoding="utf-8"))
        cur.execute("INSERT INTO openair_schema_migrations(version) VALUES (%s)", (path.name,))
