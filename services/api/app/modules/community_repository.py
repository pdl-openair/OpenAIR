"""Community publication + comment repository (C1).

A community publication marks a live task/artifact as publicly forkable/readable
across workspaces. Comments are an append-only discussion layer on publications.

Both tables are created by migration ``20260811_01_community_publications.sql``.
The connection pattern mirrors ``ContributionRepository``.
"""
from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from app.config import settings

_log = logging.getLogger(__name__)

VALID_RESOURCE_TYPES = ("task", "artifact")


class CommunityRepository:
    """Postgres repository for community publications and comments."""

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

    # ---- publications ----

    def publish(
        self,
        *,
        resource_type: str,
        resource_id: str,
        publisher_owner_id: str,
        publisher_workspace_id: str,
        title: str,
        summary: str | None = None,
        recommended_granularity: str = "full",
        manifest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Publish a resource. Idempotent on (resource_type, resource_id).

        Re-publishing by the same owner refreshes title/summary/manifest/granularity;
        a resource already published by another owner raises (caller maps to 409).
        """
        if resource_type not in VALID_RESOURCE_TYPES:
            raise ValueError(f"invalid community resource_type: {resource_type}")
        if recommended_granularity not in ("full", "template"):
            recommended_granularity = "full"
        publication_id = f"pub_{uuid.uuid4().hex[:12]}"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH upserted AS (
                        INSERT INTO openair_community_publications
                            (publication_id, resource_type, resource_id,
                             publisher_owner_id, publisher_workspace_id,
                             title, summary, recommended_granularity, published_manifest)
                        VALUES
                            (%(publication_id)s, %(resource_type)s, %(resource_id)s,
                             %(publisher_owner_id)s, %(publisher_workspace_id)s,
                             %(title)s, %(summary)s, %(recommended_granularity)s, %(published_manifest)s::jsonb)
                        ON CONFLICT (resource_type, resource_id) DO UPDATE SET
                            title = EXCLUDED.title,
                            summary = EXCLUDED.summary,
                            recommended_granularity = EXCLUDED.recommended_granularity,
                            published_manifest = EXCLUDED.published_manifest
                        RETURNING *
                    )
                    SELECT u.*, COALESCE(usr.display_name, usr.username, u.publisher_owner_id) AS publisher_display_name
                    FROM upserted u
                    LEFT JOIN openair_users usr ON usr.user_id = u.publisher_owner_id
                    """,
                    {
                        "publication_id": publication_id,
                        "resource_type": resource_type,
                        "resource_id": resource_id,
                        "publisher_owner_id": publisher_owner_id,
                        "publisher_workspace_id": publisher_workspace_id,
                        "title": title,
                        "summary": summary,
                        "recommended_granularity": recommended_granularity,
                        "published_manifest": json.dumps(manifest or {}),
                    },
                )
                row = cur.fetchone()
                assert row is not None
                return row

    def list_publications(
        self,
        *,
        resource_type: str | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """Cross-user list of published resources, newest first."""
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if resource_type in VALID_RESOURCE_TYPES:
            clauses.append("resource_type = %(resource_type)s")
            params["resource_type"] = resource_type
        if q and q.strip():
            clauses.append("(title ILIKE %(q)s OR COALESCE(summary,'') ILIKE %(q)s)")
            params["q"] = f"%{q.strip()}%"
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        offset = (page - 1) * page_size
        params["limit"] = page_size
        params["offset"] = offset
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS c FROM openair_community_publications {where}", params)
                total = int((cur.fetchone() or {}).get("c", 0))
                cur.execute(
                    f"""
                    SELECT p.*, COALESCE(u.display_name, u.username, p.publisher_owner_id) AS publisher_display_name
                    FROM openair_community_publications p
                    LEFT JOIN openair_users u ON u.user_id = p.publisher_owner_id
                    {where}
                    ORDER BY p.created_at DESC NULLS LAST
                    LIMIT %(limit)s OFFSET %(offset)s
                    """,
                    params,
                )
                rows = cur.fetchall()
        return rows, total

    def get_publication(self, publication_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT p.*, COALESCE(u.display_name, u.username, p.publisher_owner_id) AS publisher_display_name
                       FROM openair_community_publications p
                       LEFT JOIN openair_users u ON u.user_id = p.publisher_owner_id
                       WHERE p.publication_id = %s""",
                    (publication_id,),
                )
                return cur.fetchone()

    def find_publication(self, resource_type: str, resource_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM openair_community_publications
                    WHERE resource_type = %s AND resource_id = %s
                    """,
                    (resource_type, resource_id),
                )
                return cur.fetchone()

    def comment_counts(self, publication_ids: list[str]) -> dict[str, int]:
        if not publication_ids:
            return {}
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT publication_id, COUNT(*) AS c
                    FROM openair_community_comments
                    WHERE publication_id = ANY(%s)
                    GROUP BY publication_id
                    """,
                    (publication_ids,),
                )
                return {row["publication_id"]: int(row["c"]) for row in cur.fetchall()}

    # ---- comments ----

    def add_comment(
        self,
        *,
        publication_id: str,
        author_owner_id: str,
        author_workspace_id: str,
        body: str,
        parent_comment_id: str | None = None,
    ) -> dict[str, Any]:
        comment_id = f"cmc_{uuid.uuid4().hex[:12]}"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH inserted AS (
                        INSERT INTO openair_community_comments
                            (comment_id, publication_id, author_owner_id,
                             author_workspace_id, body, parent_comment_id)
                        VALUES
                            (%(comment_id)s, %(publication_id)s, %(author_owner_id)s,
                             %(author_workspace_id)s, %(body)s, %(parent_comment_id)s)
                        RETURNING *
                    )
                    SELECT i.*, COALESCE(u.display_name, u.username, i.author_owner_id) AS author_display_name
                    FROM inserted i
                    LEFT JOIN openair_users u ON u.user_id = i.author_owner_id
                    """,
                    {
                        "comment_id": comment_id,
                        "publication_id": publication_id,
                        "author_owner_id": author_owner_id,
                        "author_workspace_id": author_workspace_id,
                        "body": body,
                        "parent_comment_id": parent_comment_id,
                    },
                )
                row = cur.fetchone()
                assert row is not None
                return row

    def list_comments(
        self, publication_id: str, *, page: int = 1, page_size: int = 50
    ) -> tuple[list[dict[str, Any]], int]:
        offset = (page - 1) * page_size
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS c FROM openair_community_comments WHERE publication_id = %s",
                    (publication_id,),
                )
                total = int((cur.fetchone() or {}).get("c", 0))
                cur.execute(
                    """
                    SELECT c.*, COALESCE(u.display_name, u.username, c.author_owner_id) AS author_display_name
                    FROM openair_community_comments c
                    LEFT JOIN openair_users u ON u.user_id = c.author_owner_id
                    WHERE c.publication_id = %s
                    ORDER BY c.created_at ASC
                    LIMIT %s OFFSET %s
                    """,
                    (publication_id, page_size, offset),
                )
                rows = cur.fetchall()
        return rows, total
