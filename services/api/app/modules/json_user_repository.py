"""JSON file persistence for users & workspaces (dev / isolated harness)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEV_EMAIL = "dev@openair.dev"
DEV_USER_ID = "u_dev_openair"
DEV_WORKSPACE_ID = "ws_dev_personal"


class JsonUserRepository:
    def __init__(self, users_dir: Path, workspaces_dir: Path) -> None:
        self._users_dir = users_dir
        self._workspaces_dir = workspaces_dir

    def bootstrap(self) -> None:
        self._users_dir.mkdir(parents=True, exist_ok=True)
        self._workspaces_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _write_json(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("failed to read json user store file %s", path, exc_info=True)
            return None
        return raw if isinstance(raw, dict) else None

    def _safe_read(self, base_dir: Path, record_id: str) -> dict[str, Any] | None:
        """Read a JSON record by id, guarding against path traversal."""
        target = (base_dir / f"{record_id}.json").resolve()
        if not str(target).startswith(str(base_dir.resolve())):
            logger.warning("path traversal blocked for id=%r in %s", record_id, base_dir)
            return None
        return self._read_json(target)

    def _iter_users(self) -> list[dict[str, Any]]:
        users: list[dict[str, Any]] = []
        for path in sorted(self._users_dir.glob("*.json")):
            user = self._read_json(path)
            if user:
                users.append(user)
        return users

    def _iter_workspaces(self) -> list[dict[str, Any]]:
        workspaces: list[dict[str, Any]] = []
        for path in sorted(self._workspaces_dir.glob("*.json")):
            workspace = self._read_json(path)
            if workspace:
                workspaces.append(workspace)
        return workspaces

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
        existing = self.get_user_by_email(email)
        if existing:
            raise ValueError(f"user with email {email!r} already exists")
        existing_by_username = self.get_user_by_username(username)
        if existing_by_username:
            raise ValueError(f"user with username {username!r} already exists")

        uid = user_id
        if not uid and email == DEV_EMAIL:
            uid = DEV_USER_ID
        if not uid:
            uid = f"u_{uuid.uuid4().hex[:12]}"

        existing_by_id = self.get_user_by_id(uid)
        if existing_by_id:
            return existing_by_id

        user: dict[str, Any] = {
            "user_id": uid,
            "email": email,
            "username": username,
            "hashed_password": hashed_password,
            "display_name": display_name,
            "is_active": bool(is_active),
            "role": "user",
            "quota_tier": "trial",
            "bonus_quota": 0,
            "period_used": 0,
            "period_start": None,
            "quota_alert_threshold_pct": 80,
            "created_at": self._now_iso(),
            "updated_at": self._now_iso(),
        }
        self._write_json(self._users_dir / f"{uid}.json", user)
        return user

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        for user in self._iter_users():
            if user.get("email") == email:
                return user
        return None

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        return self._safe_read(self._users_dir, user_id)

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        for user in self._iter_users():
            if user.get("username") == username:
                return user
        return None

    def update_user_quota_fields(self, user_id: str, **fields: Any) -> dict[str, Any] | None:
        """Update whitelisted quota/role fields (mirrors UserRepository)."""
        from app.modules.user_repository import QUOTA_MUTABLE_FIELDS

        unknown = set(fields) - QUOTA_MUTABLE_FIELDS
        if unknown:
            raise ValueError(f"fields not mutable via update_user_quota_fields: {sorted(unknown)}")
        user = self.get_user_by_id(user_id)
        if not user:
            return None
        if fields:
            user.update(fields)
            user["updated_at"] = self._now_iso()
            self._write_json(self._users_dir / f"{user_id}.json", user)
        return user

    def increment_bonus_quota(self, user_id: str, delta: int) -> dict[str, Any] | None:
        user = self.get_user_by_id(user_id)
        if not user:
            return None
        user["bonus_quota"] = int(user.get("bonus_quota") or 0) + delta
        user["updated_at"] = self._now_iso()
        self._write_json(self._users_dir / f"{user_id}.json", user)
        return user

    def apply_usage_settlement(self, user_id: str, amount: int, period_limit: int) -> dict[str, Any] | None:
        """Dev-only settle mirror (read-modify-write; not cross-process atomic)."""
        user = self.get_user_by_id(user_id)
        if not user:
            return None
        old_used = int(user.get("period_used") or 0)
        new_used = min(old_used + amount, period_limit)
        overflow = max(0, (old_used + amount) - period_limit)
        user["period_used"] = new_used
        user["bonus_quota"] = int(user.get("bonus_quota") or 0) - overflow
        user["updated_at"] = self._now_iso()
        self._write_json(self._users_dir / f"{user_id}.json", user)
        return user

    def list_users(self, *, query: str | None = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        users = self._iter_users()
        if query:
            q = query.lower()
            users = [u for u in users if q in str(u.get("email", "")).lower() or q in str(u.get("username", "")).lower()]
        users.sort(key=lambda u: str(u.get("created_at") or ""), reverse=True)
        return users[offset : offset + limit]

    def set_is_active(self, user_id: str, is_active: bool) -> dict[str, Any] | None:
        user = self.get_user_by_id(user_id)
        if not user:
            return None
        user["is_active"] = bool(is_active)
        user["updated_at"] = self._now_iso()
        self._write_json(self._users_dir / f"{user_id}.json", user)
        return user

    def update_password(self, user_id: str, hashed_password: str) -> dict[str, Any] | None:
        user = self.get_user_by_id(user_id)
        if not user:
            return None
        user["hashed_password"] = hashed_password
        user["updated_at"] = self._now_iso()
        self._write_json(self._users_dir / f"{user_id}.json", user)
        return user

    def update_user_prefs(self, user_id: str, **fields: Any) -> dict[str, Any] | None:
        from app.modules.user_repository import USER_PREF_MUTABLE_FIELDS

        unknown = set(fields) - USER_PREF_MUTABLE_FIELDS
        if unknown:
            raise ValueError(f"fields not mutable via update_user_prefs: {sorted(unknown)}")
        user = self.get_user_by_id(user_id)
        if not user:
            return None
        if fields:
            user.update(fields)
            user["updated_at"] = self._now_iso()
            self._write_json(self._users_dir / f"{user_id}.json", user)
        return user

    def create_workspace(
        self,
        *,
        workspace_id: str | None = None,
        name: str,
        owner_id: str,
        is_personal: bool = True,
    ) -> dict[str, Any]:
        existing = self.get_workspace_by_id(workspace_id) if workspace_id else None
        if existing:
            return existing

        wid = workspace_id
        if not wid and owner_id == DEV_USER_ID and is_personal:
            wid = DEV_WORKSPACE_ID
        if not wid:
            wid = f"ws_{uuid.uuid4().hex[:12]}"

        workspace: dict[str, Any] = {
            "workspace_id": wid,
            "name": name,
            "owner_id": owner_id,
            "is_personal": is_personal,
            "created_at": self._now_iso(),
        }
        self._write_json(self._workspaces_dir / f"{wid}.json", workspace)
        return workspace

    def get_user_workspaces(self, owner_id: str) -> list[dict[str, Any]]:
        workspaces = [ws for ws in self._iter_workspaces() if ws.get("owner_id") == owner_id]
        workspaces.sort(key=lambda ws: str(ws.get("created_at") or ""))
        return workspaces

    def get_workspace_by_id(self, workspace_id: str) -> dict[str, Any] | None:
        return self._safe_read(self._workspaces_dir, workspace_id)
