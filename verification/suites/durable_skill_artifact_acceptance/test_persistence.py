"""Real acceptance/readback proof; only a disposable Harness database is allowed."""

from __future__ import annotations

import hashlib
import os
from copy import deepcopy
from urllib.parse import urlparse
from uuid import uuid4

import psycopg
import pytest
from app.config import settings
from app.models.result_protocol import normalize_result_protocol
from app.modules.artifact_repository import ArtifactRepository
from app.modules.collaboration_repository import CollaborationRepository
from app.modules.durable_execution import DurableExecutionError, DurableExecutionRepository
from app.modules.postgres_migrations import apply_postgres_migrations
from app.modules.task_repository import TaskRepository
from app.modules.user_repository import UserRepository


@pytest.fixture
def acceptance_context(monkeypatch, tmp_path):
    dsn = os.environ.get("OPENAIR_TEST_POSTGRES_DSN", "")
    database = os.environ.get("OPENAIR_TEST_POSTGRES_DATABASE", "")
    parsed = urlparse(dsn)
    if (
        not os.environ.get("OPENAIR_TEST_POSTGRES_CONTAINER_ID")
        or not os.environ.get("OPENAIR_TEST_POSTGRES_RUN_ID")
        or not database.startswith("openair_test_")
        or parsed.path.lstrip("/") != database
        or parsed.hostname != "127.0.0.1"
    ):
        pytest.fail("Identified disposable testkit.postgres_harness database required")
    for name, value in {
        "postgres_host": parsed.hostname,
        "postgres_port": parsed.port,
        "postgres_user": parsed.username,
        "postgres_password": parsed.password,
        "postgres_database": database,
        "persistence_backend": "postgres",
        "artifact_storage": "local",
        "workspace_root": tmp_path,
    }.items():
        monkeypatch.setattr(settings, name, value)
    TaskRepository().bootstrap()
    UserRepository().bootstrap()
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            apply_postgres_migrations(cur)
    ArtifactRepository().bootstrap()
    durable = DurableExecutionRepository()
    durable.bootstrap()
    task_id = "task-bl01-" + uuid4().hex
    task = {
        "taskId": task_id,
        "ownerId": "bl01-owner",
        "workspaceId": "bl01-workspace",
        "createdBy": "bl01-owner",
        "title": "BL01 real artifact acceptance",
        "domain": "research_task",
        "currentStage": "problem_modeling",
        "status": "not_started",
        "creationStatus": "confirmed",
        "resultSource": "none",
        "executionMode": "stage_confirmation",
        "stages": ["problem_modeling"],
        "stageCatalog": [],
        "stageDetails": [
            {"stageKey": "problem_modeling", "status": "not_started", "subStages": []}
        ],
        "artifacts": [],
        "materials": [],
        "memoryRecords": [],
        "confirmations": [],
        "capabilityCalls": [],
    }
    TaskRepository().insert_task(task)
    run, _ = durable.start_initial(
        task_id=task_id,
        owner_id="bl01-owner",
        workspace_id="bl01-workspace",
        idempotency_key="start-" + task_id,
        source_mode="live",
    )
    claim = durable.claim("bl01-worker", 300)
    assert claim and claim["run_id"] == run["run_id"]
    durable.record_orchestration_plan(
        run["run_id"], "bl01-worker", claim["lease_epoch"], {"workUnits": []}
    )
    collaboration = CollaborationRepository()
    unit = collaboration.create_work_unit(
        run_id=run["run_id"],
        owner_id="bl01-owner",
        workspace_id="bl01-workspace",
        worker_id="bl01-worker",
        fence_epoch=claim["lease_epoch"],
        agent_id="research_question_refinement",
        kind="agent",
        title="Prepare result",
        unit_key="bl01-unit",
        objective="Prepare a bounded research result",
    )
    collaboration.transition_work_unit(
        work_unit_id=unit["work_unit_id"],
        owner_id="bl01-owner",
        workspace_id="bl01-workspace",
        worker_id="bl01-worker",
        fence_epoch=claim["lease_epoch"],
        state="running",
    )
    call = collaboration.create_capability_invocation(
        run_id=run["run_id"],
        owner_id="bl01-owner",
        workspace_id="bl01-workspace",
        worker_id="bl01-worker",
        fence_epoch=claim["lease_epoch"],
        work_unit_id=unit["work_unit_id"],
        requesting_agent_id="research_question_refinement",
        invocation_kind="skill",
        skill_id="intent_understanding",
        idempotency_key="bl01-call",
        request_fingerprint="bl01-fingerprint",
    )
    collaboration.begin_capability_invocation(
        call_id=call["call_id"],
        owner_id="bl01-owner",
        workspace_id="bl01-workspace",
        worker_id="bl01-worker",
        fence_epoch=claim["lease_epoch"],
        idempotency_key="bl01-call",
        request_fingerprint="bl01-fingerprint",
    )
    return dsn, tmp_path, durable, run, claim, call


