"""Contracts for persisted, caller-scoped Resource Plaza saves."""

from __future__ import annotations

from collections import defaultdict

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.modules.resource_catalog import get_resource
from app.routes import resource_saves as route
from app.modules import resource_saves as service


OWNER_A = "owner-a"
WORKSPACE_A = "workspace-a"
OWNER_B = "owner-b"
WORKSPACE_B = "workspace-b"
RESOURCE_ID = "skill:title_abstract_screening"


class _SaveRepositoryFixture:
    """Test double for the PostgreSQL repository; production has no fallback."""

    records: dict[tuple[str, str], list[str]] = defaultdict(list)

    def save(self, *, owner_id: str, workspace_id: str, resource_id: str) -> None:
        ids = self.records[(owner_id, workspace_id)]
        if resource_id not in ids:
            ids.append(resource_id)

    def remove(self, *, owner_id: str, workspace_id: str, resource_id: str) -> None:
        ids = self.records[(owner_id, workspace_id)]
        if resource_id in ids:
            ids.remove(resource_id)

    def list_resource_ids(self, *, owner_id: str, workspace_id: str) -> list[str]:
        return list(reversed(self.records[(owner_id, workspace_id)]))

    def saved_resource_ids(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        resource_ids: list[str],
    ) -> list[str]:
        own_ids = self.records[(owner_id, workspace_id)]
        return [resource_id for resource_id in resource_ids if resource_id in own_ids]


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    _SaveRepositoryFixture.records.clear()
    resource = get_resource(RESOURCE_ID)
    assert resource is not None
    public_resources = {RESOURCE_ID: resource}

    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(route, "ResourceSaveRepository", _SaveRepositoryFixture)
    monkeypatch.setattr(route, "get_public_resource", public_resources.get)
    monkeypatch.setattr(service, "get_public_resource", public_resources.get)
    return TestClient(app)


def _headers(owner: str, workspace: str) -> dict[str, str]:
    return {
        "X-OpenAIR-Owner-Id": owner,
        "X-OpenAIR-Workspace-Id": workspace,
    }


def _save(client: TestClient, owner: str = OWNER_A, workspace: str = WORKSPACE_A) -> None:
    response = client.put(f"/api/resource-saves/{RESOURCE_ID}", headers=_headers(owner, workspace))
    assert response.status_code == 200, response.text
    assert response.json() == {"resourceId": RESOURCE_ID, "saved": True}


def test_save_is_idempotent_and_listed_after_reentry(client: TestClient) -> None:
    _save(client)
    _save(client)

    response = client.get("/api/resource-saves?page=1&pageSize=24", headers=_headers(OWNER_A, WORKSPACE_A))
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [item["id"] for item in response.json()["items"]] == [RESOURCE_ID]
    assert len(_SaveRepositoryFixture.records[(OWNER_A, WORKSPACE_A)]) == 1


def test_remove_is_idempotent(client: TestClient) -> None:
    _save(client)
    headers = _headers(OWNER_A, WORKSPACE_A)
    assert client.delete(f"/api/resource-saves/{RESOURCE_ID}", headers=headers).json() == {
        "resourceId": RESOURCE_ID,
        "saved": False,
    }
    assert client.delete(f"/api/resource-saves/{RESOURCE_ID}", headers=headers).json() == {
        "resourceId": RESOURCE_ID,
        "saved": False,
    }
    assert client.get("/api/resource-saves", headers=headers).json()["items"] == []


def test_owner_and_workspace_scopes_do_not_cross(client: TestClient) -> None:
    _save(client, OWNER_A, WORKSPACE_A)
    _save(client, OWNER_A, WORKSPACE_B)
    _save(client, OWNER_B, WORKSPACE_A)

    assert client.get("/api/resource-saves", headers=_headers(OWNER_A, WORKSPACE_A)).json()["total"] == 1
    assert client.get("/api/resource-saves", headers=_headers(OWNER_A, "workspace-other")).json()["total"] == 0
    assert client.get("/api/resource-saves", headers=_headers(OWNER_B, WORKSPACE_A)).json()["total"] == 1


def test_status_only_returns_current_scope_saves(client: TestClient) -> None:
    _save(client, OWNER_B, WORKSPACE_B)

    headers = _headers(OWNER_A, WORKSPACE_A)
    response = client.get(
        f"/api/resource-saves/status?resourceId={RESOURCE_ID}",
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == {"savedResourceIds": []}

    _save(client, OWNER_A, WORKSPACE_A)
    response = client.get(
        f"/api/resource-saves/status?resourceId={RESOURCE_ID}&resourceId={RESOURCE_ID}",
        headers=headers,
    )
    assert response.json() == {"savedResourceIds": [RESOURCE_ID]}


def test_unknown_or_nonpublic_resource_cannot_create_a_save(client: TestClient) -> None:
    headers = _headers(OWNER_A, WORKSPACE_A)
    response = client.put("/api/resource-saves/skill:not-public", headers=headers)
    assert response.status_code == 404
    assert _SaveRepositoryFixture.records[(OWNER_A, WORKSPACE_A)] == []


def test_stale_saved_resource_is_filtered_from_list(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _save(client)
    monkeypatch.setattr(service, "get_public_resource", lambda _resource_id: None)

    response = client.get("/api/resource-saves", headers=_headers(OWNER_A, WORKSPACE_A))
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


def test_status_limit_and_unauthenticated_contract(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    query = "&".join(f"resourceId=skill:{index}" for index in range(route.MAX_STATUS_RESOURCE_IDS + 1))
    assert client.get(f"/api/resource-saves/status?{query}", headers=_headers(OWNER_A, WORKSPACE_A)).status_code == 422

    monkeypatch.setattr(settings, "auth_required", True)
    response = client.get("/api/resource-saves")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_non_postgres_backend_has_no_in_memory_save_path(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "persistence_backend", "json")
    response = client.put(f"/api/resource-saves/{RESOURCE_ID}", headers=_headers(OWNER_A, WORKSPACE_A))
    assert response.status_code == 503
    assert response.json()["detail"] == "resource_saves_requires_postgres"


def test_migration_enforces_scoped_uniqueness() -> None:
    from app.modules.postgres_migrations import MIGRATIONS_DIR

    sql = (MIGRATIONS_DIR / "20260804_01_resource_saves.sql").read_text(encoding="utf-8")
    assert "openair_resource_saves" in sql
    assert "PRIMARY KEY (owner_id, workspace_id, resource_id)" in sql
    assert "created_at TIMESTAMPTZ NOT NULL" in sql
