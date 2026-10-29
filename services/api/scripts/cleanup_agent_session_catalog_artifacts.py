"""One-off cleanup: remove plaza agent_session rows from the artifact library."""

from __future__ import annotations

import json

import psycopg
from psycopg.rows import dict_row

from app.config import settings


def main() -> None:
    conninfo = (
        f"host={settings.postgres_host} port={settings.postgres_port} "
        f"dbname={settings.postgres_database} user={settings.postgres_user}"
    )
    with psycopg.connect(
        conninfo,
        password=settings.postgres_password,
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT artifact_id, kind, origin, title, source_task_id, metadata
                FROM openair_artifacts
                WHERE origin = 'agent_session'
                   OR kind = 'agent_session_artifact'
                   OR COALESCE(metadata->>'sourceKind', '') = 'agent_session'
                   OR source_task_id LIKE 'ags_%'
                ORDER BY created_at DESC
                """
            )
            rows = list(cur.fetchall())
            print(f"matched={len(rows)}")
            for row in rows:
                print(
                    json.dumps(
                        {
                            "id": row.get("artifact_id"),
                            "kind": row.get("kind"),
                            "origin": row.get("origin"),
                            "title": row.get("title"),
                            "sourceTaskId": row.get("source_task_id"),
                        },
                        ensure_ascii=False,
                    )
                )
            if not rows:
                return
            ids = [str(row["artifact_id"]) for row in rows]
            cur.execute(
                "DELETE FROM openair_artifacts WHERE artifact_id = ANY(%s)",
                (ids,),
            )
            print(f"deleted={cur.rowcount}")
        conn.commit()


if __name__ == "__main__":
    main()
