"""PostgreSQL proof for durable Agent work units and artifact handoffs."""

from __future__ import annotations

import os
import hashlib
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.execution import durable_multiagent_orchestrator as orchestrator_module
from app.execution.durable_multiagent_orchestrator import DurableMultiAgentOrchestrator
from app.execution.durable_orchestrator_runtime import load_frozen_artifact_contract
from app.main import app
from app.modules.artifact_repository import ArtifactRepository
from app.modules.collaboration_repository import CollaborationRepository
from app.modules.durable_execution import DurableExecutionError, DurableExecutionRepository
from app.modules.postgres_migrations import apply_postgres_migrations
from app.modules.task_repository import TaskRepository
from app.modules.user_repository import UserRepository


OWNER = "t1-owner"
WORKSPACE = "t1-workspace"


def _configure_isolated_postgres() -> str:
    dsn = os.environ.get("IB01_TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.fail("IB01_TEST_DATABASE_URL is required; T1 refuses mocks or an implicit database")
    parsed = urlparse(dsn)
    database = parsed.path.lstrip("/").lower()
    if parsed.scheme not in {"postgres", "postgresql"} or not database or not any(tag in database for tag in ("test", "qa")):
        pytest.fail("IB01_TEST_DATABASE_URL must identify an isolated PostgreSQL test or qa database")
    if any(tag in database for tag in ("prod", "production", "development", " dev")):
        pytest.fail("T1 refuses development and production databases")
    settings.postgres_host = parsed.hostname or "localhost"
    settings.postgres_port = parsed.port or 5432
    settings.postgres_user = parsed.username or "postgres"
    settings.postgres_password = parsed.password or ""
    settings.postgres_database = database
    settings.persistence_backend = "postgres"
    return dsn


@pytest.fixture(autouse=True)
def isolated_database() -> None:
    dsn = _configure_isolated_postgres()
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    TaskRepository().bootstrap()
    UserRepository().bootstrap()
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            apply_postgres_migrations(cur)
    ArtifactRepository().bootstrap()
    DurableExecutionRepository().bootstrap()
    assert _query(
        "SELECT version FROM openair_schema_migrations WHERE version = %s",
        ("20260723_01_real_multiagent_collaboration.sql",),
    )


def _query(sql: str, params: tuple = ()) -> list[dict]:
    with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], row_factory=psycopg.rows.dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def _task(task_id: str) -> dict:
    return {
        "taskId": task_id,
        "ownerId": OWNER,
        "workspaceId": WORKSPACE,
        "createdBy": OWNER,
        "title": "Durable Agent handoff",
        "domain": "research_task",
        "currentStage": "evidence",
        "status": "not_started",
        "creationStatus": "confirmed",
        "resultSource": "none",
        "executionMode": "stage_confirmation",
        "stages": ["evidence"],
        "stageCatalog": [],
        "stageDetails": [{"stageKey": "evidence", "status": "not_started", "subStages": []}],
        "artifacts": [],
        "materials": [],
        "memoryRecords": [],
        "confirmations": [],
        "capabilityCalls": [],
    }


def _start_and_claim(task_id: str) -> tuple[dict, dict]:
    TaskRepository().insert_task(_task(task_id))
    durable = DurableExecutionRepository()
    run, _ = durable.start_initial(
        task_id=task_id,
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        idempotency_key=f"start-{task_id}",
        source_mode="live",
    )
    claimed = durable.claim("T1-worker", 60)
    assert claimed and claimed["run_id"] == run["run_id"]
    return run, claimed


def _persist_artifact(*, task_id: str, run_id: str, call_id: str, artifact_id: str) -> None:
    ArtifactRepository().upsert_from_dict(
        task_id,
        {
            "id": artifact_id,
            "ownerId": OWNER,
            "workspaceId": WORKSPACE,
            "createdBy": OWNER,
            "kind": "research_output",
            "origin": "agent_execution",
            "title": artifact_id,
            "sourceTaskId": task_id,
            "sourceCapabilityCallId": call_id,
            "artifactState": "usable",
            "metadata": {"artifactVisibility": "main", "artifactState": "usable"},
        },
    )
    with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            "UPDATE openair_artifacts SET source_execution_run_id = %s, source_mode = 'live' WHERE artifact_id = %s",
            (run_id, artifact_id),
        )


def _attach_confirmed_upload(*, run: dict, claim: dict, call_id: str, artifact_id: str) -> None:
    upload_id = f"upload-{artifact_id}"
    with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            """INSERT INTO openair_artifact_uploads
               (upload_id, run_id, capability_call_id, lease_owner, lease_epoch, object_key, state, artifact_id, confirmed_at)
               VALUES (%s, %s, %s, %s, %s, %s, 'confirmed', %s, NOW())""",
            (
                upload_id, run["run_id"], call_id, "T1-worker", claim["lease_epoch"],
                f"durable/{run['run_id']}/{upload_id}", artifact_id,
            ),
        )
        conn.execute(
            """INSERT INTO openair_execution_artifact_associations
               (upload_id, run_id, call_id, artifact_id) VALUES (%s, %s, %s, %s)""",
            (upload_id, run["run_id"], call_id, artifact_id),
        )


def _unit(repo: CollaborationRepository, *, run: dict, claim: dict, agent: str, key: str,
          input_artifacts: list[str] | None = None, parent_work_unit_id: str | None = None,
          kind: str = "research") -> dict:
    return repo.create_work_unit(
        run_id=run["run_id"],
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        worker_id="T1-worker",
        fence_epoch=claim["lease_epoch"],
        agent_id=agent,
        kind=kind,
        title=key,
        unit_key=key,
        parent_work_unit_id=parent_work_unit_id,
        objective=key,
        input_artifact_refs=input_artifacts,
    )


def _call(repo: CollaborationRepository, *, run: dict, claim: dict, unit: dict, agent: str, key: str,
          input_artifacts: list[str] | None = None, invocation_kind: str = "agent_brain") -> dict:
    return repo.create_capability_invocation(
        run_id=run["run_id"],
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        worker_id="T1-worker",
        fence_epoch=claim["lease_epoch"],
        work_unit_id=unit["work_unit_id"],
        requesting_agent_id=agent,
        invocation_kind=invocation_kind,
        skill_id="intent_understanding" if invocation_kind == "skill" else None,
        idempotency_key=key,
        request_fingerprint=f"fingerprint-{key}",
        input_artifact_refs=input_artifacts,
    )


