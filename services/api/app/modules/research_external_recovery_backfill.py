"""Materialize missing formal recovery facts for terminal unknown Dispatches."""

from __future__ import annotations

import argparse
import json

import psycopg
from psycopg.rows import dict_row

from app.config import settings
from app.modules.provider_registry import load_provider_registry
from app.modules.research_orchestration_repository import ResearchOrchestrationRepository


def _task_scope(task_id: str) -> tuple[str, str]:
    conninfo = (
        f"host={settings.postgres_host} port={settings.postgres_port} "
        f"dbname={settings.postgres_database} user={settings.postgres_user}"
    )
    with psycopg.connect(
        conninfo,
        password=settings.postgres_password,
        row_factory=dict_row,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT owner_id, workspace_id FROM openair_tasks
                    WHERE task_id = %s AND status <> 'deleted'""",
                (task_id,),
            )
            row = cursor.fetchone()
    if row is None:
        raise RuntimeError("research_task_not_found")
    return str(row["owner_id"]), str(row["workspace_id"])


def _provider_identity(
    repository: ResearchOrchestrationRepository,
    task: object,
    work_unit: object,
) -> tuple[str, str]:
    executor_ref = work_unit.executor_ref
    if executor_ref.executor_type == "skill":
        return "direct_skill_runtime", "durable_skill_runtime"
    decision = next(
        row
        for row in task.execution_decisions
        if row.execution_decision_id == work_unit.execution_decision_id
    )
    snapshot = repository.load_registry_snapshot(
        decision.registry_snapshot_ref.content_hash
    )
    definitions = snapshot.get("definitions")
    agents = definitions.get("agents") if isinstance(definitions, dict) else None
    matches = [
        row
        for row in agents or []
        if isinstance(row, dict)
        and row.get("agentId") == executor_ref.executor_id
        and row.get("definitionVersion") == executor_ref.definition_version
        and row.get("enabled") is True
    ]
    if len(matches) != 1:
        raise RuntimeError("dispatch_agent_snapshot_invalid")
    provider_id = str(matches[0].get("defaultProvider") or "").strip()
    if provider_id not in (matches[0].get("allowedProviders") or []):
        raise RuntimeError("dispatch_provider_contract_mismatch")
    adapter = load_provider_registry().require(provider_id).adapter
    return provider_id, adapter


def backfill_task(task_id: str) -> dict[str, object]:
    owner_id, workspace_id = _task_scope(task_id)
    repository = ResearchOrchestrationRepository()
    task = repository.load(
        task_id=task_id,
        owner_id=owner_id,
        workspace_id=workspace_id,
    )
    unknown_work_units = [
        row for row in task.work_units if row.status.value == "external_result_unknown"
    ]
    repaired = []
    for work_unit in unknown_work_units:
        provider_id, adapter = _provider_identity(repository, task, work_unit)
        repaired.append(
            repository.ensure_unknown_recovery_facts(
                task_id=task_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
                work_unit_id=work_unit.work_unit_id,
                provider_id=provider_id,
                adapter=adapter,
            )
        )
    return {
        "taskId": task_id,
        "status": "ready" if repaired else "no_unknown_dispatch",
        "repaired": repaired,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args()
    print(json.dumps(backfill_task(args.task_id), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
