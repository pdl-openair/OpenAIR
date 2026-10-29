"""Quota adjustment audit trail (S03-14-04).

Append-only record of admin operations that change a user's quota (one-time
bonus or tier change). Postgres table ``openair_quota_adjustments`` is created
by migration 20260731_08; a JSONL dev fallback mirrors the interface.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, TypedDict, Union

import psycopg
from psycopg.rows import dict_row

from app.config import settings
from app.modules.repo_paths import repo_root

logger = logging.getLogger(__name__)

QuotaAdjustmentBackend = Union["QuotaAdjustmentRepository", "JsonQuotaAdjustmentRepository"]

_INSERT_ADJUSTMENT_SQL = """
INSERT INTO openair_quota_adjustments (
    adjustment_id, user_id, adjust_type, delta, old_tier, new_tier, reason, operator_id
) VALUES (
    %(adjustment_id)s, %(user_id)s, %(adjust_type)s, %(delta)s, %(old_tier)s,
    %(new_tier)s, %(reason)s, %(operator_id)s
)
"""


class QuotaAdjustment(TypedDict, total=False):
    adjustment_id: str
    user_id: str
    adjust_type: str  # bonus_quota | tier_change
    delta: int | None
    old_tier: str | None
    new_tier: str | None
    reason: str
    operator_id: str


def _new_adjustment_id() -> str:
    return f"adj_{uuid.uuid4().hex[:16]}"


class QuotaAdjustmentRepository:
    """Postgres-backed append-only audit trail."""

    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection[Any]]:
        conninfo = (
            f"host={settings.postgres_host} port={settings.postgres_port} "
            f"dbname={settings.postgres_database} user={settings.postgres_user}"
        )
        conn = psycopg.connect(conninfo, password=settings.postgres_password, row_factory=dict_row)  # type: ignore[arg-type]
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def record(self, adjustment: QuotaAdjustment) -> dict[str, Any]:
        adj_id = adjustment.get("adjustment_id") or _new_adjustment_id()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    _INSERT_ADJUSTMENT_SQL,
                    {
                        "adjustment_id": adj_id,
                        "user_id": adjustment["user_id"],
                        "adjust_type": adjustment["adjust_type"],
                        "delta": adjustment.get("delta"),
                        "old_tier": adjustment.get("old_tier"),
                        "new_tier": adjustment.get("new_tier"),
                        "reason": adjustment["reason"],
                        "operator_id": adjustment["operator_id"],
                    },
                )
                cur.execute(
                    "SELECT * FROM openair_quota_adjustments WHERE adjustment_id = %s",
                    (adj_id,),
                )
                return cur.fetchone()

    def list_for_user(self, user_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM openair_quota_adjustments
                     WHERE user_id = %s
                     ORDER BY created_at DESC LIMIT %s
                    """,
                    (user_id, limit),
                )
                return cur.fetchall()


class JsonQuotaAdjustmentRepository:
    """JSONL dev fallback: one append-only file per user under data/quota_adjustments/."""

    def __init__(self, adjustments_dir: Path) -> None:
        self._dir = adjustments_dir

    def bootstrap(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, user_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in user_id)
        return self._dir / f"{safe}.jsonl"

    def record(self, adjustment: QuotaAdjustment) -> dict[str, Any]:
        entry = dict(adjustment)
        entry["adjustment_id"] = entry.get("adjustment_id") or _new_adjustment_id()
        entry["created_at"] = datetime.now(timezone.utc).isoformat()
        path = self._path_for(entry["user_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def list_for_user(self, user_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        path = self._path_for(user_id)
        if not path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        return rows[:limit]


def get_quota_adjustment_repository() -> QuotaAdjustmentBackend:
    if settings.persistence_backend == "postgres":
        return QuotaAdjustmentRepository()
    if settings.is_production:
        raise RuntimeError(
            "JSON quota adjustment store is not supported in production; "
            "set OPENAIR_PERSISTENCE_BACKEND=postgres"
        )
    repo = JsonQuotaAdjustmentRepository(adjustments_dir=repo_root() / "data" / "quota_adjustments")
    repo.bootstrap()
    return repo
