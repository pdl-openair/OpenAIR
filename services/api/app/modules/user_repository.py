"""User & workspace persistence — raw psycopg, same pattern as task_repository.py."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Union

import psycopg
from psycopg.rows import dict_row

from app.config import settings
from app.modules.repo_paths import repo_root

UserRepositoryBackend = Union["UserRepository", "JsonUserRepository"]

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_USERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS openair_users (
    user_id         TEXT PRIMARY KEY,
    email           TEXT UNIQUE NOT NULL,
    username        TEXT UNIQUE NOT NULL,
    hashed_password TEXT,
    display_name    TEXT,
    is_active       BOOLEAN DEFAULT FALSE,
    role            TEXT NOT NULL DEFAULT 'user',
    quota_tier      TEXT NOT NULL DEFAULT 'trial',
    bonus_quota     BIGINT NOT NULL DEFAULT 0,
    period_used     BIGINT NOT NULL DEFAULT 0,
    period_start    TIMESTAMPTZ,
    quota_alert_threshold_pct INTEGER DEFAULT 80,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
"""

# Fields mutable through update_user_quota_fields (Sprint-03 quota). Anything
# outside this whitelist is rejected to keep account fields write-controlled.
QUOTA_MUTABLE_FIELDS = frozenset({"role", "quota_tier", "bonus_quota", "period_used", "period_start"})

# User-self-service preference fields (S04-01). Never includes tier/bonus.
USER_PREF_MUTABLE_FIELDS = frozenset({"quota_alert_threshold_pct", "display_name"})

