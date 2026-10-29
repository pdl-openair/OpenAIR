"""Read-only Generic Recovery API contract."""

from fastapi.testclient import TestClient

from app.main import app
from app.modules.recovery_repository import RecoveryError, RecoveryRepository
from app.modules.recovery_service import RecoveryService


def test_openapi_contract_documents_frozen_recovery_paths_and_structured_errors() -> None:
    document = app.openapi()
    response_properties = document["components"]["schemas"]["RecoveryRequestOut"]["properties"]
    assert {
        "recoveryRequestId", "taskId", "sourceRunId", "recoveryKind", "state", "expectedTaskRevision",
        "taskRevision", "successorRunId", "replayed", "allowedActions",
    } <= set(response_properties)
    paths = document["paths"]
    create = "/api/tasks/{task_id}/recovery-requests"
    detail = "/api/tasks/{task_id}/recovery-requests/{recovery_request_id}"
    assert {create, f"{detail}/confirm", detail} <= set(paths)
    assert {"post", "get"} <= set(paths[create])
    discovery_parameters = {parameter["name"] for parameter in paths[create]["get"]["parameters"]}
    assert {"sourceRunId", "expectedTaskRevision"} <= discovery_parameters
    assert "requestBody" not in paths[create]["post"]
    for operation in (paths[create]["post"], paths[f"{detail}/confirm"]["post"]):
        assert "410" in operation["responses"]
        assert operation["responses"]["410"]["content"]["application/json"]["schema"]["$ref"].endswith("/RecoveryErrorOut")
    assert {"404", "409", "503"} <= set(paths[detail]["get"]["responses"])
    codes = document["components"]["schemas"]["RecoveryErrorDetail"]["properties"]["code"]["enum"]
    assert {
        "recovery_not_found", "recovery_preflight_failed", "recovery_stale", "idempotency_payload_conflict",
        "recovery_mutex_conflict", "recovery_successor_exists", "recovery_terminal", "recovery_requires_postgres",
        "legacy_recovery_read_only",
    } <= set(codes)


def test_generic_recovery_posts_are_gone_for_failed_and_unknown_sources() -> None:
    client = TestClient(app)
    headers = {"X-OpenAIR-Owner-Id": "owner", "X-OpenAIR-Workspace-Id": "workspace"}
    for source_state in ("failed", "unknown"):
        response = client.post(
            f"/api/tasks/task-{source_state}/recovery-requests",
            headers=headers,
            json={"sourceRunId": f"run-{source_state}", "recoveryKind": "retry"},
        )
        assert response.status_code == 410
        assert response.json() == {"detail": {"code": "legacy_recovery_read_only"}}
    confirm = client.post(
        "/api/tasks/task-failed/recovery-requests/recovery-old/confirm",
        headers=headers,
    )
    assert confirm.status_code == 410
    assert confirm.json() == {"detail": {"code": "legacy_recovery_read_only"}}


def test_historical_detail_and_discovery_routes_remain_readable(monkeypatch) -> None:
    historical = {
        "recoveryRequestId": "recovery-old",
        "taskId": "task-old",
        "sourceRunId": "run-old",
        "recoveryKind": "replan",
        "state": "requested",
        "expectedTaskRevision": 7,
        "taskRevision": 7,
        "successorRunId": None,
        "replayed": True,
        "allowedActions": [],
        "preservedFacts": {
            "sourceRunState": "failed",
            "sourceRunNumber": 2,
            "preservedArtifactCount": 3,
        },
    }
    monkeypatch.setattr(RecoveryService, "get", lambda self, **kwargs: historical)
    monkeypatch.setattr(RecoveryService, "discover", lambda self, **kwargs: historical)
    client = TestClient(app)
    headers = {"X-OpenAIR-Owner-Id": "owner", "X-OpenAIR-Workspace-Id": "workspace"}
    detail = client.get(
        "/api/tasks/task-old/recovery-requests/recovery-old",
        headers=headers,
    )
    discover = client.get(
        "/api/tasks/task-old/recovery-requests?sourceRunId=run-old&expectedTaskRevision=7",
        headers=headers,
    )
    assert detail.status_code == discover.status_code == 200
    assert detail.json() == discover.json() == historical


def test_service_and_repository_write_surfaces_fail_before_storage(monkeypatch) -> None:
    repository = RecoveryRepository()
    monkeypatch.setattr(repository, "_connect", lambda: (_ for _ in ()).throw(AssertionError("storage reached")))
    for operation in (
        lambda: repository.create(task_id="task", owner_id="owner", workspace_id="workspace", body={}),
        lambda: repository.confirm(task_id="task", request_id="request", owner_id="owner", workspace_id="workspace"),
        lambda: RecoveryService(repository).create(),
        lambda: RecoveryService(repository).confirm(),
    ):
        try:
            operation()
        except RecoveryError as error:
            assert (error.status_code, error.code) == (410, "legacy_recovery_read_only")
        else:
            raise AssertionError("legacy recovery write unexpectedly succeeded")
