"""Verify existing development projects through live HTTP and unchanged DB facts.

Run from services/api with the main checkout's Python interpreter. This probe
reads existing projects and signs in with the already provisioned development
account; it never schedules research or changes task state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services/api"))

from app.config import settings  # noqa: E402
from app.models.research_orchestration import ResearchTaskV12  # noqa: E402
from app.modules.research_orchestration_repository import (  # noqa: E402
    ResearchOrchestrationRepository,
    _extract_contract_payload,
)
from app.modules.task_public_projection import project_public_task_read_only  # noqa: E402
from app.modules.task_repository import TaskRepository  # noqa: E402
from app.modules.user_dev_seed import DEV_EMAIL, DEV_PASSWORD  # noqa: E402

PVD_IDS = (
    "task_2cab2097a5544f028176953dda397e32",
    "task_eada3100090c458e9c4717e5d1ae8e60",
)


def _require(condition: bool, message: object) -> None:
    if not condition:
        raise RuntimeError(str(message))


@contextmanager
def _read_connection():
    with psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_database,
        user=settings.postgres_user,
        password=settings.postgres_password,
        connect_timeout=5,
        row_factory=dict_row,
    ) as conn:
        conn.read_only = True
        yield conn


def _snapshot(task_id: str) -> dict:
    with _read_connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT task_id, owner_id, workspace_id, revision, payload "
            "FROM openair_tasks WHERE task_id = %s",
            (task_id,),
        )
        row = cursor.fetchone()
    _require(row is not None, task_id)
    if isinstance(row["payload"], str):
        row["payload"] = json.loads(row["payload"])
    return row


def _digest(row: dict) -> str:
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _get(client: httpx.Client, path: str, report: dict) -> dict:
    response = client.get(path)
    report["http"].append({"path": path, "status": response.status_code})
    _require(response.status_code == 200, (path, response.status_code))
    return response.json()


def _verify_pvd(client: httpx.Client, task_id: str, report: dict) -> None:
    before = _snapshot(task_id)
    payload = before["payload"]
    source = payload["researchDesignVersions"][0]["understanding"]
    _require(
        "routeRationale" in source and "planningRationale" not in source, "live verification failed"
    )
    value = source["routeRationale"]
    canonical = ResearchTaskV12.model_validate(_extract_contract_payload(payload))
    _require(
        canonical.research_design_versions[0].understanding.planning_rationale == value,
        "live verification failed",
    )
    repository = ResearchOrchestrationRepository(connection_factory=_read_connection)
    scoped = {
        "task_id": task_id,
        "owner_id": before["owner_id"],
        "workspace_id": before["workspace_id"],
    }
    _require(repository.load(**scoped) == canonical, "live verification failed")
    _require(repository.load_read_only(**scoped) == canonical, "live verification failed")
    for read in (TaskRepository().get_task, TaskRepository().get_task_for_public_read):
        public = project_public_task_read_only(read(task_id))
        _require(public.taskId == task_id, "live verification failed")
    detail = _get(client, f"/api/tasks/{task_id}", report)
    _get(client, f"/api/tasks/{task_id}/research-design", report)
    _get(client, f"/api/tasks/{task_id}/collaboration/canonical", report)
    _get(client, f"/api/tasks/{task_id}/execution-policy", report)
    artifacts = detail.get("artifacts", [])
    _require(len(artifacts) == 6, (task_id, len(artifacts)))
    for artifact in artifacts:
        artifact_id = artifact["id"]
        body = _get(client, f"/api/tasks/{task_id}/artifacts/{artifact_id}/content", report)
        _require(body, artifact_id)
    after = _snapshot(task_id)
    _require(before == after, "GET must preserve the persisted task snapshot")
    report["projects"].append(
        {
            "taskId": task_id,
            "revision": before["revision"],
            "artifacts": len(artifacts),
            "originalRationalePreserved": True,
            "strictCommandLoad": "PASS",
            "readOnlyLoad": "PASS",
            "snapshotSha256Before": _digest(before),
            "snapshotSha256After": _digest(after),
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {
        "runAt": datetime.now(UTC).isoformat(),
        "interpreter": sys.executable,
        "sourceRoot": str(ROOT),
        "baseUrl": args.base_url,
        "profile": "existing real projects, live API and PostgreSQL",
        "http": [],
        "projects": [],
    }
    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        report["runtime"] = _get(client, "/health", report)
        login = client.post("/api/auth/login", json={"email": DEV_EMAIL, "password": DEV_PASSWORD})
        _require(login.status_code == 200, "existing development account login failed")
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        for task_id in PVD_IDS:
            _verify_pvd(client, task_id, report)
        task_list = _get(client, "/api/tasks?limit=100", report)
        for task in task_list["tasks"]:
            _get(client, f"/api/tasks/{task['taskId']}", report)
        report["allVisibleTaskDetails"] = len(task_list["tasks"])
    report["verdict"] = "PASS"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