_WORKSPACES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS openair_workspaces (
    workspace_id TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    owner_id     TEXT NOT NULL REFERENCES openair_users(user_id),
    is_personal  BOOLEAN DEFAULT TRUE,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_workspaces_owner ON openair_workspaces(owner_id);
"""

# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

_INSERT_USER_SQL = """
INSERT INTO openair_users (user_id, email, username, hashed_password, display_name, is_active)
VALUES (%(user_id)s, %(email)s, %(username)s, %(hashed_password)s, %(display_name)s, %(is_active)s)
ON CONFLICT (user_id) DO NOTHING
"""

_INSERT_WORKSPACE_SQL = """
INSERT INTO openair_workspaces (workspace_id, name, owner_id, is_personal)
VALUES (%(workspace_id)s, %(name)s, %(owner_id)s, %(is_personal)s)
ON CONFLICT (workspace_id) DO NOTHING
"""

_SELECT_USER_BY_EMAIL = "SELECT * FROM openair_users WHERE email = %s"
_SELECT_USER_BY_ID = "SELECT * FROM openair_users WHERE user_id = %s"
_SELECT_USER_BY_USERNAME = "SELECT * FROM openair_users WHERE username = %s"
_SELECT_WORKSPACES_BY_OWNER = "SELECT * FROM openair_workspaces WHERE owner_id = %s ORDER BY created_at"
_SELECT_WORKSPACE_BY_ID = "SELECT * FROM openair_workspaces WHERE workspace_id = %s"


class UserRepository:
    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection[Any]]:
        # password is passed as a keyword arg, NOT stringified into the conninfo
        # string — so it never appears if the connection params are logged or repr'd.
        conninfo = (
            f"host={settings.postgres_host} port={settings.postgres_port} "
            f"dbname={settings.postgres_database} user={settings.postgres_user}"
        )
        # dict_row vs TupleRow is a known psycopg type-stub limitation; password stays a kwarg.
        conn = psycopg.connect(conninfo, password=settings.postgres_password, row_factory=dict_row)  # type: ignore[arg-type]
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------

    def bootstrap(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_USERS_TABLE_SQL)
                cur.execute(_WORKSPACES_TABLE_SQL)
                # S04-01: prefer additive ALTER so older DBs pick up prefs without waiting on migration order
                cur.execute(
                    "ALTER TABLE openair_users "
                    "ADD COLUMN IF NOT EXISTS quota_alert_threshold_pct INTEGER DEFAULT 80"
                )
                # S04-02: OAuth-only accounts may have no local password
                cur.execute(
                    "ALTER TABLE openair_users "
                    "ALTER COLUMN hashed_password DROP NOT NULL"
                )
                # S04-04: public registration stays inactive until an admin activates
                cur.execute(
                    "ALTER TABLE openair_users "
                    "ALTER COLUMN is_active SET DEFAULT FALSE"
                )

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def create_user(
        self,
        *,
        user_id: str | None = None,
        email: str,
        username: str,
        hashed_password: str | None,
        display_name: str | None = None,
        is_active: bool = False,
    ) -> dict[str, Any]:
        uid = user_id or f"u_{uuid.uuid4().hex[:12]}"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    _INSERT_USER_SQL,
                    {
                        "user_id": uid,
                        "email": email,
                        "username": username,
                        "hashed_password": hashed_password,
                        "display_name": display_name,
                        "is_active": bool(is_active),
                    },
                )
        return self.get_user_by_id(uid)  # type: ignore[return-value]

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_SELECT_USER_BY_EMAIL, (email,))
                return cur.fetchone()

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_SELECT_USER_BY_ID, (user_id,))
                return cur.fetchone()

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_SELECT_USER_BY_USERNAME, (username,))
                return cur.fetchone()

    def update_user_quota_fields(self, user_id: str, **fields: Any) -> dict[str, Any] | None:
        """Update whitelisted quota/role fields; returns the updated user row."""
        unknown = set(fields) - QUOTA_MUTABLE_FIELDS
        if unknown:
            raise ValueError(f"fields not mutable via update_user_quota_fields: {sorted(unknown)}")
        if not fields:
            return self.get_user_by_id(user_id)
        assignments = ", ".join(f"{key} = %({key})s" for key in sorted(fields))
        params = {**fields, "user_id": user_id}
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE openair_users SET {assignments}, updated_at = NOW() WHERE user_id = %(user_id)s",
                    params,
                )
        return self.get_user_by_id(user_id)

    def increment_bonus_quota(self, user_id: str, delta: int) -> dict[str, Any] | None:
        """Atomically add (or subtract, when delta<0) bonus quota; returns updated row."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE openair_users
                       SET bonus_quota = bonus_quota + %(delta)s, updated_at = NOW()
                     WHERE user_id = %(user_id)s
                    """,
                    {"delta": delta, "user_id": user_id},
                )
        return self.get_user_by_id(user_id)

    def apply_usage_settlement(self, user_id: str, amount: int, period_limit: int) -> dict[str, Any] | None:
        """Charge ``amount`` tokens: fill period quota first, spill overflow to bonus.

        Single atomic UPDATE. Both SET expressions read the OLD row values, so
        the spill (bonus -= overflow) is consistent with the period increment.
        Bonus may go negative under concurrent bursts (explainable; S03-14-01 §5).
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE openair_users
                       SET period_used = LEAST(period_used + %(amount)s, %(period_limit)s),
                           bonus_quota = bonus_quota - GREATEST(0, (period_used + %(amount)s) - %(period_limit)s),
                           updated_at = NOW()
                     WHERE user_id = %(user_id)s
                    """,
                    {"amount": amount, "period_limit": period_limit, "user_id": user_id},
                )
        return self.get_user_by_id(user_id)

    def list_users(self, *, query: str | None = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """List users for admin views; optional email/username fuzzy match."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                if query:
                    cur.execute(
                        """
                        SELECT * FROM openair_users
                         WHERE email ILIKE %(q)s OR username ILIKE %(q)s
                         ORDER BY created_at DESC LIMIT %(limit)s OFFSET %(offset)s
                        """,
                        {"q": f"%{query}%", "limit": limit, "offset": offset},
                    )
                else:
                    cur.execute(
                        "SELECT * FROM openair_users ORDER BY created_at DESC LIMIT %s OFFSET %s",
                        (limit, offset),
                    )
                return cur.fetchall()

    def set_is_active(self, user_id: str, is_active: bool) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE openair_users
                       SET is_active = %(is_active)s, updated_at = NOW()
                     WHERE user_id = %(user_id)s
                    """,
                    {"user_id": user_id, "is_active": is_active},
                )
        return self.get_user_by_id(user_id)

    def update_password(self, user_id: str, hashed_password: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE openair_users
                       SET hashed_password = %(hashed_password)s, updated_at = NOW()
                     WHERE user_id = %(user_id)s
                    """,
                    {"user_id": user_id, "hashed_password": hashed_password},
                )
        return self.get_user_by_id(user_id)

    def update_user_prefs(self, user_id: str, **fields: Any) -> dict[str, Any] | None:
        """Update whitelisted self-service preference fields."""
        unknown = set(fields) - USER_PREF_MUTABLE_FIELDS
        if unknown:
            raise ValueError(f"fields not mutable via update_user_prefs: {sorted(unknown)}")
        if not fields:
            return self.get_user_by_id(user_id)
        assignments = ", ".join(f"{key} = %({key})s" for key in sorted(fields))
        params = {**fields, "user_id": user_id}
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE openair_users SET {assignments}, updated_at = NOW() WHERE user_id = %(user_id)s",
                    params,
                )
        return self.get_user_by_id(user_id)

    # ------------------------------------------------------------------
    # Workspaces
    # ------------------------------------------------------------------

    def create_workspace(
        self,
        *,
        workspace_id: str | None = None,
        name: str,
        owner_id: str,
        is_personal: bool = True,
    ) -> dict[str, Any]:
        wid = workspace_id or f"ws_{uuid.uuid4().hex[:12]}"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    _INSERT_WORKSPACE_SQL,
                    {
                        "workspace_id": wid,
                        "name": name,
                        "owner_id": owner_id,
                        "is_personal": is_personal,
                    },
                )
        return self.get_workspace_by_id(wid)  # type: ignore[return-value]

    def get_user_workspaces(self, owner_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_SELECT_WORKSPACES_BY_OWNER, (owner_id,))
                return cur.fetchall()

    def get_workspace_by_id(self, workspace_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_SELECT_WORKSPACE_BY_ID, (workspace_id,))
                return cur.fetchone()


def get_user_repository() -> UserRepositoryBackend:
    """Return the user/workspace backend for the active persistence mode."""
    if settings.persistence_backend == "postgres":
        return UserRepository()
    if settings.is_production:
        raise RuntimeError(
            "JSON user persistence is not supported in production; "
            "set OPENAIR_PERSISTENCE_BACKEND=postgres"
        )
    from app.modules.json_user_repository import JsonUserRepository

    root = repo_root()
    return JsonUserRepository(
        users_dir=root / "data" / "users",
        workspaces_dir=root / "data" / "workspaces",
    )