def test_skill_result_persists_quality_and_rejects_invalid_content_or_authority(acceptance_context):
    dsn, storage, durable, run, claim, call = acceptance_context
    content = b"project: bounded research\n"
    digest = hashlib.sha256(content).hexdigest()
    protocol = normalize_result_protocol(
        {
            "accepted_items": [{"path": "queries", "count": 2}],
            "quarantined_items": [{"path": "queries[2]", "reason": "invalid query"}],
            "warnings": ["one query skipped"],
            "coverage": {"accepted_query_count": 2},
        }
    )
    metadata = {
        "runtime": "llm_reasoning",
        "skillId": "intent_understanding",
        "resultProtocol": protocol,
        "artifactQuality": "degraded",
        "warnings": protocol["warnings"],
    }
    presentation = {
        "title": "Research configuration",
        "mimeType": "text/yaml",
        "metadata": metadata,
        "contentSha256": digest,
        "contentSize": len(content),
    }

    def persist(
        value,
        *,
        call_id=call["call_id"],
        epoch=claim["lease_epoch"],
        result=content,
        related_skill_id="intent_understanding",
    ):
        return durable.persist_collaboration_artifact(
            run["run_id"],
            call_id,
            "bl01-worker",
            epoch,
            content=result,
            presentation=value,
            summary="Bounded result",
            related_skill_id=related_skill_id,
        )

    for field, value in [("contentSha256", "0" * 64), ("contentSize", len(content) + 1)]:
        invalid = deepcopy(presentation)
        invalid[field] = value
        with pytest.raises(DurableExecutionError, match="collaboration_artifact_checksum_mismatch"):
            persist(invalid)
    with pytest.raises(DurableExecutionError):
        persist(presentation, call_id="call-not-owned-by-this-run")
    with pytest.raises(DurableExecutionError):
        persist(presentation, epoch=claim["lease_epoch"] + 1)
    wrong_identity = deepcopy(presentation)
    wrong_identity["metadata"]["skillId"] = "another_skill"
    for value, related in [
        (wrong_identity, "intent_understanding"),
        (presentation, "another_skill"),
    ]:
        with pytest.raises(
            DurableExecutionError, match="skill_artifact_related_skill_mismatch"
        ) as rejected:
            persist(value, related_skill_id=related)
        assert rejected.value.status_code == 422
        with psycopg.connect(dsn) as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM openair_artifacts WHERE source_execution_run_id = %s",
                    (run["run_id"],),
                ).fetchone()[0]
                == 0
            )
        assert not list(storage.rglob("*")), "identity rejection must precede blob storage"
    with psycopg.connect(dsn) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM openair_artifacts WHERE source_execution_run_id = %s",
                (run["run_id"],),
            ).fetchone()[0]
            == 0
        )
    assert not list(storage.rglob("*")), "rejected results must not write local content"

    artifact_id = persist(presentation)
    assert persist(presentation) == artifact_id
    with pytest.raises(DurableExecutionError, match="collaboration_artifact_result_conflict"):
        persist(presentation, result=b"different result")
    with psycopg.connect(dsn) as conn:
        stored_metadata = conn.execute(
            "SELECT metadata FROM openair_artifacts WHERE artifact_id = %s", (artifact_id,)
        ).fetchone()[0]
    for key, expected in metadata.items():
        assert stored_metadata[key] == expected
    artifact = ArtifactRepository().get_by_id(artifact_id)
    assert artifact is not None
    assert artifact["resultProtocol"] == protocol
    assert artifact["artifactQuality"] == "degraded"
    assert artifact["warnings"] == protocol["warnings"]
    assert artifact["sourceExecutionRunId"] == run["run_id"]
    assert artifact["sourceCapabilityCallId"] == call["call_id"]
    assert artifact["contentSha256"] == digest
    assert artifact["contentSize"] == len(content)
    assert len(artifact["versions"]) == 1
    assert (storage / run["task_id"] / artifact["path"]).read_bytes() == content
    with psycopg.connect(dsn) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM openair_artifacts WHERE source_execution_run_id = %s",
                (run["run_id"],),
            ).fetchone()[0]
            == 1
        )