def _start_call(repo: CollaborationRepository, *, run: dict, claim: dict, unit: dict, agent: str, key: str,
                invocation_kind: str = "agent_brain") -> dict:
    repo.transition_work_unit(
        work_unit_id=unit["work_unit_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="running",
    )
    call = _call(
        repo, run=run, claim=claim, unit=unit, agent=agent, key=key,
        input_artifacts=unit.get("input_artifact_refs"), invocation_kind=invocation_kind,
    )
    repo.begin_capability_invocation(
        call_id=call["call_id"], owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T1-worker",
        fence_epoch=claim["lease_epoch"], idempotency_key=key, request_fingerprint=f"fingerprint-{key}",
    )
    return call


def _frozen_skill_request(
    *, skill_id: str = "materials_mp_constraint_search", binding_version: int = 1,
    input_artifact_refs: list[str] | None = None,
) -> dict:
    definition = {
        "skillId": skill_id,
        "definitionVersion": 1,
        "status": "ready",
        "enabled": True,
        "inputContract": {"status": "unknown", "acceptedInputRefs": []},
        "outputContract": {"status": "unknown", "artifactTypes": []},
        "judgmentSupport": {"deterministic": False},
    }
    binding = {
        "bindingId": f"bind:test:route:skill:{skill_id}",
        "bindingVersion": binding_version,
        "definitionId": "test:route",
        "definitionVersion": 1,
        "skillId": skill_id,
        "skillDefinitionVersion": 1,
        "enabled": True,
        "sourceAuthority": "skill_binding_source",
    }
    return {
        "skillId": skill_id,
        "definitionVersion": 1,
        "snapshotDefinition": definition,
        "sourceBindingRefs": [{
            "bindingId": binding["bindingId"], "bindingVersion": binding_version,
        }],
        "selectedSkillBindings": [binding],
        "inputMemoryRefs": [],
        "inputArtifactRefs": list(input_artifact_refs or []),
        "parameters": {"focus": "bounded"},
        "reason": "need a research configuration",
        "idempotencyKey": "agent-request-1",
    }


def test_artifact_storage_failure_closes_active_run_call_and_work_unit() -> None:
    run, claim = _start_and_claim("task-storage-failure")
    durable = DurableExecutionRepository()
    durable.callback(
        run["run_id"],
        "T1-worker",
        claim["lease_epoch"],
        "execution_started",
        {},
    )
    collaboration = CollaborationRepository()
    unit = _unit(
        collaboration,
        run=run,
        claim=claim,
        agent="research_question_refinement",
        key="storage-failure-unit",
    )
    _start_call(
        collaboration,
        run=run,
        claim=claim,
        unit=unit,
        agent="research_question_refinement",
        key="storage-failure-call",
    )

    terminal = durable.fail_artifact_storage(
        run["run_id"],
        "T1-worker",
        claim["lease_epoch"],
    )

    assert terminal["state"] == "failed"
    assert terminal["failure_classification"] == "artifact_storage_failed_after_provider_result"
    calls = _query(
        "SELECT state, failure_classification FROM openair_execution_capability_calls WHERE run_id = %s",
        (run["run_id"],),
    )
    assert {row["state"] for row in calls} == {"failed"}
    assert {row["failure_classification"] for row in calls} == {
        "artifact_storage_failed_after_provider_result"
    }
    work_units = _query(
        "SELECT state FROM openair_execution_work_units WHERE run_id = %s",
        (run["run_id"],),
    )
    assert work_units == [{"state": "failed"}]
    events = _query(
        "SELECT type, payload FROM openair_execution_run_events WHERE run_id = %s ORDER BY sequence DESC LIMIT 1",
        (run["run_id"],),
    )
    assert events == [
        {
            "type": "failed",
            "payload": {
                "failureClassification": "artifact_storage_failed_after_provider_result"
            },
        }
    ]


def test_local_durable_artifact_persists_checksum_and_lineage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(settings, "artifact_storage", "local")
    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    run, claim = _start_and_claim("task-local-artifact")
    durable = DurableExecutionRepository()
    durable.record_orchestration_plan(
        run["run_id"],
        "T1-worker",
        claim["lease_epoch"],
        {"selectedProvider": "openai_primary", "workUnits": []},
    )
    durable.read_orchestration_execution_context(
        run["run_id"],
        "T1-worker",
        claim["lease_epoch"],
    )
    collaboration = CollaborationRepository()
    unit = _unit(
        collaboration,
        run=run,
        claim=claim,
        agent="research_question_refinement",
        key="local-artifact-unit",
    )
    call = _start_call(
        collaboration,
        run=run,
        claim=claim,
        unit=unit,
        agent="research_question_refinement",
        key="local-artifact-call",
    )
    content = b'{"question_candidates":["candidate"]}'
    digest = hashlib.sha256(content).hexdigest()

    artifact_id = durable.persist_collaboration_artifact(
        run["run_id"],
        call["call_id"],
        "T1-worker",
        claim["lease_epoch"],
        content=content,
        summary="A verified local artifact",
        presentation={
            "title": "A verified local artifact",
            "mimeType": "text/plain",
            "metadata": {
                "providerId": "openai_primary",
                "adapter": "huiyan_chat_completions",
                "contentLength": len(content.decode("utf-8")),
            },
            "contentSha256": digest,
            "contentSize": len(content),
        },
    )

    artifacts = _query(
        """SELECT artifact_id, storage_type, storage_ref, content_sha256, metadata,
                  source_execution_run_id, source_capability_call_id
           FROM openair_artifacts WHERE artifact_id = %s""",
        (artifact_id,),
    )
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact["storage_type"] == "local"
    assert artifact["content_sha256"] == digest
    assert artifact["source_execution_run_id"] == run["run_id"]
    assert artifact["source_capability_call_id"] == call["call_id"]
    initial_id = f"v_initial_{artifact_id}"
    assert artifact["metadata"]["currentVersionId"] == initial_id
    assert len(artifact["metadata"]["versions"]) == 1
    initial = artifact["metadata"]["versions"][0]
    assert initial["versionId"] == initial_id
    assert initial["versionNumber"] == 1
    assert initial["source"] == "initial"
    assert initial["contentSha256"] == digest
    assert initial["contentSize"] == len(content)
    assert initial["storageType"] == "local"
    assert initial["path"] == artifact["storage_ref"]
    stored_path = tmp_path / run["task_id"] / artifact["storage_ref"]
    assert stored_path.read_bytes() == content
    materials = collaboration.read_review_materials(
        run_id=run["run_id"],
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        artifact_refs=[artifact_id],
    )
    assert materials == [
        {
            "artifactId": artifact_id,
            "title": "A verified local artifact",
            "mimeType": "text/plain",
            "content": content.decode("utf-8"),
            "truncated": False,
            "contentSha256": digest,
        }
    ]


def test_work_unit_without_invocation_cannot_claim_success() -> None:
    run, claim = _start_and_claim("task-no-invocation")
    repo = CollaborationRepository()
    unit = _unit(repo, run=run, claim=claim, agent="agent-a", key="unit-a")
    repo.transition_work_unit(
        work_unit_id=unit["work_unit_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="running",
    )
    with pytest.raises(DurableExecutionError, match="work_unit_success_requires_successful_invocation"):
        repo.transition_work_unit(
            work_unit_id=unit["work_unit_id"], owner_id=OWNER, workspace_id=WORKSPACE,
            worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="succeeded",
        )


def test_successful_invocation_without_output_cannot_claim_an_explicit_artifact() -> None:
    run, claim = _start_and_claim("task-empty-output")
    repo = CollaborationRepository()
    unit_a = _unit(repo, run=run, claim=claim, agent="agent-a", key="unit-a")
    call_a = _start_call(repo, run=run, claim=claim, unit=unit_a, agent="agent-a", key="call-a")
    repo.finish_capability_invocation(
        call_id=call_a["call_id"], owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T1-worker",
        fence_epoch=claim["lease_epoch"], state="succeeded", summary="No artifact",
    )
    with pytest.raises(DurableExecutionError, match="work_unit_success_requires_output_reference"):
        repo.transition_work_unit(
            work_unit_id=unit_a["work_unit_id"], owner_id=OWNER, workspace_id=WORKSPACE,
            worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="succeeded",
            output_artifact_refs=["artifact-from-somewhere-else"],
        )


def test_work_unit_rejects_artifact_owned_by_another_work_unit_call() -> None:
    run, claim = _start_and_claim("task-foreign-output")
    repo = CollaborationRepository()
    unit_a = _unit(repo, run=run, claim=claim, agent="agent-a", key="unit-a")
    call_a = _start_call(repo, run=run, claim=claim, unit=unit_a, agent="agent-a", key="call-a")
    unit_b = _unit(repo, run=run, claim=claim, agent="agent-b", key="unit-b")
    call_b = _start_call(repo, run=run, claim=claim, unit=unit_b, agent="agent-b", key="call-b")
    _persist_artifact(
        task_id="task-foreign-output", run_id=run["run_id"], call_id=call_b["call_id"], artifact_id="artifact-b",
    )
    repo.finish_capability_invocation(
        call_id=call_b["call_id"], owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T1-worker",
        fence_epoch=claim["lease_epoch"], state="succeeded", summary="Agent B output",
        output_artifact_refs=["artifact-b"],
    )
    with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            """UPDATE openair_execution_capability_calls
               SET state = 'succeeded', output_artifact_refs = '[\"artifact-b\"]'::jsonb, ended_at = NOW()
               WHERE call_id = %s""",
            (call_a["call_id"],),
        )
    with pytest.raises(DurableExecutionError, match="collaboration_artifact_reference_not_found"):
        repo.transition_work_unit(
            work_unit_id=unit_a["work_unit_id"], owner_id=OWNER, workspace_id=WORKSPACE,
            worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="succeeded",
            output_artifact_refs=["artifact-b"],
        )


def test_postgres_persists_two_actual_agents_and_the_same_a_to_b_artifact_reference() -> None:
    run, claim = _start_and_claim("task-handoff")
    repo = CollaborationRepository()

    agent_a = _unit(repo, run=run, claim=claim, agent="agent-a", key="unit-a")
    assert _unit(repo, run=run, claim=claim, agent="agent-a", key="unit-a")["work_unit_id"] == agent_a["work_unit_id"]
    repo.transition_work_unit(
        work_unit_id=agent_a["work_unit_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="running",
    )
    call_a = _call(repo, run=run, claim=claim, unit=agent_a, agent="agent-a", key="call-a")
    assert _call(repo, run=run, claim=claim, unit=agent_a, agent="agent-a", key="call-a")["call_id"] == call_a["call_id"]
    _, replay = repo.begin_capability_invocation(
        call_id=call_a["call_id"], owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T1-worker",
        fence_epoch=claim["lease_epoch"], idempotency_key="call-a", request_fingerprint="fingerprint-call-a",
    )
    assert replay is False
    _, replay = repo.begin_capability_invocation(
        call_id=call_a["call_id"], owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T1-worker",
        fence_epoch=claim["lease_epoch"], idempotency_key="call-a", request_fingerprint="fingerprint-call-a",
    )
    assert replay is True
    _persist_artifact(task_id="task-handoff", run_id=run["run_id"], call_id=call_a["call_id"], artifact_id="artifact-a")
    repo.finish_capability_invocation(
        call_id=call_a["call_id"], owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T1-worker",
        fence_epoch=claim["lease_epoch"], state="succeeded", summary="Agent A result",
        output_artifact_refs=["artifact-a"],
    )
    agent_a = repo.transition_work_unit(
        work_unit_id=agent_a["work_unit_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="succeeded",
        output_artifact_refs=["artifact-a"],
    )

    agent_b = _unit(
        repo, run=run, claim=claim, agent="agent-b", key="unit-b",
        parent_work_unit_id=agent_a["work_unit_id"],
    )
    handoff = repo.handoff(
        from_work_unit_id=agent_a["work_unit_id"], to_work_unit_id=agent_b["work_unit_id"],
        owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T1-worker", fence_epoch=claim["lease_epoch"],
        summary="Agent A evidence passed to Agent B", input_artifact_refs=["artifact-a"],
        output_artifact_refs=["artifact-a"],
    )
    assert repo.handoff(
        from_work_unit_id=agent_a["work_unit_id"], to_work_unit_id=agent_b["work_unit_id"],
        owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T1-worker", fence_epoch=claim["lease_epoch"],
        summary="Agent A evidence passed to Agent B", input_artifact_refs=["artifact-a"],
        output_artifact_refs=["artifact-a"],
    )["handoff_id"] == handoff["handoff_id"]

    repo.transition_work_unit(
        work_unit_id=agent_b["work_unit_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="running",
    )
    call_b = _call(
        repo, run=run, claim=claim, unit=agent_b, agent="agent-b", key="call-b",
        input_artifacts=["artifact-a"],
    )
    repo.begin_capability_invocation(
        call_id=call_b["call_id"], owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T1-worker",
        fence_epoch=claim["lease_epoch"], idempotency_key="call-b", request_fingerprint="fingerprint-call-b",
    )
    _persist_artifact(task_id="task-handoff", run_id=run["run_id"], call_id=call_b["call_id"], artifact_id="artifact-b")
    repo.finish_capability_invocation(
        call_id=call_b["call_id"], owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T1-worker",
        fence_epoch=claim["lease_epoch"], state="succeeded", summary="Agent B result",
        output_artifact_refs=["artifact-b"],
    )
    repo.transition_work_unit(
        work_unit_id=agent_b["work_unit_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="succeeded",
        output_artifact_refs=["artifact-b"],
    )

    reloaded = CollaborationRepository()
    units = reloaded.list_work_units(run_id=run["run_id"], owner_id=OWNER, workspace_id=WORKSPACE)
    handoffs = reloaded.list_handoffs(run_id=run["run_id"], owner_id=OWNER, workspace_id=WORKSPACE)
    assert [(unit["agent_id"], unit["state"]) for unit in units] == [("agent-a", "succeeded"), ("agent-b", "succeeded")]
    assert units[0]["output_artifact_refs"] == ["artifact-a"]
    assert units[1]["input_artifact_refs"] == ["artifact-a"]
    assert len(handoffs) == 1
    assert handoffs[0]["from_agent_id"] == "agent-a" and handoffs[0]["to_agent_id"] == "agent-b"
    assert handoffs[0]["output_artifact_refs"] == handoffs[0]["input_artifact_refs"] == ["artifact-a"]
    assert _query("SELECT attempt_number FROM openair_execution_capability_calls WHERE call_id = %s", (call_a["call_id"],))[0]["attempt_number"] == 1


def test_coordinator_decision_completes_without_fabricating_an_artifact() -> None:
    run, claim = _start_and_claim("task-coordinator")
    repo = CollaborationRepository()
    unit = repo.create_work_unit(
        run_id=run["run_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"],
        agent_id="openair_orchestrator", kind="coordinator", title="协调研究草案",
        unit_key="coordinator", objective="按用户确认的草案派发研究工作",
    )
    repo.transition_work_unit(
        work_unit_id=unit["work_unit_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="running",
    )
    completed = repo.transition_work_unit(
        work_unit_id=unit["work_unit_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="succeeded",
        summary="任务已派发，尚未形成研究产物。", decision_summary="按已确认草案执行。",
    )
    assert completed["state"] == "succeeded"
    assert completed["output_artifact_refs"] == []


def test_postgres_and_minio_complete_a_real_server_owned_multiagent_handoff() -> None:
    endpoint = os.environ.get("IB01_TEST_MINIO_ENDPOINT", "").strip()
    if not endpoint:
        pytest.fail("IB01_TEST_MINIO_ENDPOINT is required for the multi-Agent persistence proof")
    settings.minio_endpoint = endpoint
    settings.minio_access_key = os.environ.get("IB01_TEST_MINIO_ACCESS_KEY", "minio-test")
    settings.minio_secret_key = os.environ.get("IB01_TEST_MINIO_SECRET_KEY", "minio-test-secret")
    settings.minio_bucket = "openair-collab-test"
    settings.minio_secure = False
    settings.artifact_storage = "minio"
    from app.modules import minio_client
    minio_client._client = None
    minio_client.ensure_minio_bucket()

    run, claim = _start_and_claim("task-full-loop")
    question_frozen = load_frozen_artifact_contract(
        "research_question_refinement_artifact_v1",
        "schemas/agents/artifacts/research_question_refinement_artifact_v1.schema.json",
    )
    question_contract = {
        **question_frozen.snapshot(),
        "artifactContractSchemaVersion": 1,
        "artifactRequiredFields": list(question_frozen.schema["required"]),
    }
    literature_frozen = load_frozen_artifact_contract(
        "key_literature_positioning_artifact_v1",
        "schemas/agents/artifacts/key_literature_positioning_artifact_v1.schema.json",
    )
    literature_contract = {
        **literature_frozen.snapshot(),
        "artifactContractSchemaVersion": 1,
        "artifactRequiredFields": list(literature_frozen.schema["required"]),
    }
    review_frozen = load_frozen_artifact_contract(
        "independent_result_review_v1",
        "schemas/agents/reviews/independent_result_review_v1.schema.json",
    )
    review_contract = {
        "reviewerAgentId": "result_reliability_audit",
        "reviewKind": "independent_result_review",
        "artifactContractSchemaVersion": 1,
        "artifactRequiredFields": list(review_frozen.schema["required"]),
        **review_frozen.snapshot(),
    }
    plan = {
        "context": {
            "runId": run["run_id"], "taskId": run["task_id"],
            "ownerId": OWNER, "workspaceId": WORKSPACE,
        },
        "selectedAgentId": "paper_evidence_extraction",
        "selectedSkillIds": [],
        "modelProfile": "terra",
        "selectedProvider": "openai_primary",
        "providerPolicy": {"allowedProviders": ["openai_primary"]},
        "workUnits": [
                {"unitKey": "question", "parentUnitKey": None, "agentId": "research_question_refinement", "kind": "specialist", "state": "planned", "objective": "Refine the research question.", "inputMemoryRefs": [], "inputArtifactRefs": [], "selectedSkillIds": [], "modelMode": "terra", "mandatoryReviewContract": review_contract, **question_contract},
                {"unitKey": "literature", "parentUnitKey": "question", "agentId": "key_literature_positioning", "kind": "specialist", "state": "planned", "objective": "Position literature from the reviewed question artifact.", "inputMemoryRefs": [], "inputArtifactRefs": [], "selectedSkillIds": [], "modelMode": "terra", "mandatoryReviewContract": review_contract, **literature_contract},
        ],
    }
    DurableExecutionRepository().record_orchestration_plan(
        run["run_id"], "T1-worker", claim["lease_epoch"], plan,
    )
    executed: list[dict] = []

    def execute_agent(request: dict) -> dict:
        executed.append(request)
        if request.get("phase") == "review_artifact":
            return {
                "verdict": "usable",
                "basis": "The persisted artifact supports the next step.",
                "issues": [],
                "missingInputs": [],
            }
        return {
            "state": "succeeded",
            "summary": f"{request['agentId']} 已完成",
            "content": f"真实持久化测试产物：{request['unitKey']}",
        }

    durable = DurableExecutionRepository()

    def persist_artifact(call_id: str, result: dict) -> str:
        content = result["content"].encode("utf-8")
        return durable.persist_collaboration_artifact(
            run["run_id"], call_id, "T1-worker", claim["lease_epoch"],
            content=content,
            summary=result["summary"],
            presentation={
                "title": result["summary"],
                "mimeType": "text/plain",
                "metadata": {
                    "providerId": "openai_primary",
                    "adapter": "huiyan_chat_completions",
                    "contentLength": len(content.decode("utf-8")),
                },
                "contentSha256": hashlib.sha256(content).hexdigest(),
                "contentSize": len(content),
            },
        )

    outcome = DurableMultiAgentOrchestrator(
        collaboration=CollaborationRepository(durable),
        execute_agent_unit=execute_agent,
        persist_artifact=persist_artifact,
    ).run(
        frozen_run={**run, "input_snapshot": {"goalSummary": "核查研究问题", "creationPlan": {}}},
        persisted_plan=plan,
        worker_id="T1-worker",
        lease_epoch=claim["lease_epoch"],
    )
    assert outcome["state"] == "succeeded"
    assert [item["agentId"] for item in executed] == [
        "research_question_refinement", "result_reliability_audit",
        "key_literature_positioning", "result_reliability_audit",
    ]
    assert executed[2]["inputArtifactRefs"] == [outcome["artifactIds"][0]]
    terminal = durable.callback(run["run_id"], "T1-worker", claim["lease_epoch"], "succeeded", {})
    assert terminal["state"] == "succeeded"
    projection = CollaborationRepository(durable).list_projection(
        task_id=run["task_id"], owner_id=OWNER, workspace_id=WORKSPACE,
    )
    assert [entry["status"] for entry in projection["entries"]] == ["completed"] * 4
    assert projection["entries"][2]["handoff"]["fromAgentId"] == "research_question_refinement"
    assert len(durable.list_artifacts(run["run_id"], OWNER, WORKSPACE)) == 2


def test_stale_fence_unknown_and_stop_prevent_progress_or_handoff() -> None:
    run, claim = _start_and_claim("task-stop-unknown")
    repo = CollaborationRepository()
    agent_a = _unit(repo, run=run, claim=claim, agent="agent-a", key="unit-a")
    agent_b = _unit(
        repo, run=run, claim=claim, agent="agent-b", key="unit-b",
        parent_work_unit_id=agent_a["work_unit_id"],
    )
    repo.transition_work_unit(
        work_unit_id=agent_a["work_unit_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="running",
    )
    call_a = _call(repo, run=run, claim=claim, unit=agent_a, agent="agent-a", key="call-a")
    call_b = _call(repo, run=run, claim=claim, unit=agent_b, agent="agent-b", key="call-b")
    repo.begin_capability_invocation(
        call_id=call_a["call_id"], owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T1-worker",
        fence_epoch=claim["lease_epoch"], idempotency_key="call-a", request_fingerprint="fingerprint-call-a",
    )
    repo.mark_capability_result_unknown(
        call_id=call_a["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"],
    )
    with pytest.raises(DurableExecutionError, match="external_result_unknown_no_automatic_replay"):
        repo.begin_capability_invocation(
            call_id=call_a["call_id"], owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T1-worker",
            fence_epoch=claim["lease_epoch"], idempotency_key="call-a", request_fingerprint="fingerprint-call-a",
        )
    with pytest.raises(DurableExecutionError, match="terminal_work_unit"):
        repo.transition_work_unit(
            work_unit_id=agent_a["work_unit_id"], owner_id=OWNER, workspace_id=WORKSPACE,
            worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="succeeded",
            output_artifact_refs=["missing-artifact"],
        )
    with pytest.raises(DurableExecutionError, match="stale_or_expired_lease"):
        repo.transition_work_unit(
            work_unit_id=agent_b["work_unit_id"], owner_id=OWNER, workspace_id=WORKSPACE,
            worker_id="T1-worker", fence_epoch=claim["lease_epoch"] + 1, state="running",
        )

    DurableExecutionRepository().stop_current("task-stop-unknown", OWNER, WORKSPACE)
    before = _query("SELECT count(*) AS count FROM openair_execution_handoffs")[0]["count"]
    with pytest.raises(DurableExecutionError, match="stop_won"):
        repo.handoff(
            from_work_unit_id=agent_a["work_unit_id"], to_work_unit_id=agent_b["work_unit_id"],
            owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T1-worker", fence_epoch=claim["lease_epoch"],
            summary="must not happen",
        )
    with pytest.raises(DurableExecutionError, match="stop_won"):
        _unit(repo, run=run, claim=claim, agent="agent-c", key="unit-c")
    assert _query("SELECT count(*) AS count FROM openair_execution_handoffs")[0]["count"] == before == 0
    states = {
        row["work_unit_id"]: row["state"]
        for row in _query("SELECT work_unit_id, state FROM openair_execution_work_units WHERE run_id = %s", (run["run_id"],))
    }
    assert states == {agent_a["work_unit_id"]: "unknown", agent_b["work_unit_id"]: "cancelled"}
    calls = {
        row["call_id"]: (row["state"], row["failure_classification"])
        for row in _query(
            "SELECT call_id, state, failure_classification FROM openair_execution_capability_calls WHERE work_unit_id IS NOT NULL",
        )
    }
    assert calls[call_a["call_id"]] == ("unknown", "external_result_unknown")
    assert calls[call_b["call_id"]] == ("cancel_requested", "stopped_by_user")


def test_execution_policy_persists_pending_until_next_dispatch_boundary() -> None:
    run, claim = _start_and_claim("task-policy-pending")
    repo = CollaborationRepository()
    initial = repo.get_task_execution_policy(task_id="task-policy-pending", owner_id=OWNER, workspace_id=WORKSPACE)
    assert initial["currentMode"] == "stage_confirmation"
    assert initial["pendingMode"] is None

    unit_a = _unit(repo, run=run, claim=claim, agent="agent-a", key="unit-a")
    call_a = _start_call(repo, run=run, claim=claim, unit=unit_a, agent="agent-a", key="call-a")
    pending = repo.set_task_execution_policy(
        task_id="task-policy-pending", owner_id=OWNER, workspace_id=WORKSPACE,
        mode="automatic_research", changed_by=OWNER,
    )
    assert pending["currentMode"] == "stage_confirmation"
    assert pending["pendingMode"] == "automatic_research"
    assert pending["pendingPolicyVersion"] == 2

    _persist_artifact(task_id="task-policy-pending", run_id=run["run_id"], call_id=call_a["call_id"], artifact_id="artifact-a")
    repo.finish_capability_invocation(
        call_id=call_a["call_id"], owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T1-worker",
        fence_epoch=claim["lease_epoch"], state="succeeded", summary="Agent A complete", output_artifact_refs=["artifact-a"],
    )
    completed_a = repo.transition_work_unit(
        work_unit_id=unit_a["work_unit_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="succeeded", output_artifact_refs=["artifact-a"],
    )
    retried_a = CollaborationRepository().create_work_unit(
        run_id=run["run_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"],
        agent_id="agent-a", kind="research", title="unit-a", unit_key="unit-a", objective="unit-a",
    )
    assert retried_a["work_unit_id"] == completed_a["work_unit_id"]
    assert retried_a["dispatch_mode"] == "stage_confirmation"
    assert retried_a["policy_version"] == 1
    still_pending = CollaborationRepository().get_task_execution_policy(
        task_id="task-policy-pending", owner_id=OWNER, workspace_id=WORKSPACE,
    )
    assert still_pending["pendingMode"] == "automatic_research"
    unit_b = _unit(
        repo, run=run, claim=claim, agent="agent-b", key="unit-b", parent_work_unit_id=completed_a["work_unit_id"],
        input_artifacts=["artifact-a"],
    )
    assert unit_b["dispatch_mode"] == "automatic_research"
    assert unit_b["policy_version"] == 2
    assert unit_b["boundary_kind"] == "dispatch"

    reloaded = CollaborationRepository().get_task_execution_policy(
        task_id="task-policy-pending", owner_id=OWNER, workspace_id=WORKSPACE,
    )
    assert reloaded["currentMode"] == "automatic_research"
    assert reloaded["pendingMode"] is None
    assert TaskRepository().get_task("task-policy-pending")["executionMode"] == "automatic_research"


def test_postgres_execution_policy_routes_replace_legacy_mode_503() -> None:
    task_id = "task-policy-routes"
    task = _task(task_id)
    task.update({
        "currentStage": "problem_modeling",
        "stages": ["problem_modeling"],
        "stageDetails": [{"stageKey": "problem_modeling", "status": "not_started", "subStages": []}],
    })
    TaskRepository().insert_task(task)
    headers = {
        "X-OpenAIR-Owner-Id": OWNER,
        "X-OpenAIR-Workspace-Id": WORKSPACE,
        "X-OpenAIR-Created-By": OWNER,
    }
    client = TestClient(app)
    initial = client.get(f"/api/tasks/{task_id}/execution-policy", headers=headers)
    assert initial.status_code == 200, initial.text
    assert initial.json()["currentMode"] == "stage_confirmation"

    policy_patch = client.patch(
        f"/api/tasks/{task_id}/execution-policy", headers=headers,
        json={"executionMode": "manual_step"},
    )
    assert policy_patch.status_code == 200, policy_patch.text
    assert policy_patch.json()["currentMode"] == "manual_step"
    assert policy_patch.json()["pendingMode"] is None

    compatibility_patch = client.patch(
        f"/api/tasks/{task_id}/execution-mode", headers=headers,
        json={"executionMode": "automatic_research"},
    )
    assert compatibility_patch.status_code == 200, compatibility_patch.text
    assert compatibility_patch.json()["executionMode"] == "automatic_research"
    reloaded = client.get(f"/api/tasks/{task_id}/execution-policy", headers=headers)
    assert reloaded.status_code == 200
    assert reloaded.json()["currentMode"] == "automatic_research"


def test_policy_switch_never_resolves_an_existing_gate() -> None:
    run, claim = _start_and_claim("task-policy-gate")
    repo = CollaborationRepository()
    gate = repo.create_gate(
        run_id=run["run_id"], owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T1-worker",
        fence_epoch=claim["lease_epoch"], gate_kind="artifact_review", reason="human review required", policy_version=1,
    )
    changed = repo.set_task_execution_policy(
        task_id="task-policy-gate", owner_id=OWNER, workspace_id=WORKSPACE,
        mode="manual_step", changed_by=OWNER,
    )
    assert changed["currentMode"] == "manual_step"
    assert changed["pendingMode"] is None
    reloaded_gate = CollaborationRepository().list_gates(
        run_id=run["run_id"], owner_id=OWNER, workspace_id=WORKSPACE,
    )[0]
    assert reloaded_gate["gate_id"] == gate["gate_id"]
    assert reloaded_gate["state"] == "pending"
    assert reloaded_gate["resolved_by"] is None and reloaded_gate["resolved_at"] is None


def test_manual_policy_boundary_requires_one_explicit_continue_on_same_run() -> None:
    run, claim = _start_and_claim("task-policy-manual-continue")
    repo = CollaborationRepository()
    repo.set_task_execution_policy(
        task_id=run["task_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        mode="manual_step", changed_by=OWNER,
    )
    source = _unit(repo, run=run, claim=claim, agent="agent-a", key="unit-a")
    source["state"] = "succeeded"

    paused = repo.create_work_unit(
        run_id=run["run_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"],
        agent_id="agent-b", kind="specialist", title="unit-b", unit_key="unit-b",
        parent_work_unit_id=source["work_unit_id"], objective="unit-b",
        boundary_kind="handoff_dispatch", enforce_execution_policy=True,
    )
    assert paused["dispatch_paused"] is True
    gates = repo.list_gates(run_id=run["run_id"], owner_id=OWNER, workspace_id=WORKSPACE)
    assert [(gate["gate_kind"], gate["state"]) for gate in gates] == [("execution_policy", "pending")]
    assert _query("SELECT state FROM openair_execution_runs WHERE run_id = %s", (run["run_id"],)) == [{"state": "awaiting_human"}]
    projection = repo.list_projection(task_id=run["task_id"], owner_id=OWNER, workspace_id=WORKSPACE)
    assert projection["collaboration"]["pendingDecisions"][0]["allowedActions"] == ["continue_run", "terminate_run"]

    gate_id = gates[0]["gate_id"]
    with pytest.raises(DurableExecutionError, match="execution_policy_requires_explicit_decision"):
        repo.resolve_gate(
            gate_id=gate_id, owner_id=OWNER, workspace_id=WORKSPACE,
            resolved_by=OWNER, state="resolved",
        )
    assert _query(
        "SELECT state, resolution_action FROM openair_execution_gates WHERE gate_id = %s", (gate_id,),
    ) == [{"state": "pending", "resolution_action": None}]
    assert _query("SELECT state FROM openair_execution_runs WHERE run_id = %s", (run["run_id"],)) == [{"state": "awaiting_human"}]
    client = TestClient(app)
    headers = {"X-OpenAIR-Owner-Id": OWNER, "X-OpenAIR-Workspace-Id": WORKSPACE, "X-OpenAIR-Created-By": OWNER}
    response = client.post(
        f"/api/tasks/{run['task_id']}/collaboration/decisions/{gate_id}", headers=headers,
        json={"action": "continue_run"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["runId"] == run["run_id"]
    assert response.json()["successorRunId"] is None
    assert _query("SELECT state FROM openair_execution_runs WHERE run_id = %s", (run["run_id"],)) == [{"state": "queued"}]
    resumed_claim = DurableExecutionRepository().claim("T1-worker", 60)
    assert resumed_claim and resumed_claim["run_id"] == run["run_id"]
    assert repo.create_work_unit(
        run_id=run["run_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=resumed_claim["lease_epoch"],
        agent_id="agent-b", kind="specialist", title="unit-b", unit_key="unit-b",
        parent_work_unit_id=source["work_unit_id"], objective="unit-b",
        boundary_kind="handoff_dispatch", enforce_execution_policy=True,
    )["dispatch_paused"] is False
    replay = client.post(
        f"/api/tasks/{run['task_id']}/collaboration/decisions/{gate_id}", headers=headers,
        json={"action": "continue_run"},
    )
    assert replay.status_code == 200
    review_gate = repo.create_gate(
        run_id=run["run_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=resumed_claim["lease_epoch"], gate_kind="artifact_review",
        reason="Independent review remains separate.", policy_version=1,
    )
    rejected = client.post(
        f"/api/tasks/{run['task_id']}/collaboration/decisions/{review_gate['gate_id']}", headers=headers,
        json={"action": "continue_run"},
    )
    assert rejected.status_code == 422
    with pytest.raises(DurableExecutionError, match="execution gate not found"):
        repo.decide_execution_policy_gate(
            task_id=run["task_id"], gate_id=review_gate["gate_id"], owner_id=OWNER, workspace_id=WORKSPACE,
            decided_by=OWNER, action="continue_run",
        )


def test_handoff_requires_live_usable_artifacts_and_allows_them_after_reload() -> None:
    run, claim = _start_and_claim("task-handoff-artifact-gate")
    repo = CollaborationRepository()
    unit_a = _unit(repo, run=run, claim=claim, agent="agent-a", key="unit-a")
    call_a = _start_call(repo, run=run, claim=claim, unit=unit_a, agent="agent-a", key="call-a")
    _persist_artifact(task_id="task-handoff-artifact-gate", run_id=run["run_id"], call_id=call_a["call_id"], artifact_id="artifact-a")
    repo.finish_capability_invocation(
        call_id=call_a["call_id"], owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T1-worker",
        fence_epoch=claim["lease_epoch"], state="succeeded", summary="Agent A complete", output_artifact_refs=["artifact-a"],
    )
    completed_a = repo.transition_work_unit(
        work_unit_id=unit_a["work_unit_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="succeeded", output_artifact_refs=["artifact-a"],
    )
    unit_b = _unit(
        repo, run=run, claim=claim, agent="agent-b", key="unit-b", parent_work_unit_id=completed_a["work_unit_id"],
        input_artifacts=["artifact-a"],
    )
    with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            "UPDATE openair_artifacts SET metadata = jsonb_set(metadata, '{artifactState}', '\"needs_review\"'::jsonb) WHERE artifact_id = 'artifact-a'",
        )
    with pytest.raises(DurableExecutionError, match="handoff_requires_live_usable_artifacts"):
        repo.handoff(
            from_work_unit_id=completed_a["work_unit_id"], to_work_unit_id=unit_b["work_unit_id"],
            owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T1-worker", fence_epoch=claim["lease_epoch"],
            summary="blocked review artifact", input_artifact_refs=["artifact-a"], output_artifact_refs=["artifact-a"],
        )
    with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            "UPDATE openair_artifacts SET metadata = jsonb_set(metadata, '{artifactState}', '\"usable\"'::jsonb), source_mode = 'live' WHERE artifact_id = 'artifact-a'",
        )
    handoff = CollaborationRepository().handoff(
        from_work_unit_id=completed_a["work_unit_id"], to_work_unit_id=unit_b["work_unit_id"],
        owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T1-worker", fence_epoch=claim["lease_epoch"],
        summary="usable live artifact", input_artifact_refs=["artifact-a"], output_artifact_refs=["artifact-a"],
    )
    assert handoff["output_artifact_refs"] == ["artifact-a"]


def test_independent_review_unlocks_specialist_before_one_handoff() -> None:
    run, claim = _start_and_claim("task-independent-review")
    repo = CollaborationRepository()
    specialist = _unit(
        repo, run=run, claim=claim, agent="paper_evidence_extraction", key="unit-specialist", kind="specialist",
    )
    specialist_call = _start_call(
        repo, run=run, claim=claim, unit=specialist,
        agent="paper_evidence_extraction", key="specialist-call",
    )
    _persist_artifact(
        task_id="task-independent-review", run_id=run["run_id"],
        call_id=specialist_call["call_id"], artifact_id="artifact-review-target",
    )
    with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            "UPDATE openair_artifacts SET metadata = jsonb_set(metadata, '{artifactState}', '\"needs_review\"'::jsonb) WHERE artifact_id = 'artifact-review-target'",
        )
    repo.finish_capability_invocation(
        call_id=specialist_call["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="succeeded",
        summary="Evidence extracted", output_artifact_refs=["artifact-review-target"],
    )
    with pytest.raises(DurableExecutionError, match="specialist_completion_requires_live_usable_artifacts"):
        repo.complete_specialist_after_review(
            work_unit_id=specialist["work_unit_id"], owner_id=OWNER, workspace_id=WORKSPACE,
            worker_id="T1-worker", fence_epoch=claim["lease_epoch"],
            artifact_refs=["artifact-review-target"], summary="not reviewed",
        )

    reviewer = _unit(
        repo, run=run, claim=claim, agent="result_reliability_audit", key="review:unit-specialist",
        parent_work_unit_id=specialist["work_unit_id"], input_artifacts=["artifact-review-target"], kind="review",
    )
    review_call = _start_call(
        repo, run=run, claim=claim, unit=reviewer,
        agent="result_reliability_audit", key="independent-review-call", invocation_kind="review",
    )
    persisted = repo.finish_artifact_review(
        call_id=review_call["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], verdict="usable",
        basis="The evidence supports the next step.", issues=[], missing_inputs=[],
        artifact_refs=["artifact-review-target"],
    )
    assert persisted["workUnit"]["state"] == "succeeded"
    completed = CollaborationRepository().complete_specialist_after_review(
        work_unit_id=specialist["work_unit_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"],
        artifact_refs=["artifact-review-target"], summary="Independently reviewed",
    )
    downstream = _unit(
        repo, run=run, claim=claim, agent="validation_design", key="unit-downstream",
        parent_work_unit_id=completed["work_unit_id"], input_artifacts=["artifact-review-target"],
    )
    handoff = repo.handoff(
        from_work_unit_id=completed["work_unit_id"], to_work_unit_id=downstream["work_unit_id"],
        owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T1-worker", fence_epoch=claim["lease_epoch"],
        summary="Reviewed artifact handed off", input_artifact_refs=["artifact-review-target"],
        output_artifact_refs=["artifact-review-target"],
    )
    artifact = _query(
        "SELECT metadata FROM openair_artifacts WHERE artifact_id = %s", ("artifact-review-target",),
    )[0]["metadata"]
    assert artifact["artifactState"] == "usable"
    assert artifact["reviewedAt"]
    assert artifact["reviewedBy"] == "result_reliability_audit"
    assert artifact["artifactVersion"] == specialist_call["call_id"]
    assert persisted["call"]["requesting_agent_id"] != specialist["agent_id"]
    assert handoff["output_artifact_refs"] == ["artifact-review-target"]


def test_usable_review_with_one_upload_satisfies_run_completion_barrier() -> None:
    run, claim = _start_and_claim("task-review-completion-barrier")
    repo = CollaborationRepository()
    specialist = _unit(
        repo, run=run, claim=claim, agent="paper_evidence_extraction",
        key="unit-specialist", kind="specialist",
    )
    specialist_call = _start_call(
        repo, run=run, claim=claim, unit=specialist,
        agent="paper_evidence_extraction", key="specialist-call",
    )
    artifact_id = "artifact-reviewed-completion"
    _persist_artifact(
        task_id=run["task_id"], run_id=run["run_id"],
        call_id=specialist_call["call_id"], artifact_id=artifact_id,
    )
    _attach_confirmed_upload(
        run=run, claim=claim, call_id=specialist_call["call_id"], artifact_id=artifact_id,
    )
    repo.finish_capability_invocation(
        call_id=specialist_call["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="succeeded",
        summary="Evidence extracted", output_artifact_refs=[artifact_id],
    )
    reviewer = _unit(
        repo, run=run, claim=claim, agent="result_reliability_audit",
        key="review:unit-specialist", parent_work_unit_id=specialist["work_unit_id"],
        input_artifacts=[artifact_id], kind="review",
    )
    review_call = _start_call(
        repo, run=run, claim=claim, unit=reviewer,
        agent="result_reliability_audit", key="review-call", invocation_kind="review",
    )
    repo.finish_artifact_review(
        call_id=review_call["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], verdict="usable",
        basis="The artifact is usable.", issues=[], missing_inputs=[], artifact_refs=[artifact_id],
    )
    repo.complete_specialist_after_review(
        work_unit_id=specialist["work_unit_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"],
        artifact_refs=[artifact_id], summary="Independently reviewed",
    )

    terminal = DurableExecutionRepository().callback(
        run["run_id"], "T1-worker", claim["lease_epoch"], "succeeded", {},
    )

    assert terminal["state"] == "succeeded"
    assert _query(
        "SELECT kind, state FROM openair_execution_work_units WHERE run_id = %s ORDER BY sequence",
        (run["run_id"],),
    ) == [{"kind": "specialist", "state": "succeeded"}, {"kind": "review", "state": "succeeded"}]
    assert _query(
        "SELECT COUNT(*) AS count FROM openair_artifact_uploads WHERE run_id = %s",
        (run["run_id"],),
    )[0]["count"] == 1


def test_nonusable_review_atomically_pauses_run_behind_pending_gate() -> None:
    run, claim = _start_and_claim("task-review-awaiting-human")
    repo = CollaborationRepository()
    specialist = _unit(
        repo, run=run, claim=claim, agent="paper_evidence_extraction",
        key="unit-specialist", kind="specialist",
    )
    specialist_call = _start_call(
        repo, run=run, claim=claim, unit=specialist,
        agent="paper_evidence_extraction", key="specialist-call",
    )
    artifact_id = "artifact-needs-revision"
    _persist_artifact(
        task_id=run["task_id"], run_id=run["run_id"],
        call_id=specialist_call["call_id"], artifact_id=artifact_id,
    )
    repo.finish_capability_invocation(
        call_id=specialist_call["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="succeeded",
        summary="Evidence extracted", output_artifact_refs=[artifact_id],
    )
    reviewer = _unit(
        repo, run=run, claim=claim, agent="result_reliability_audit",
        key="review:unit-specialist", parent_work_unit_id=specialist["work_unit_id"],
        input_artifacts=[artifact_id], kind="review",
    )
    review_call = _start_call(
        repo, run=run, claim=claim, unit=reviewer,
        agent="result_reliability_audit", key="review-call", invocation_kind="review",
    )

    paused = repo.finish_artifact_review(
        call_id=review_call["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], verdict="needs_revision",
        basis="The evidence needs revision.", issues=[{
            "code": "missing_support", "severity": "blocking", "message": "Add supporting evidence.",
        }], missing_inputs=["supporting evidence"], artifact_refs=[artifact_id],
    )

    assert paused["gate"]["state"] == "pending"
    assert paused["gate"]["artifact_refs"] == [artifact_id]
    assert paused["run"]["state"] == "awaiting_human"
    assert paused["run"]["lease_owner"] is None and paused["run"]["lease_expires_at"] is None
    assert DurableExecutionRepository().claim("other-worker", 60) is None
    with pytest.raises(DurableExecutionError, match="human_gate_pending"):
        DurableExecutionRepository().callback(
            run["run_id"], "T1-worker", claim["lease_epoch"], "succeeded", {},
        )
    projection = CollaborationRepository().list_projection(
        task_id=run["task_id"], owner_id=OWNER, workspace_id=WORKSPACE,
    )
    assert projection["status"] == "awaiting_user_confirmation"
    assert projection["statusLabel"] == "等待你确认"
    assert projection["collaboration"]["needsUserDecision"] is True
    assert projection["collaboration"]["pendingDecisions"][0]["kind"] == "artifact_review"


def _persist_needs_input_review(*, task_id: str, continue_allowed: bool, severity: str) -> tuple[dict, dict, str]:
    run, claim = _start_and_claim(task_id)
    repo = CollaborationRepository()
    specialist = _unit(
        repo, run=run, claim=claim, agent="research_question_refinement",
        key="unit-question-refinement", kind="specialist",
    )
    specialist_call = _start_call(
        repo, run=run, claim=claim, unit=specialist,
        agent="research_question_refinement", key="question-refinement-call",
    )
    artifact_id = "artifact-question-refinement"
    _persist_artifact(
        task_id=run["task_id"], run_id=run["run_id"],
        call_id=specialist_call["call_id"], artifact_id=artifact_id,
    )
    repo.finish_capability_invocation(
        call_id=specialist_call["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="succeeded",
        summary="Valid question-refinement artifact", output_artifact_refs=[artifact_id],
    )
    reviewer = _unit(
        repo, run=run, claim=claim, agent="result_reliability_audit",
        key="review:unit-question-refinement", parent_work_unit_id=specialist["work_unit_id"],
        input_artifacts=[artifact_id], kind="review",
    )
    review_call = _start_call(
        repo, run=run, claim=claim, unit=reviewer,
        agent="result_reliability_audit", key="question-refinement-review", invocation_kind="review",
    )
    persisted = repo.finish_artifact_review(
        call_id=review_call["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], verdict="needs_input",
        basis="Question candidates are valid; additional scope detail is optional.",
        issues=[{
            "code": "scope_detail", "severity": severity,
            "message": "Additional scope detail is available from the user.",
        }],
        missing_inputs=["preferred scope detail"], artifact_refs=[artifact_id],
        continue_allowed=continue_allowed,
    )
    return run, persisted, artifact_id


def test_advisory_needs_input_preserves_review_and_allows_durable_progress() -> None:
    run, persisted, artifact_id = _persist_needs_input_review(
        task_id="task-advisory-needs-input", continue_allowed=True, severity="warning",
    )

    repo = CollaborationRepository()
    source = next(
        row for row in _query(
            "SELECT work_unit_id FROM openair_execution_work_units WHERE run_id = %s AND kind = 'specialist'",
            (run["run_id"],),
        )
    )
    next_unit = _unit(
        repo, run=run, claim={"lease_epoch": 1}, agent="key_literature_positioning",
        key="unit-literature", parent_work_unit_id=source["work_unit_id"], kind="specialist",
        input_artifacts=[artifact_id],
    )
    repo.complete_specialist_after_review(
        work_unit_id=source["work_unit_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=1, artifact_refs=[artifact_id],
        summary="Question refinement may continue with optional supplement.",
    )
    repo.handoff(
        from_work_unit_id=source["work_unit_id"], to_work_unit_id=next_unit["work_unit_id"],
        owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T1-worker", fence_epoch=1,
        summary="Continue using the valid question-refinement artifact.",
        output_artifact_refs=[artifact_id],
    )

    artifact = _query("SELECT metadata FROM openair_artifacts WHERE artifact_id = %s", (artifact_id,))[0]["metadata"]
    run_state = _query("SELECT state FROM openair_execution_runs WHERE run_id = %s", (run["run_id"],))[0]["state"]
    review_state = _query(
        "SELECT state, decision_summary FROM openair_execution_work_units WHERE work_unit_id = %s",
        (persisted["workUnit"]["work_unit_id"],),
    )[0]
    assert run_state == "leased"
    assert review_state == {"state": "succeeded", "decision_summary": "needs_input"}
    assert artifact["artifactState"] == "usable"
    assert artifact["validatorResult"]["verdict"] == "needs_input"
    assert artifact["validatorResult"]["reviewArtifactState"] == "needs_input"
    assert artifact["validatorResult"]["continueAllowed"] is True
    assert artifact["validatorResult"]["qualityAdvisory"] is True
    assert artifact["validatorResult"]["nextAction"] == "use_downstream"
    assert artifact["validatorResult"]["missingInputs"] == ["preferred scope detail"]
    assert artifact["validatorResult"]["issues"][0]["severity"] == "warning"
    assert artifact["humanGate"]["requiresHumanReview"] is False
    assert artifact["humanGate"]["canUseDownstream"] is True
    assert _query(
        "SELECT state FROM openair_execution_work_units WHERE work_unit_id = %s", (next_unit["work_unit_id"],),
    )[0]["state"] == "queued"


def test_blocking_needs_input_preserves_review_and_pauses_durable_run() -> None:
    run, persisted, artifact_id = _persist_needs_input_review(
        task_id="task-blocking-needs-input", continue_allowed=False, severity="blocking",
    )

    artifact = _query("SELECT metadata FROM openair_artifacts WHERE artifact_id = %s", (artifact_id,))[0]["metadata"]
    run_state = _query("SELECT state FROM openair_execution_runs WHERE run_id = %s", (run["run_id"],))[0]["state"]
    review_state = _query(
        "SELECT state, decision_summary FROM openair_execution_work_units WHERE work_unit_id = %s",
        (persisted["workUnit"]["work_unit_id"],),
    )[0]
    source_state = _query(
        "SELECT state FROM openair_execution_work_units WHERE run_id = %s AND kind = 'specialist'",
        (run["run_id"],),
    )[0]["state"]
    assert run_state == "awaiting_human"
    assert review_state == {"state": "blocked", "decision_summary": "needs_input"}
    assert source_state == "running"
    assert _query(
        "SELECT COUNT(*) AS count FROM openair_execution_handoffs WHERE run_id = %s", (run["run_id"],),
    )[0]["count"] == 0
    assert artifact["artifactState"] == "needs_input"
    assert artifact["validatorResult"]["verdict"] == "needs_input"
    assert artifact["validatorResult"]["continueAllowed"] is False
    assert artifact["validatorResult"]["missingInputs"] == ["preferred scope detail"]
    assert artifact["validatorResult"]["issues"][0]["severity"] == "blocking"


def test_existing_blocked_review_is_recovered_into_pending_gate() -> None:
    run, claim = _start_and_claim("task-review-pause-recovery")
    repo = CollaborationRepository()
    specialist = _unit(
        repo, run=run, claim=claim, agent="paper_evidence_extraction",
        key="unit-specialist", kind="specialist",
    )
    artifact_id = "artifact-review-pause-recovery"
    specialist_call = _start_call(
        repo, run=run, claim=claim, unit=specialist,
        agent="paper_evidence_extraction", key="specialist-call",
    )
    _persist_artifact(
        task_id=run["task_id"], run_id=run["run_id"],
        call_id=specialist_call["call_id"], artifact_id=artifact_id,
    )
    reviewer = _unit(
        repo, run=run, claim=claim, agent="result_reliability_audit",
        key="review:unit-specialist", parent_work_unit_id=specialist["work_unit_id"],
        input_artifacts=[artifact_id], kind="review",
    )
    review_call = _start_call(
        repo, run=run, claim=claim, unit=reviewer,
        agent="result_reliability_audit", key="review-call", invocation_kind="review",
    )
    repo.finish_capability_invocation(
        call_id=review_call["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="blocked",
        summary="Review requires a human decision.", output_artifact_refs=[],
    )

    recovered = repo.pause_for_artifact_review(
        run_id=run["run_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"],
    )

    assert recovered["run"]["state"] == "awaiting_human"
    assert recovered["gate"]["state"] == "pending"
    assert recovered["gate"]["work_unit_id"] == reviewer["work_unit_id"]
    assert recovered["gate"]["artifact_refs"] == [artifact_id]


def _paused_artifact_review(task_id: str) -> tuple[dict, dict, dict, str]:
    run, claim = _start_and_claim(task_id)
    repo = CollaborationRepository()
    specialist = _unit(
        repo, run=run, claim=claim, agent="paper_evidence_extraction",
        key="unit-specialist", kind="specialist",
    )
    specialist_call = _start_call(
        repo, run=run, claim=claim, unit=specialist,
        agent="paper_evidence_extraction", key="specialist-call",
    )
    artifact_id = f"artifact-{task_id}"
    _persist_artifact(
        task_id=run["task_id"], run_id=run["run_id"],
        call_id=specialist_call["call_id"], artifact_id=artifact_id,
    )
    repo.finish_capability_invocation(
        call_id=specialist_call["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="succeeded",
        summary="Evidence extracted", output_artifact_refs=[artifact_id],
    )
    reviewer = _unit(
        repo, run=run, claim=claim, agent="result_reliability_audit",
        key="review:unit-specialist", parent_work_unit_id=specialist["work_unit_id"],
        input_artifacts=[artifact_id], kind="review",
    )
    review_call = _start_call(
        repo, run=run, claim=claim, unit=reviewer,
        agent="result_reliability_audit", key="review-call", invocation_kind="review",
    )
    paused = repo.finish_artifact_review(
        call_id=review_call["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], verdict="needs_revision",
        basis="The evidence needs a new execution.", issues=[{
            "code": "missing_support", "severity": "blocking", "message": "Add supporting evidence.",
        }], missing_inputs=["supporting evidence"], artifact_refs=[artifact_id],
    )
    return run, paused["gate"], review_call, artifact_id


def test_gate_revision_decision_supersedes_old_run_and_creates_fresh_successor() -> None:
    run, gate, review_call, _artifact_id = _paused_artifact_review("task-gate-revision")
    repo = CollaborationRepository()

    decided = repo.decide_artifact_review_gate(
        task_id=run["task_id"], gate_id=gate["gate_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        decided_by=OWNER, action="start_revision_run", note="Run the research again with stronger support.",
    )

    assert decided["state"] == "resolved"
    assert decided["action"] == "start_revision_run"
    assert decided["successorRunId"]
    rows = _query(
        "SELECT run_id, run_number, run_kind, state, input_snapshot FROM openair_execution_runs WHERE task_id = %s ORDER BY run_number",
        (run["task_id"],),
    )
    assert [(row["run_id"], row["state"]) for row in rows] == [
        (run["run_id"], "superseded"),
        (decided["successorRunId"], "queued"),
    ]
    assert rows[1]["run_kind"] == "artifact_review_revision"
    assert rows[1]["input_snapshot"]["gateDecisionContext"] == {
        "sourceRunId": run["run_id"],
        "gateId": gate["gate_id"],
        "action": "start_revision_run",
        "note": "Run the research again with stronger support.",
    }
    assert _query(
        "SELECT COUNT(*) AS count FROM openair_execution_work_units WHERE run_id = %s",
        (decided["successorRunId"],),
    )[0]["count"] == 0
    assert _query(
        "SELECT state FROM openair_execution_capability_calls WHERE call_id = %s",
        (review_call["call_id"],),
    )[0]["state"] == "blocked"
    successor_calls = _query(
        "SELECT state, external_action_started, work_unit_id FROM openair_execution_capability_calls WHERE run_id = %s",
        (decided["successorRunId"],),
    )
    assert successor_calls == [{"state": "queued", "external_action_started": False, "work_unit_id": None}]
    claimed = DurableExecutionRepository().claim("revision-worker", 60)
    assert claimed and claimed["run_id"] == decided["successorRunId"]

    replay = repo.decide_artifact_review_gate(
        task_id=run["task_id"], gate_id=gate["gate_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        decided_by=OWNER, action="start_revision_run", note="Run the research again with stronger support.",
    )
    assert replay["successorRunId"] == decided["successorRunId"]
    assert len(_query("SELECT run_id FROM openair_execution_runs WHERE task_id = %s", (run["task_id"],))) == 2
    with pytest.raises(DurableExecutionError, match="execution_gate_decision_conflict"):
        repo.decide_artifact_review_gate(
            task_id=run["task_id"], gate_id=gate["gate_id"], owner_id=OWNER, workspace_id=WORKSPACE,
            decided_by="different-collaborator", action="start_revision_run",
            note="Run the research again with stronger support.",
        )
    with pytest.raises(DurableExecutionError, match="execution_gate_decision_conflict"):
        repo.decide_artifact_review_gate(
            task_id=run["task_id"], gate_id=gate["gate_id"], owner_id=OWNER, workspace_id=WORKSPACE,
            decided_by=OWNER, action="terminate_run", note=None,
        )
    stopped = DurableExecutionRepository().stop_current(run["task_id"], OWNER, WORKSPACE)
    assert stopped["status"] == "cancelled"
    final_runs = _query(
        "SELECT run_id, state FROM openair_execution_runs WHERE task_id = %s ORDER BY run_number",
        (run["task_id"],),
    )
    assert final_runs == [
        {"run_id": run["run_id"], "state": "superseded"},
        {"run_id": decided["successorRunId"], "state": "cancelled"},
    ]


def test_gate_terminate_decision_cancels_gate_run_and_task_without_requeue() -> None:
    run, gate, _review_call, _artifact_id = _paused_artifact_review("task-gate-terminate")
    repo = CollaborationRepository()

    decided = repo.decide_artifact_review_gate(
        task_id=run["task_id"], gate_id=gate["gate_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        decided_by=OWNER, action="terminate_run", note="Stop after the review finding.",
    )

    assert decided["state"] == "cancelled"
    assert decided["action"] == "terminate_run"
    assert decided["successorRunId"] is None
    assert _query(
        "SELECT state, failure_classification FROM openair_execution_runs WHERE run_id = %s",
        (run["run_id"],),
    ) == [{"state": "cancelled", "failure_classification": "stopped_by_user"}]
    assert _query(
        "SELECT status FROM openair_tasks WHERE task_id = %s", (run["task_id"],),
    )[0]["status"] == "cancelled"
    assert DurableExecutionRepository().claim("other-worker", 60) is None
    with pytest.raises(DurableExecutionError, match="stop_won"):
        DurableExecutionRepository().callback(
            run["run_id"], "T1-worker", 1, "succeeded", {},
        )
    replay = repo.decide_artifact_review_gate(
        task_id=run["task_id"], gate_id=gate["gate_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        decided_by=OWNER, action="terminate_run", note="Stop after the review finding.",
    )
    assert replay["state"] == "cancelled"


def test_general_stop_closes_pending_gate_with_authenticated_actor() -> None:
    run, gate, _review_call, _artifact_id = _paused_artifact_review("task-gate-general-stop")

    stopped = DurableExecutionRepository().stop_current(
        run["task_id"], OWNER, WORKSPACE, stopped_by="review-decision-user",
    )

    assert stopped["status"] == "cancelled"
    assert _query(
        """SELECT state, resolution_action, resolved_by FROM openair_execution_gates
           WHERE gate_id = %s""",
        (gate["gate_id"],),
    ) == [{
        "state": "cancelled", "resolution_action": "terminate_run",
        "resolved_by": "review-decision-user",
    }]
    projection = CollaborationRepository().list_projection(
        task_id=run["task_id"], owner_id=OWNER, workspace_id=WORKSPACE,
    )
    assert projection["collaboration"]["needsUserDecision"] is False
    assert projection["collaboration"]["pendingDecisions"] == []


def test_external_result_unknown_can_terminate_but_cannot_start_revision() -> None:
    run, gate, _review_call, _artifact_id = _paused_artifact_review("task-gate-unknown-terminate")
    with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            """UPDATE openair_execution_capability_calls
               SET state = 'unknown', failure_classification = 'external_result_unknown'
               WHERE call_id = %s""",
            (run["capability_call_id"],),
        )
    repo = CollaborationRepository()
    with pytest.raises(DurableExecutionError, match="external_result_unknown_no_automatic_replay"):
        repo.decide_artifact_review_gate(
            task_id=run["task_id"], gate_id=gate["gate_id"], owner_id=OWNER,
            workspace_id=WORKSPACE, decided_by=OWNER, action="start_revision_run",
        )

    terminated = repo.decide_artifact_review_gate(
        task_id=run["task_id"], gate_id=gate["gate_id"], owner_id=OWNER,
        workspace_id=WORKSPACE, decided_by=OWNER, action="terminate_run",
    )

    assert terminated["state"] == "cancelled"
    assert _query(
        "SELECT state FROM openair_execution_runs WHERE run_id = %s", (run["run_id"],),
    )[0]["state"] == "cancelled"


def test_artifact_review_gate_rejects_legacy_generic_resolution() -> None:
    run, gate, _review_call, _artifact_id = _paused_artifact_review("task-gate-no-generic-resolve")

    with pytest.raises(DurableExecutionError, match="artifact_review_requires_explicit_decision"):
        CollaborationRepository().resolve_gate(
            gate_id=gate["gate_id"], owner_id=OWNER, workspace_id=WORKSPACE,
            resolved_by=OWNER, state="resolved",
        )

    assert _query(
        "SELECT state FROM openair_execution_gates WHERE gate_id = %s", (gate["gate_id"],),
    )[0]["state"] == "pending"
    assert _query(
        "SELECT state FROM openair_execution_runs WHERE run_id = %s", (run["run_id"],),
    )[0]["state"] == "awaiting_human"


def test_gate_decision_scope_is_hidden_and_route_rejects_actor_spoofing() -> None:
    run, gate, _review_call, _artifact_id = _paused_artifact_review("task-gate-scope")
    repo = CollaborationRepository()
    with pytest.raises(DurableExecutionError, match="task not found"):
        repo.decide_artifact_review_gate(
            task_id=run["task_id"], gate_id=gate["gate_id"], owner_id="other-owner",
            workspace_id=WORKSPACE, decided_by="other-owner", action="terminate_run",
        )
    client = TestClient(app)
    response = client.post(
        f"/api/tasks/{run['task_id']}/collaboration/decisions/{gate['gate_id']}",
        headers={
            "X-OpenAIR-Owner-Id": OWNER,
            "X-OpenAIR-Workspace-Id": WORKSPACE,
            "X-OpenAIR-Created-By": OWNER,
        },
        json={"action": "terminate_run", "ownerId": "attacker"},
    )
    assert response.status_code == 422


def test_concurrent_gate_revision_and_termination_have_exactly_one_winner() -> None:
    run, gate, _review_call, _artifact_id = _paused_artifact_review("task-gate-decision-race")

    def decide(action: str) -> str:
        try:
            CollaborationRepository().decide_artifact_review_gate(
                task_id=run["task_id"], gate_id=gate["gate_id"], owner_id=OWNER,
                workspace_id=WORKSPACE, decided_by=OWNER, action=action,
            )
            return f"ok:{action}"
        except DurableExecutionError as exc:
            return f"error:{exc.detail}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(decide, ("start_revision_run", "terminate_run")))

    assert sum(outcome.startswith("ok:") for outcome in outcomes) == 1
    assert "error:execution_gate_decision_conflict" in outcomes
    persisted_gate = _query(
        "SELECT state, resolution_action, successor_run_id FROM openair_execution_gates WHERE gate_id = %s",
        (gate["gate_id"],),
    )[0]
    original = _query(
        "SELECT state FROM openair_execution_runs WHERE run_id = %s", (run["run_id"],),
    )[0]
    if persisted_gate["resolution_action"] == "start_revision_run":
        assert persisted_gate["state"] == "resolved" and persisted_gate["successor_run_id"]
        assert original["state"] == "superseded"
    else:
        assert persisted_gate == {
            "state": "cancelled", "resolution_action": "terminate_run", "successor_run_id": None,
        }
        assert original["state"] == "cancelled"


def test_agent_skill_request_is_atomic_and_queued_call_survives_cross_lease_recovery() -> None:
    run, claim = _start_and_claim("task-atomic-requested-skill")
    repo = CollaborationRepository()
    unit = _unit(repo, run=run, claim=claim, agent="agent-a", key="unit-request", kind="specialist")
    parent = _start_call(repo, run=run, claim=claim, unit=unit, agent="agent-a", key="parent-agent")
    request = _frozen_skill_request()

    persisted = repo.finish_agent_and_create_skill_call(
        call_id=parent["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], summary="Agent requested one Skill.",
        skill_id="materials_mp_constraint_search", skill_idempotency_key="requested-skill-key",
        skill_request_fingerprint="requested-skill-fingerprint", skill_request=request,
        input_memory_refs=[], input_artifact_refs=[],
    )
    repeated = repo.finish_agent_and_create_skill_call(
        call_id=parent["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], summary="Agent requested one Skill.",
        skill_id="materials_mp_constraint_search", skill_idempotency_key="requested-skill-key",
        skill_request_fingerprint="requested-skill-fingerprint", skill_request=request,
        input_memory_refs=[], input_artifact_refs=[],
    )
    with pytest.raises(DurableExecutionError, match="capability_invocation_idempotency_conflict"):
        repo.finish_agent_and_create_skill_call(
            call_id=parent["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
            worker_id="T1-worker", fence_epoch=claim["lease_epoch"], summary="Agent requested one Skill.",
            skill_id="materials_mp_constraint_search", skill_idempotency_key="requested-skill-key",
            skill_request_fingerprint="requested-skill-fingerprint",
            skill_request=_frozen_skill_request(binding_version=2),
            input_memory_refs=[], input_artifact_refs=[],
        )

    assert persisted["parentCall"]["state"] == "succeeded"
    assert repeated["skillCall"]["call_id"] == persisted["skillCall"]["call_id"]
    skill_call_id = persisted["skillCall"]["call_id"]
    stored = _query(
        """SELECT state, external_action_started, attempt_number, result_summary
           FROM openair_execution_capability_calls WHERE call_id = %s""",
        (skill_call_id,),
    )[0]
    assert stored["state"] == "queued"
    assert stored["external_action_started"] is False
    assert stored["attempt_number"] == 0
    assert stored["result_summary"].startswith("skill-request.v2:")
    assert orchestrator_module._persisted_skill_request(stored["result_summary"]) == request

    with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            "UPDATE openair_execution_runs SET lease_expires_at = NOW() - INTERVAL '1 second' WHERE run_id = %s",
            (run["run_id"],),
        )
    adopted = DurableExecutionRepository().claim("T2-worker", 60)
    assert adopted and adopted["run_id"] == run["run_id"]
    recovered = repo.find_recoverable_requested_skill_call(
        run_id=run["run_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        work_unit_id=unit["work_unit_id"], requesting_agent_id="agent-a",
    )
    assert recovered and recovered["call_id"] == skill_call_id and recovered["state"] == "queued"
    with pytest.raises(DurableExecutionError, match="stale_or_expired_lease"):
        repo.begin_capability_invocation(
            call_id=skill_call_id, owner_id=OWNER, workspace_id=WORKSPACE,
            worker_id="T1-worker", fence_epoch=claim["lease_epoch"],
            idempotency_key="requested-skill-key", request_fingerprint="requested-skill-fingerprint",
        )
    begun, already_started = repo.begin_capability_invocation(
        call_id=skill_call_id, owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T2-worker", fence_epoch=adopted["lease_epoch"],
        idempotency_key="requested-skill-key", request_fingerprint="requested-skill-fingerprint",
    )
    assert already_started is False
    assert begun["attempt_number"] == 1


def test_agent_skill_request_transaction_rolls_back_parent_finish_when_skill_is_invalid() -> None:
    run, claim = _start_and_claim("task-atomic-requested-skill-rollback")
    repo = CollaborationRepository()
    unit = _unit(repo, run=run, claim=claim, agent="agent-a", key="unit-request", kind="specialist")
    parent = _start_call(repo, run=run, claim=claim, unit=unit, agent="agent-a", key="parent-agent")

    with pytest.raises(DurableExecutionError, match="collaboration_artifact_reference_not_found"):
        repo.finish_agent_and_create_skill_call(
            call_id=parent["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
            worker_id="T1-worker", fence_epoch=claim["lease_epoch"], summary="Agent requested one Skill.",
            skill_id="materials_mp_constraint_search", skill_idempotency_key="requested-skill-key",
            skill_request_fingerprint="requested-skill-fingerprint",
            skill_request=_frozen_skill_request(input_artifact_refs=["missing-artifact"]),
            input_memory_refs=[], input_artifact_refs=["missing-artifact"],
        )

    calls = _query(
        "SELECT invocation_kind, state FROM openair_execution_capability_calls WHERE work_unit_id = %s ORDER BY created_at",
        (unit["work_unit_id"],),
    )
    assert calls == [{"invocation_kind": "agent_brain", "state": "running"}]


def test_unstarted_skill_call_can_fail_durably_and_idempotently() -> None:
    run, claim = _start_and_claim("task-unstarted-skill-failure")
    repo = CollaborationRepository()
    unit = _unit(repo, run=run, claim=claim, agent="agent-a", key="unit-unstarted", kind="specialist")
    repo.transition_work_unit(
        work_unit_id=unit["work_unit_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="running",
    )
    queued = _call(
        repo, run=run, claim=claim, unit=unit, agent="agent-a", key="queued-skill",
        invocation_kind="skill",
    )
    summary = "Frozen Skill authority is unavailable."

    failed = repo.fail_unstarted_capability_invocation(
        call_id=queued["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], summary=summary,
    )
    repeated = repo.fail_unstarted_capability_invocation(
        call_id=queued["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], summary=summary,
    )

    assert repeated["call_id"] == failed["call_id"]
    stored = _query(
        """SELECT state, external_action_started, attempt_number, started_at, ended_at,
                  failure_classification, output_artifact_refs
           FROM openair_execution_capability_calls WHERE call_id = %s""",
        (queued["call_id"],),
    )[0]
    assert stored["state"] == "failed"
    assert stored["external_action_started"] is False
    assert stored["attempt_number"] == 0
    assert stored["started_at"] is None
    assert stored["ended_at"] is not None
    assert stored["failure_classification"] == "skill_runtime_unavailable"
    assert stored["output_artifact_refs"] == []
    with pytest.raises(DurableExecutionError, match="capability_invocation_result_conflict"):
        repo.fail_unstarted_capability_invocation(
            call_id=queued["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
            worker_id="T1-worker", fence_epoch=claim["lease_epoch"], summary="different result",
        )
    with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            "UPDATE openair_execution_runs SET lease_expires_at = NOW() - INTERVAL '1 second' WHERE run_id = %s",
            (run["run_id"],),
        )
    with pytest.raises(DurableExecutionError, match="stale_or_expired_lease"):
        repo.fail_unstarted_capability_invocation(
            call_id=queued["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
            worker_id="T1-worker", fence_epoch=claim["lease_epoch"], summary=summary,
        )


def test_unstarted_failure_rejects_started_call_and_started_finish_accepts_runtime_unavailable() -> None:
    run, claim = _start_and_claim("task-started-skill-failure")
    repo = CollaborationRepository()
    unit = _unit(repo, run=run, claim=claim, agent="agent-a", key="unit-started", kind="specialist")
    call = _start_call(
        repo, run=run, claim=claim, unit=unit, agent="agent-a", key="started-skill",
        invocation_kind="skill",
    )

    with pytest.raises(DurableExecutionError, match="capability_invocation_already_started"):
        repo.fail_unstarted_capability_invocation(
            call_id=call["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
            worker_id="T1-worker", fence_epoch=claim["lease_epoch"], summary="unavailable",
        )
    finished = repo.finish_capability_invocation(
        call_id=call["call_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        worker_id="T1-worker", fence_epoch=claim["lease_epoch"], state="failed",
        summary="runtime unavailable", output_artifact_refs=[],
        failure_classification="skill_runtime_unavailable",
    )

    assert finished["state"] == "failed"
    assert finished["failure_classification"] == "skill_runtime_unavailable"


@pytest.mark.parametrize("path", ("ordered_skill_route", "specialist_requested_skill"))
def test_expired_lease_adopts_only_a_complete_persisted_call_without_a_second_attempt(path: str) -> None:
    run, claim = _start_and_claim(f"task-adopt-{path}")
    repo = CollaborationRepository()
    unit = _unit(repo, run=run, claim=claim, agent="agent-a", key=f"unit-{path}")
    call = _start_call(
        repo, run=run, claim=claim, unit=unit, agent="agent-a", key=f"call-{path}", invocation_kind="skill",
    )
    artifact_id = f"artifact-adopt-{path}"
    _persist_artifact(task_id=run["task_id"], run_id=run["run_id"], call_id=call["call_id"], artifact_id=artifact_id)
    _attach_confirmed_upload(run=run, claim=claim, call_id=call["call_id"], artifact_id=artifact_id)
    with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            """UPDATE openair_execution_capability_calls
               SET output_artifact_refs = %s::jsonb, result_summary = 'persisted before worker death'
               WHERE call_id = %s""",
            (f'["{artifact_id}"]', call["call_id"]),
        )
        conn.execute(
            "UPDATE openair_execution_runs SET lease_expires_at = NOW() - INTERVAL '1 second' WHERE run_id = %s",
            (run["run_id"],),
        )

    adopted = DurableExecutionRepository().claim("T2-worker", 60)

    assert adopted and adopted["run_id"] == run["run_id"]
    assert adopted["lease_epoch"] == claim["lease_epoch"] + 1
    persisted_call = _query(
        "SELECT state, fence_epoch, attempt_number, output_artifact_refs, result_summary FROM openair_execution_capability_calls WHERE call_id = %s",
        (call["call_id"],),
    )[0]
    assert persisted_call == {
        "state": "running", "fence_epoch": adopted["lease_epoch"], "attempt_number": 1,
        "output_artifact_refs": [artifact_id], "result_summary": "persisted before worker death",
    }
    recovered = repo.find_recoverable_requested_skill_call(
        run_id=run["run_id"], owner_id=OWNER, workspace_id=WORKSPACE,
        work_unit_id=unit["work_unit_id"], requesting_agent_id="agent-a",
    )
    assert recovered and recovered["call_id"] == call["call_id"]
    assert recovered["output_artifact_refs"] == [artifact_id]
    with pytest.raises(DurableExecutionError, match="stale_or_expired_lease"):
        repo.finish_capability_invocation(
            call_id=call["call_id"], owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T1-worker",
            fence_epoch=claim["lease_epoch"], state="succeeded", summary="persisted before worker death",
            output_artifact_refs=[artifact_id],
        )
    finished = repo.finish_capability_invocation(
        call_id=call["call_id"], owner_id=OWNER, workspace_id=WORKSPACE, worker_id="T2-worker",
        fence_epoch=adopted["lease_epoch"], state="succeeded", summary="persisted before worker death",
        output_artifact_refs=[artifact_id],
    )
    assert finished["state"] == "succeeded"
    assert "sourceExecutionRunId" not in _query(
        "SELECT metadata FROM openair_artifacts WHERE artifact_id = %s", (artifact_id,),
    )[0]["metadata"]


def test_expired_run_reuses_completed_call_when_only_recomputed_fingerprint_differs() -> None:
    run, claim = _start_and_claim("task-completed-call-recovery")
    repo = CollaborationRepository()
    unit = _unit(repo, run=run, claim=claim, agent="agent-a", key="unit-completed-recovery")
    call = _start_call(
        repo,
        run=run,
        claim=claim,
        unit=unit,
        agent="agent-a",
        key="call-completed-recovery",
    )
    artifact_id = "artifact-completed-call-recovery"
    _persist_artifact(
        task_id=run["task_id"],
        run_id=run["run_id"],
        call_id=call["call_id"],
        artifact_id=artifact_id,
    )
    _attach_confirmed_upload(
        run=run,
        claim=claim,
        call_id=call["call_id"],
        artifact_id=artifact_id,
    )
    repo.finish_capability_invocation(
        call_id=call["call_id"],
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        worker_id="T1-worker",
        fence_epoch=claim["lease_epoch"],
        state="succeeded",
        summary="Persisted before recovery",
        output_artifact_refs=[artifact_id],
    )
    with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            "UPDATE openair_execution_runs SET lease_expires_at = NOW() - INTERVAL '1 minute' WHERE run_id = %s",
            (run["run_id"],),
        )

    adopted = DurableExecutionRepository().claim("T2-worker", 60)
    assert adopted and adopted["run_id"] == run["run_id"]
    recovered = repo.create_capability_invocation(
        run_id=run["run_id"],
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        worker_id="T2-worker",
        fence_epoch=adopted["lease_epoch"],
        work_unit_id=unit["work_unit_id"],
        requesting_agent_id="agent-a",
        invocation_kind="agent_brain",
        idempotency_key="call-completed-recovery",
        request_fingerprint="recomputed-after-recovery",
    )

    assert recovered["call_id"] == call["call_id"]
    assert recovered["state"] == "succeeded"
    assert recovered["output_artifact_refs"] == [artifact_id]
    assert _query(
        "SELECT count(*) AS count FROM openair_execution_capability_calls WHERE work_unit_id = %s",
        (unit["work_unit_id"],),
    ) == [{"count": 1}]


def test_expired_lease_without_complete_persisted_call_remains_external_result_unknown() -> None:
    run, claim = _start_and_claim("task-adopt-incomplete")
    repo = CollaborationRepository()
    unit = _unit(repo, run=run, claim=claim, agent="agent-a", key="unit-incomplete")
    call = _start_call(repo, run=run, claim=claim, unit=unit, agent="agent-a", key="call-incomplete")
    with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            "UPDATE openair_execution_runs SET lease_expires_at = NOW() - INTERVAL '1 second' WHERE run_id = %s",
            (run["run_id"],),
        )

    assert DurableExecutionRepository().claim("T2-worker", 60) is None
    assert _query(
        "SELECT state, failure_classification FROM openair_execution_capability_calls WHERE call_id = %s",
        (call["call_id"],),
    )[0] == {"state": "abandoned", "failure_classification": "external_result_unknown"}


def test_expired_lease_does_not_adopt_an_unconfirmed_artifact_upload() -> None:
    run, claim = _start_and_claim("task-adopt-unconfirmed")
    repo = CollaborationRepository()
    unit = _unit(repo, run=run, claim=claim, agent="agent-a", key="unit-unconfirmed")
    call = _start_call(repo, run=run, claim=claim, unit=unit, agent="agent-a", key="call-unconfirmed")
    artifact_id = "artifact-adopt-unconfirmed"
    _persist_artifact(task_id=run["task_id"], run_id=run["run_id"], call_id=call["call_id"], artifact_id=artifact_id)
    _attach_confirmed_upload(run=run, claim=claim, call_id=call["call_id"], artifact_id=artifact_id)
    with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            "UPDATE openair_artifact_uploads SET state = 'pending', confirmed_at = NULL WHERE artifact_id = %s",
            (artifact_id,),
        )
        conn.execute(
            """UPDATE openair_execution_capability_calls
               SET output_artifact_refs = %s::jsonb, result_summary = 'unconfirmed upload'
               WHERE call_id = %s""",
            (f'["{artifact_id}"]', call["call_id"]),
        )
        conn.execute(
            "UPDATE openair_execution_runs SET lease_expires_at = NOW() - INTERVAL '1 second' WHERE run_id = %s",
            (run["run_id"],),
        )

    assert DurableExecutionRepository().claim("T2-worker", 60) is None
    assert _query(
        "SELECT state, failure_classification FROM openair_execution_capability_calls WHERE call_id = %s",
        (call["call_id"],),
    )[0] == {"state": "abandoned", "failure_classification": "external_result_unknown"}
