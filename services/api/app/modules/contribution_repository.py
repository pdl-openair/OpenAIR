"""User contribution / public-publish review persistence (S04-01)."""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Union

import psycopg
from psycopg.rows import dict_row

from app.config import settings
from app.modules.repo_paths import repo_root

logger = logging.getLogger(__name__)

ContributionBackend = Union["ContributionRepository", "JsonContributionRepository"]

VALID_STATUSES = frozenset(
    {
        "private_ready",
        "pending_public_review",
        "approved_public",
        "rejected",
        "withdrawn",
    }
)
VALID_SYSTEM_CHECK_STATUSES = frozenset({"not_checked", "passed", "needs_attention"})

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS openair_contributions (
    contribution_id       TEXT PRIMARY KEY,
    owner_id              TEXT NOT NULL,
    workspace_id          TEXT,
    resource_type         TEXT NOT NULL DEFAULT 'skill',
    resource_ref          TEXT NOT NULL,
    title                 TEXT NOT NULL,
    description           TEXT NOT NULL DEFAULT '',
    domain                TEXT NOT NULL DEFAULT 'general',
    discovery_scene       TEXT NOT NULL DEFAULT 'research_materials',
    status                TEXT NOT NULL DEFAULT 'private_ready',
    system_check_status   TEXT NOT NULL DEFAULT 'not_checked',
    system_check_issues   JSONB NOT NULL DEFAULT '[]'::jsonb,
    review_notes          TEXT,
    reviewer_id           TEXT,
    reviewed_at           TIMESTAMPTZ,
    catalog_resource_id   TEXT,
    release_version       TEXT,
    published_at          TIMESTAMPTZ,
    published_manifest    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_contributions_owner_created
    ON openair_contributions (owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_contributions_status_created
    ON openair_contributions (status, created_at DESC);
"""


def _json_list(value: list[str] | None) -> str | None:
    return json.dumps(value, ensure_ascii=False) if value is not None else None


def _json_object(value: dict[str, Any] | None) -> str | None:
    return json.dumps(value, ensure_ascii=False) if value is not None else None


class ContributionRepository:
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

    def bootstrap(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_TABLE_SQL)

    def create(
        self,
        *,
        owner_id: str,
        workspace_id: str | None,
        resource_type: str,
        resource_ref: str,
        title: str,
        description: str = "",
        domain: str = "general",
        discovery_scene: str = "research_materials",
        status: str = "private_ready",
        system_check_status: str = "not_checked",
        system_check_issues: list[str] | None = None,
    ) -> dict[str, Any]:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid contribution status: {status}")
        if system_check_status not in VALID_SYSTEM_CHECK_STATUSES:
            raise ValueError(f"invalid system check status: {system_check_status}")
        cid = f"ctrb_{uuid.uuid4().hex[:12]}"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO openair_contributions
                        (contribution_id, owner_id, workspace_id, resource_type, resource_ref,
                         title, description, domain, discovery_scene, status,
                         system_check_status, system_check_issues)
                    VALUES
                        (%(contribution_id)s, %(owner_id)s, %(workspace_id)s, %(resource_type)s,
                         %(resource_ref)s, %(title)s, %(description)s, %(domain)s,
                         %(discovery_scene)s, %(status)s, %(system_check_status)s,
                         %(system_check_issues)s::jsonb)
                    RETURNING *
                    """,
                    {
                        "contribution_id": cid,
                        "owner_id": owner_id,
                        "workspace_id": workspace_id,
                        "resource_type": resource_type,
                        "resource_ref": resource_ref,
                        "title": title,
                        "description": description,
                        "domain": domain,
                        "discovery_scene": discovery_scene,
                        "status": status,
                        "system_check_status": system_check_status,
                        "system_check_issues": _json_list(system_check_issues or []),
                    },
                )
                return cur.fetchone()  # type: ignore[return-value]

    def get(self, contribution_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM openair_contributions WHERE contribution_id = %s", (contribution_id,))
                return cur.fetchone()

    def find_for_owner_resource(
        self,
        owner_id: str,
        resource_type: str,
        resource_ref: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM openair_contributions
                     WHERE owner_id = %s AND resource_type = %s AND resource_ref = %s
                     ORDER BY created_at DESC LIMIT 1
                    """,
                    (owner_id, resource_type, resource_ref),
                )
                return cur.fetchone()

    def list_for_owner(self, owner_id: str, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM openair_contributions
                     WHERE owner_id = %s
                     ORDER BY created_at DESC LIMIT %s OFFSET %s
                    """,
                    (owner_id, limit, offset),
                )
                return cur.fetchall()

    def list_by_status(self, status: str, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM openair_contributions
                     WHERE status = %s
                     ORDER BY created_at ASC LIMIT %s OFFSET %s
                    """,
                    (status, limit, offset),
                )
                return cur.fetchall()

    def list_public_catalog(self, *, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        return self.list_by_status("approved_public", limit=limit, offset=offset)

    def update_status(
        self,
        contribution_id: str,
        *,
        status: str,
        review_notes: str | None = None,
        reviewer_id: str | None = None,
        system_check_status: str | None = None,
        system_check_issues: list[str] | None = None,
        catalog_resource_id: str | None = None,
        release_version: str | None = None,
        published_manifest: dict[str, Any] | None = None,
        mark_published: bool = False,
        clear_review: bool = False,
    ) -> dict[str, Any] | None:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid contribution status: {status}")
        if system_check_status is not None and system_check_status not in VALID_SYSTEM_CHECK_STATUSES:
            raise ValueError(f"invalid system check status: {system_check_status}")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE openair_contributions
                       SET status = %(status)s,
                           review_notes = CASE
                               WHEN %(clear_review)s THEN NULL
                               WHEN %(review_notes)s::text IS NOT NULL THEN %(review_notes)s::text
                               ELSE review_notes
                           END,
                           reviewer_id = CASE
                               WHEN %(clear_review)s THEN NULL
                               WHEN %(reviewer_id)s::text IS NOT NULL THEN %(reviewer_id)s::text
                               ELSE reviewer_id
                           END,
                           reviewed_at = CASE
                               WHEN %(status)s IN ('approved_public', 'rejected') THEN NOW()
                               WHEN %(clear_review)s THEN NULL
                               ELSE reviewed_at
                           END,
                           system_check_status = COALESCE(%(system_check_status)s, system_check_status),
                           system_check_issues = COALESCE(%(system_check_issues)s::jsonb, system_check_issues),
                           catalog_resource_id = COALESCE(%(catalog_resource_id)s, catalog_resource_id),
                           release_version = COALESCE(%(release_version)s, release_version),
                           published_manifest = COALESCE(%(published_manifest)s::jsonb, published_manifest),
                           published_at = CASE WHEN %(mark_published)s THEN NOW() ELSE published_at END,
                           updated_at = NOW()
                     WHERE contribution_id = %(contribution_id)s
                    RETURNING *
                    """,
                    {
                        "contribution_id": contribution_id,
                        "status": status,
                        "review_notes": review_notes,
                        "reviewer_id": reviewer_id,
                        "system_check_status": system_check_status,
                        "system_check_issues": _json_list(system_check_issues),
                        "catalog_resource_id": catalog_resource_id,
                        "release_version": release_version,
                        "published_manifest": _json_object(published_manifest),
                        "mark_published": mark_published,
                        "clear_review": clear_review,
                    },
                )
                return cur.fetchone()


class JsonContributionRepository:
    def __init__(self, root: Path) -> None:
        self._dir = root / "data" / "contributions"
        self._dir.mkdir(parents=True, exist_ok=True)

    def bootstrap(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _write(self, row: dict[str, Any]) -> None:
        cid = row["contribution_id"]
        path = self._dir / f"{cid}.json"
        path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_all(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in self._dir.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(raw, dict):
                rows.append(raw)
        return rows

    def create(
        self,
        *,
        owner_id: str,
        workspace_id: str | None,
        resource_type: str,
        resource_ref: str,
        title: str,
        description: str = "",
        domain: str = "general",
        discovery_scene: str = "research_materials",
        status: str = "private_ready",
        system_check_status: str = "not_checked",
        system_check_issues: list[str] | None = None,
    ) -> dict[str, Any]:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid contribution status: {status}")
        if system_check_status not in VALID_SYSTEM_CHECK_STATUSES:
            raise ValueError(f"invalid system check status: {system_check_status}")
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "contribution_id": f"ctrb_{uuid.uuid4().hex[:12]}",
            "owner_id": owner_id,
            "workspace_id": workspace_id,
            "resource_type": resource_type,
            "resource_ref": resource_ref,
            "title": title,
            "description": description,
            "domain": domain,
            "discovery_scene": discovery_scene,
            "status": status,
            "system_check_status": system_check_status,
            "system_check_issues": list(system_check_issues or []),
            "review_notes": None,
            "reviewer_id": None,
            "reviewed_at": None,
            "catalog_resource_id": None,
            "release_version": None,
            "published_at": None,
            "published_manifest": {},
            "created_at": now,
            "updated_at": now,
        }
        self._write(row)
        return row

    def get(self, contribution_id: str) -> dict[str, Any] | None:
        path = self._dir / f"{contribution_id}.json"
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return raw if isinstance(raw, dict) else None

    def find_for_owner_resource(
        self,
        owner_id: str,
        resource_type: str,
        resource_ref: str,
    ) -> dict[str, Any] | None:
        rows = [
            row
            for row in self._read_all()
            if row.get("owner_id") == owner_id
            and row.get("resource_type") == resource_type
            and row.get("resource_ref") == resource_ref
        ]
        rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return rows[0] if rows else None

    def list_for_owner(self, owner_id: str, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        rows = [r for r in self._read_all() if r.get("owner_id") == owner_id]
        rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        return rows[offset : offset + limit]

    def list_by_status(self, status: str, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        rows = [r for r in self._read_all() if r.get("status") == status]
        rows.sort(key=lambda r: str(r.get("created_at") or ""))
        return rows[offset : offset + limit]

    def list_public_catalog(self, *, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        return self.list_by_status("approved_public", limit=limit, offset=offset)

    def update_status(
        self,
        contribution_id: str,
        *,
        status: str,
        review_notes: str | None = None,
        reviewer_id: str | None = None,
        system_check_status: str | None = None,
        system_check_issues: list[str] | None = None,
        catalog_resource_id: str | None = None,
        release_version: str | None = None,
        published_manifest: dict[str, Any] | None = None,
        mark_published: bool = False,
        clear_review: bool = False,
    ) -> dict[str, Any] | None:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid contribution status: {status}")
        if system_check_status is not None and system_check_status not in VALID_SYSTEM_CHECK_STATUSES:
            raise ValueError(f"invalid system check status: {system_check_status}")
        row = self.get(contribution_id)
        if not row:
            return None
        row["status"] = status
        if clear_review:
            row["review_notes"] = None
            row["reviewer_id"] = None
            row["reviewed_at"] = None
        if review_notes is not None:
            row["review_notes"] = review_notes
        if reviewer_id is not None:
            row["reviewer_id"] = reviewer_id
        if system_check_status is not None:
            row["system_check_status"] = system_check_status
        if system_check_issues is not None:
            row["system_check_issues"] = list(system_check_issues)
        if catalog_resource_id is not None:
            row["catalog_resource_id"] = catalog_resource_id
        if release_version is not None:
            row["release_version"] = release_version
        if published_manifest is not None:
            row["published_manifest"] = dict(published_manifest)
        if status in ("approved_public", "rejected"):
            row["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        if mark_published:
            row["published_at"] = datetime.now(timezone.utc).isoformat()
        row["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write(row)
        return row


_contrib_singleton: ContributionBackend | None = None


def get_contribution_repository() -> ContributionBackend:
    global _contrib_singleton
    if _contrib_singleton is not None:
        return _contrib_singleton
    if settings.persistence_backend == "postgres":
        _contrib_singleton = ContributionRepository()
    else:
        _contrib_singleton = JsonContributionRepository(repo_root())
    return _contrib_singleton
