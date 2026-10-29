"""``openair_artifact_relations`` 仓库（Artifact-03 来源链）。

软 FK（无物理外键——目标产物可能尚未入库/可能来自外部）。幂等：``capability_call_id``
非空（默认 ''），唯一索引 ``(source, target, type, capability_call_id)`` 上 ``ON CONFLICT``
保证重复回写不膨胀。
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from app.config import settings


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value

RELATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS openair_artifact_relations (
    relation_id        TEXT PRIMARY KEY,
    source_artifact_id TEXT NOT NULL,
    target_artifact_id TEXT NOT NULL,
    relation_type      TEXT NOT NULL,
    owner_id           TEXT NOT NULL,
    workspace_id       TEXT NOT NULL,
    capability_call_id TEXT NOT NULL DEFAULT '',
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_openair_artifact_relations
    ON openair_artifact_relations (source_artifact_id, target_artifact_id, relation_type, capability_call_id);
CREATE INDEX IF NOT EXISTS idx_openair_artifact_relations_source ON openair_artifact_relations(source_artifact_id);
CREATE INDEX IF NOT EXISTS idx_openair_artifact_relations_target ON openair_artifact_relations(target_artifact_id);
CREATE INDEX IF NOT EXISTS idx_openair_artifact_relations_type ON openair_artifact_relations(relation_type);
CREATE INDEX IF NOT EXISTS idx_openair_artifact_relations_scope ON openair_artifact_relations(owner_id, workspace_id);
"""

UPSERT_RELATION_SQL = """
INSERT INTO openair_artifact_relations (
    relation_id, source_artifact_id, target_artifact_id, relation_type,
    owner_id, workspace_id, capability_call_id, metadata, created_at
) VALUES (
    %(relation_id)s, %(source_artifact_id)s, %(target_artifact_id)s, %(relation_type)s,
    %(owner_id)s, %(workspace_id)s, %(capability_call_id)s, %(metadata)s::jsonb, %(created_at)s
)
ON CONFLICT (source_artifact_id, target_artifact_id, relation_type, capability_call_id)
DO UPDATE SET metadata = EXCLUDED.metadata;
"""


class ArtifactRelationRepository:
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
                cur.execute(RELATIONS_TABLE_SQL)

    def upsert_relation(
        self,
        *,
        source_artifact_id: str,
        target_artifact_id: str,
        relation_type: str,
        owner_id: str,
        workspace_id: str,
        capability_call_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> None:
        cap = capability_call_id or ""
        # relation_id 确定性：四元组本身唯一，用其拼 id 保证 ON CONFLICT 之外也幂等。
        rid = f"rel_{source_artifact_id}_{target_artifact_id}_{relation_type}_{cap}"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    UPSERT_RELATION_SQL,
                    {
                        "relation_id": rid,
                        "source_artifact_id": source_artifact_id,
                        "target_artifact_id": target_artifact_id,
                        "relation_type": relation_type,
                        "owner_id": owner_id,
                        "workspace_id": workspace_id,
                        "capability_call_id": cap,
                        "metadata": json.dumps(metadata or {}, ensure_ascii=False),
                        "created_at": created_at,
                    },
                )

    def list_for(
        self,
        artifact_id: str,
        *,
        owner_id: str,
        workspace_id: str,
        direction: str = "both",
        relation_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """返回与 artifact_id 相关的关系。direction: incoming | outgoing | both。"""
        clauses = ["owner_id = %s", "workspace_id = %s"]
        params: list[Any] = [owner_id, workspace_id]
        dir_clause = "(source_artifact_id = %s OR target_artifact_id = %s)"
        if direction == "outgoing":
            dir_clause = "source_artifact_id = %s"
            params.append(artifact_id)
        elif direction == "incoming":
            dir_clause = "target_artifact_id = %s"
            params.append(artifact_id)
        else:
            params.extend([artifact_id, artifact_id])
        clauses.append(dir_clause)
        if relation_type is not None:
            clauses.append("relation_type = %s")
            params.append(relation_type)
        where_sql = " AND ".join(clauses)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT source_artifact_id, target_artifact_id, relation_type, "
                    "capability_call_id, metadata, created_at FROM openair_artifact_relations "
                    f"WHERE {where_sql} ORDER BY created_at DESC NULLS LAST",
                    params,
                )
                rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            meta = r.get("metadata") or {}
            if isinstance(meta, str):
                meta = json.loads(meta)
            cap = r.get("capability_call_id") or None
            out.append(
                {
                    "sourceArtifactId": r.get("source_artifact_id"),
                    "targetArtifactId": r.get("target_artifact_id"),
                    "relationType": r.get("relation_type"),
                    "capabilityCallId": cap,
                    "metadata": meta,
                    "createdAt": _iso(r.get("created_at")),
                }
            )
        return out
