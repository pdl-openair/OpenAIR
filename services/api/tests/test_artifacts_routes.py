"""Artifact-01 产物库路由测试（json 后端，无 postgres）。

覆盖：跨任务目录聚合 / 过滤 / 搜索 / 分页 / 详情 / 内容 / 下载 / 复用，
以及 owner/workspace scope 隔离（403）与不存在（404）。
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.modules.artifact_gate import human_gate_for_state
from app.modules.artifact_repository import ArtifactRepository
from app.modules.task_repository import TaskRepository
from app.modules.task_store import store
from app.routes import artifacts as artifacts_route
from app.routes.artifacts import _origin_source_mode

OWNER_A = "ownerA"
WS_A = "wsA"
OWNER_B = "ownerB"
WS_B = "wsB"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolated_store():
    original = store._tasks.copy()
    store._tasks.clear()
    try:
        yield
    finally:
        store._tasks.clear()
        store._tasks.update(original)


def _headers(owner: str, ws: str) -> dict[str, str]:
    return {"X-OpenAIR-Owner-Id": owner, "X-OpenAIR-Workspace-Id": ws}


def _create_task(owner: str, ws: str, title: str = "task") -> str:
    task = store.create_task(
        {
            "title": title,
            "goalSummary": "catalog test",
            "domain": "research_task",
            "currentStage": "problem_modeling",
            "status": "running",
            "ownerId": owner,
            "workspaceId": ws,
        }
    )
    return str(task["taskId"])


def _add_artifact(
    task_id: str,
    *,
    kind: str = "report",
    title: str = "art",
    summary: str | None = None,
    fmt: str = "markdown",
    storage: str | None = None,
    inline: str | None = None,
    artifact_id: str | None = None,
    extra_fields: dict | None = None,
) -> dict | None:
    return store.add_artifact(
        task_id,
        kind=kind,
        title=title,
        summary=summary,
        format=fmt,
        storage_type=storage,
        inline_content=inline,
        artifact_id=artifact_id,
        extra_fields=extra_fields,
    )


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_returns_only_scoped_artifacts(client: TestClient) -> None:
    ta = _create_task(OWNER_A, WS_A, "task A")
    _add_artifact(ta, title="A1", artifact_id="art_a1")
    tb = _create_task(OWNER_B, WS_B, "task B")
    _add_artifact(tb, title="B1", artifact_id="art_b1")

    ra = client.get("/api/artifacts", headers=_headers(OWNER_A, WS_A))
    assert ra.status_code == 200
    assert [a["id"] for a in ra.json()["artifacts"]] == ["art_a1"]

    rb = client.get("/api/artifacts", headers=_headers(OWNER_B, WS_B))
    assert [a["id"] for a in rb.json()["artifacts"]] == ["art_b1"]


def test_postgres_list_reads_first_class_artifact_catalog(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    row = {
        "id": "artifact:durable:1",
        "taskId": "task_durable",
        "ownerId": OWNER_A,
        "workspaceId": WS_A,
        "createdBy": OWNER_A,
        "kind": "execution_output",
        "title": "Durable research output",
        "createdAt": "2026-07-31T07:00:00+00:00",
        "sourceTaskId": "task_durable",
        "sourceTaskTitle": "Durable task",
        "artifactState": "usable",
        "resultProtocol": {
            "version": "result-protocol.v1",
            "status": "degraded",
            "accepted_items": [{"path": "queries", "count": 7}],
            "quarantined_items": [
                {"path": "queries[7]", "reason": "invalid_query"}
            ],
            "repaired_items": [],
            "warnings": ["one query was skipped"],
            "coverage": {"accepted_query_count": 7, "input_query_count": 8},
        },
        "artifactQuality": "degraded",
        "warnings": ["one query was skipped"],
    }
    monkeypatch.setattr(
        ArtifactRepository,
        "list_by_scope",
        lambda self, owner_id, workspace_id, **kwargs: ([row], 1),
    )

    response = client.get(
        "/api/artifacts?sourceTaskId=task_durable",
        headers=_headers(OWNER_A, WS_A),
    )

    assert response.status_code == 200, response.text
    artifact = response.json()["artifacts"][0]
    assert artifact["id"] == row["id"]
    assert artifact["sourceTaskId"] == row["sourceTaskId"]
    assert artifact["ownerId"] == OWNER_A
    assert artifact["workspaceId"] == WS_A
    assert artifact["artifactState"] == "usable"
    assert artifact["resultProtocol"]["status"] == "degraded"
    assert artifact["resultProtocol"]["quarantined_items"] == [
        {"path": "queries[7]", "reason": "invalid_query"}
    ]
    assert artifact["artifactQuality"] == "degraded"
    assert artifact["warnings"] == ["one query was skipped"]


def test_postgres_content_reads_the_catalog_artifact_without_taskstore(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    durable = {
        "id": "artifact:durable:content",
        "taskId": "task_durable",
        "sourceTaskId": "task_durable",
        "sourceTaskTitle": "Durable task",
        "ownerId": OWNER_A,
        "workspaceId": WS_A,
        "createdBy": OWNER_A,
        "kind": "execution_output",
        "title": "Durable research output",
        "createdAt": "2026-07-31T07:00:00+00:00",
        "artifactState": "usable",
        "storageType": "inline",
        "inlineContent": "# Durable body\n\nStored in PostgreSQL.",
        "contentSha256": hashlib.sha256(b"# Durable body\n\nStored in PostgreSQL.").hexdigest(),
        "contentSize": len(b"# Durable body\n\nStored in PostgreSQL."),
        "format": "markdown",
    }
    monkeypatch.setattr(ArtifactRepository, "get_by_id", lambda self, artifact_id: durable if artifact_id == durable["id"] else None)
    monkeypatch.setattr(
        ArtifactRepository,
        "get_storage_reference",
        lambda self, **kwargs: {
            "storage_type": "inline",
            "storage_scope": durable["id"],
            "storage_ref": durable["contentSha256"],
            "content_sha256": durable["contentSha256"],
            "content_size": durable["contentSize"],
        },
    )
    monkeypatch.setattr(
        artifacts_route.store,
        "get_task",
        lambda _task_id: pytest.fail("PostgreSQL artifact reads must not use TaskStore"),
    )

    detail = client.get(f"/api/artifacts/{durable['id']}", headers=_headers(OWNER_A, WS_A))
    content = client.get(f"/api/artifacts/{durable['id']}/content", headers=_headers(OWNER_A, WS_A))

    assert detail.status_code == 200, detail.text
    assert content.status_code == 200, content.text
    assert detail.json()["sourceTaskId"] == "task_durable"
    assert content.json()["content"] == durable["inlineContent"]
    assert content.json()["sourceMode"] == "inline"


def test_postgres_review_and_scope_confirmation_use_durable_writers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    durable = {
        "id": "artifact:durable:pending",
        "taskId": "task_durable",
        "sourceTaskId": "task_durable",
        "sourceTaskTitle": "Durable task",
        "ownerId": OWNER_A,
        "workspaceId": WS_A,
        "createdBy": OWNER_A,
        "kind": "execution_output",
        "title": "Pending durable output",
        "createdAt": "2026-07-31T07:00:00+00:00",
        "artifactState": "needs_review",
    }
    task = {
        "taskId": "task_durable", "ownerId": OWNER_A, "workspaceId": WS_A,
        "createdBy": OWNER_A, "title": "Durable task",
    }
    monkeypatch.setattr(TaskRepository, "get_task", lambda self, task_id: task if task_id == "task_durable" else None)
    monkeypatch.setattr(
        ArtifactRepository,
        "review_artifact",
        lambda self, **kwargs: {**durable, "artifactState": "usable"},
    )
    monkeypatch.setattr(
        ArtifactRepository,
        "confirm_scope_expansion",
        lambda self, **kwargs: {**durable, "artifactState": "needs_input"},
    )
    monkeypatch.setattr(
        artifacts_route.store,
        "update_artifact_review_state",
        lambda *args, **kwargs: pytest.fail("PostgreSQL review must not write TaskStore"),
    )
    monkeypatch.setattr(
        artifacts_route.store,
        "confirm_artifact_scope_expansion",
        lambda *args, **kwargs: pytest.fail("PostgreSQL scope confirmation must not write TaskStore"),
    )

    review = client.post(
        f"/api/artifacts/{durable['id']}/review",
        json={"decision": "approved"},
        headers=_headers(OWNER_A, WS_A),
    )
    scope = client.post(
        f"/api/artifacts/{durable['id']}/confirm-scope-expansion",
        json={},
        headers=_headers(OWNER_A, WS_A),
    )

    assert review.status_code == 200, review.text
    assert scope.status_code == 200, scope.text
    assert review.json()["artifactState"] == "usable"
    assert scope.json()["artifactState"] == "needs_input"


def test_content_source_mode_is_actual_resolver_mode() -> None:
    assert _origin_source_mode(
        {"sourceCapabilityCallId": "call:durable:1", "sourceMode": "live"},
        "local",
    ) == "local"


def test_list_hides_internal_audit_artifacts_without_keyword_filtering(client: TestClient) -> None:
    task_id = _create_task(OWNER_A, WS_A)
    _add_artifact(
        task_id,
        title="Adapter execution report for model=none baseline",
        summary="Normal research artifact containing technical comparison terms.",
        artifact_id="user_visible_terms",
        extra_fields={"artifactVisibility": "user", "audience": "research_user"},
    )
    _add_artifact(
        task_id,
        title="Preview Live run proof",
        artifact_id="audit_hidden",
        extra_fields={"artifactVisibility": "audit", "audience": "internal"},
    )

    r = client.get("/api/artifacts", headers=_headers(OWNER_A, WS_A))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert [a["id"] for a in body["artifacts"]] == ["user_visible_terms"]
    assert body["artifacts"][0]["artifactVisibility"] == "user"
    assert body["artifacts"][0]["audience"] == "research_user"


def test_list_filter_by_kind(client: TestClient) -> None:
    t = _create_task(OWNER_A, WS_A)
    _add_artifact(t, kind="report", title="r", artifact_id="a_report")
    _add_artifact(t, kind="dataset", title="d", artifact_id="a_data")

    r = client.get("/api/artifacts?kind=report", headers=_headers(OWNER_A, WS_A))
    assert [a["id"] for a in r.json()["artifacts"]] == ["a_report"]


def test_list_filter_by_origin(client: TestClient) -> None:
    t = _create_task(OWNER_A, WS_A)
    _add_artifact(t, title="gen", artifact_id="a_gen")  # default origin → generated
    store.add_artifact(
        t, kind="report", title="imp", artifact_id="a_imp", extra_fields={"origin": "imported"}
    )

    r = client.get("/api/artifacts?origin=imported", headers=_headers(OWNER_A, WS_A))
    assert [a["id"] for a in r.json()["artifacts"]] == ["a_imp"]

    r2 = client.get("/api/artifacts?origin=generated", headers=_headers(OWNER_A, WS_A))
    assert [a["id"] for a in r2.json()["artifacts"]] == ["a_gen"]


def test_list_filter_by_source_task_id(client: TestClient) -> None:
    t1 = _create_task(OWNER_A, WS_A, "t1")
    t2 = _create_task(OWNER_A, WS_A, "t2")
    _add_artifact(t1, title="x", artifact_id="a1")
    _add_artifact(t2, title="y", artifact_id="a2")

    r = client.get(f"/api/artifacts?sourceTaskId={t1}", headers=_headers(OWNER_A, WS_A))
    assert [a["id"] for a in r.json()["artifacts"]] == ["a1"]


def test_list_search_q_matches_title_and_summary(client: TestClient) -> None:
    t = _create_task(OWNER_A, WS_A)
    _add_artifact(t, title="Lithium Report", summary="about batteries", artifact_id="a1")
    _add_artifact(t, title="Other", summary="nothing", artifact_id="a2")

    assert [a["id"] for a in client.get(
        "/api/artifacts?q=lithium", headers=_headers(OWNER_A, WS_A)
    ).json()["artifacts"]] == ["a1"]
    # 大小写不敏感，命中 summary
    assert [a["id"] for a in client.get(
        "/api/artifacts?q=BATTERIES", headers=_headers(OWNER_A, WS_A)
    ).json()["artifacts"]] == ["a1"]


def test_list_search_inline_optional(client: TestClient) -> None:
    t = _create_task(OWNER_A, WS_A)
    _add_artifact(t, title="t", inline="secret payload inside", artifact_id="a1")

    no_inline = client.get("/api/artifacts?q=secret", headers=_headers(OWNER_A, WS_A))
    assert no_inline.json()["artifacts"] == []

    with_inline = client.get(
        "/api/artifacts?q=secret&searchInline=true", headers=_headers(OWNER_A, WS_A)
    )
    assert [a["id"] for a in with_inline.json()["artifacts"]] == ["a1"]


def test_list_pagination(client: TestClient) -> None:
    t = _create_task(OWNER_A, WS_A)
    for i in range(3):
        _add_artifact(t, title=f"art{i}", artifact_id=f"a{i}")

    r1 = client.get("/api/artifacts?page=1&pageSize=2", headers=_headers(OWNER_A, WS_A))
    body = r1.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["pageSize"] == 2
    assert len(body["artifacts"]) == 2

    r2 = client.get("/api/artifacts?page=2&pageSize=2", headers=_headers(OWNER_A, WS_A))
    assert len(r2.json()["artifacts"]) == 1


# ---------------------------------------------------------------------------
# detail / content / download
# ---------------------------------------------------------------------------


def test_detail_returns_source_task_id(client: TestClient) -> None:
    t = _create_task(OWNER_A, WS_A, "my task")
    _add_artifact(t, title="art", artifact_id="a1")

    r = client.get("/api/artifacts/a1", headers=_headers(OWNER_A, WS_A))
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "a1"
    assert body["sourceTaskId"] == t
    assert body["sourceTaskTitle"] == "my task"


def test_detail_404_unknown(client: TestClient) -> None:
    r = client.get("/api/artifacts/nope", headers=_headers(OWNER_A, WS_A))
    assert r.status_code == 404


def test_detail_403_out_of_scope(client: TestClient) -> None:
    tb = _create_task(OWNER_B, WS_B)
    _add_artifact(tb, title="b", artifact_id="b1")

    r = client.get("/api/artifacts/b1", headers=_headers(OWNER_A, WS_A))
    assert r.status_code == 403


def test_content_resolves_inline(client: TestClient) -> None:
    t = _create_task(OWNER_A, WS_A)
    _add_artifact(t, title="a", inline="# hello world", storage="inline", artifact_id="a1")

    r = client.get("/api/artifacts/a1/content", headers=_headers(OWNER_A, WS_A))
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == "# hello world"
    assert body["sourceMode"] == "inline"


def test_content_403_out_of_scope(client: TestClient) -> None:
    tb = _create_task(OWNER_B, WS_B)
    _add_artifact(tb, title="b", inline="x", artifact_id="b1")

    r = client.get("/api/artifacts/b1/content", headers=_headers(OWNER_A, WS_A))
    assert r.status_code == 403


def test_download_inline_returns_200(client: TestClient) -> None:
    t = _create_task(OWNER_A, WS_A)
    _add_artifact(t, title="a", inline="# data", storage="inline", artifact_id="a1")

    r = client.get("/api/artifacts/a1/download", headers=_headers(OWNER_A, WS_A))
    assert r.status_code == 200


def test_download_external_returns_presigned_url(client: TestClient) -> None:
    """Artifact-02: external blob download returns a short-lived presigned GET url."""
    from unittest.mock import patch

    t = _create_task(OWNER_A, WS_A)
    store.add_artifact(
        t, kind="model", title="m", format="safetensors",
        storage_type="external", artifact_id="a1", extra_fields={"externalKey": "task_x/obj"},
    )

    with patch(
        "app.modules.minio_client.presigned_get_object",
        return_value="http://mock-minio/task_x/obj?sig=abc",
    ):
        r = client.get("/api/artifacts/a1/download", headers=_headers(OWNER_A, WS_A))
    assert r.status_code == 200
    assert r.json()["url"].startswith("http://mock-minio/")


def test_download_403_out_of_scope(client: TestClient) -> None:
    tb = _create_task(OWNER_B, WS_B)
    _add_artifact(tb, title="b", inline="x", artifact_id="b1")

    r = client.get("/api/artifacts/b1/download", headers=_headers(OWNER_A, WS_A))
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# reuse
# ---------------------------------------------------------------------------


def test_reuse_creates_imported_artifact(client: TestClient) -> None:
    src = _create_task(OWNER_A, WS_A, "src")
    _add_artifact(
        src,
        kind="report",
        title="original",
        summary="s",
        artifact_id="a_src",
        extra_fields={"artifactState": "usable"},
    )
    tgt = _create_task(OWNER_A, WS_A, "tgt")

    r = client.post(
        "/api/artifacts/a_src/reuse",
        json={"targetTaskId": tgt},
        headers=_headers(OWNER_A, WS_A),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["sourceArtifactId"] == "a_src"
    assert body["sourceTaskId"] == src
    assert body["artifact"]["origin"] == "imported"
    assert body["artifact"]["artifactState"] == "usable"
    assert body["artifact"]["humanGate"]["canUseDownstream"] is True

    # 持久化 provenance 校验
    target = store.get_task(tgt)
    assert target is not None
    new_art = next(a for a in target["artifacts"] if a["id"] == body["artifact"]["id"])
    assert new_art["origin"] == "imported"
    assert new_art["artifactState"] == "usable"
    assert new_art["metadata"]["reusedFrom"]["artifactId"] == "a_src"
    assert new_art["metadata"]["reusedFrom"]["taskId"] == src


def test_reuse_copies_local_content_for_target_task(client: TestClient, tmp_path: Path) -> None:
    src = _create_task(OWNER_A, WS_A, "source")
    target = _create_task(OWNER_A, WS_A, "target")
    source_file = tmp_path / "source-note.md"
    source_file.write_text("# Source-bounded note", encoding="utf-8")
    store.add_artifact(
        src,
        kind="report",
        title="source note",
        path=str(source_file),
        format="markdown",
        artifact_id="a_local",
        extra_fields={"artifactState": "usable"},
    )

    response = client.post(
        "/api/artifacts/a_local/reuse",
        json={"targetTaskId": target},
        headers=_headers(OWNER_A, WS_A),
    )

    assert response.status_code == 200
    imported = response.json()["artifact"]
    assert imported["storageType"] == "inline"
    assert imported["path"] is None
    assert "inlineContent" not in imported
    content = client.get(
        f"/api/artifacts/{imported['id']}/content",
        headers=_headers(OWNER_A, WS_A),
    )
    assert content.status_code == 200
    assert content.json()["content"] == "# Source-bounded note"


def test_reuse_rejects_artifact_not_usable_downstream(client: TestClient) -> None:
    src = _create_task(OWNER_A, WS_A, "src")
    _add_artifact(
        src,
        kind="report",
        title="needs review",
        summary="s",
        artifact_id="a_pending",
        extra_fields={"artifactState": "needs_review"},
    )
    tgt = _create_task(OWNER_A, WS_A, "tgt")

    r = client.post(
        "/api/artifacts/a_pending/reuse",
        json={"targetTaskId": tgt},
        headers=_headers(OWNER_A, WS_A),
    )

    assert r.status_code == 409
    assert "not usable for downstream reuse" in r.json()["detail"]
    assert store.get_task(tgt)["artifacts"] == []


def test_reuse_403_when_target_task_not_owned(client: TestClient) -> None:
    src = _create_task(OWNER_A, WS_A)
    _add_artifact(src, title="a", artifact_id="a_src", extra_fields={"artifactState": "usable"})
    tgt_b = _create_task(OWNER_B, WS_B)  # 归 B，A 不能写入

    r = client.post(
        "/api/artifacts/a_src/reuse",
        json={"targetTaskId": tgt_b},
        headers=_headers(OWNER_A, WS_A),
    )
    assert r.status_code == 403


def test_reuse_403_when_source_artifact_out_of_scope(client: TestClient) -> None:
    src_b = _create_task(OWNER_B, WS_B)
    _add_artifact(src_b, title="b", artifact_id="b_src")
    tgt = _create_task(OWNER_A, WS_A)

    r = client.post(
        "/api/artifacts/b_src/reuse",
        json={"targetTaskId": tgt},
        headers=_headers(OWNER_A, WS_A),
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------


def test_review_marks_artifact_verified_and_updates_catalog(client: TestClient) -> None:
    task_id = _create_task(OWNER_A, WS_A, "review task")
    store.add_artifact(
        task_id,
        kind="research_note",
        title="Needs review",
        artifact_id="a_review",
        storage_type="inline",
        inline_content="# draft",
        extra_fields={"artifactState": "pending_review"},
    )

    r = client.post(
        "/api/artifacts/a_review/review",
        json={"decision": "approved", "note": "Checked by test user."},
        headers=_headers(OWNER_A, WS_A),
    )

    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "a_review"
    assert body["artifactState"] == "usable"
    assert body["sourceTaskId"] == task_id
    assert body["validatorResult"]["usable"] is True
    assert body["humanGate"]["canUseDownstream"] is True

    task = store.get_task(task_id)
    artifact = next(a for a in task["artifacts"] if a["id"] == "a_review")
    assert artifact["artifactState"] == "usable"
    assert artifact["reviewSource"] == "user"
    assert artifact["trustRecords"][-1]["trustContribution"] == "evidence"

    listed = client.get("/api/artifacts", headers=_headers(OWNER_A, WS_A)).json()["artifacts"]
    assert listed[0]["artifactState"] == "usable"
    assert listed[0]["humanGate"]["canUseDownstream"] is True


def test_review_rejects_research_start_draft(client: TestClient) -> None:
    task_id = _create_task(OWNER_A, WS_A, "starter draft task")
    store.add_artifact(
        task_id,
        kind="research_start_draft",
        title="Research start draft",
        artifact_id="a_start_draft",
        storage_type="inline",
        inline_content="# draft",
        extra_fields={"artifactState": "pending_review", "artifactType": "research_start_draft"},
    )

    r = client.post(
        "/api/artifacts/a_start_draft/review",
        json={"decision": "approved"},
        headers=_headers(OWNER_A, WS_A),
    )

    assert r.status_code == 409
    assert "not reviewable research outputs" in r.json()["detail"]
    task = store.get_task(task_id)
    artifact = next(a for a in task["artifacts"] if a["id"] == "a_start_draft")
    assert artifact["artifactState"] == "needs_review"


def test_review_403_out_of_scope(client: TestClient) -> None:
    task_id = _create_task(OWNER_B, WS_B, "other review task")
    store.add_artifact(
        task_id,
        kind="research_note",
        title="Other owner",
        artifact_id="b_review",
        extra_fields={"artifactState": "pending_review"},
    )

    r = client.post(
        "/api/artifacts/b_review/review",
        json={"decision": "approved"},
        headers=_headers(OWNER_A, WS_A),
    )

    assert r.status_code == 403


def test_review_rejects_artifact_without_confirmable_gate(client: TestClient) -> None:
    task_id = _create_task(OWNER_A, WS_A, "no artifact task")
    for state in ("insufficient_materials", "failed", "needs_input", "partial_success"):
        store.add_artifact(
            task_id,
            kind="research_note",
            title=f"Blocked {state}",
            artifact_id=f"a_{state}",
            storage_type="inline",
            inline_content="# blocked",
            extra_fields={"artifactState": state},
        )

        r = client.post(
            f"/api/artifacts/a_{state}/review",
            json={"decision": "approved"},
            headers=_headers(OWNER_A, WS_A),
        )

        assert r.status_code == 409
        assert "cannot be confirmed as usable" in r.json()["detail"]
        artifact = next(a for a in store.get_task(task_id)["artifacts"] if a["id"] == f"a_{state}")
        assert artifact["artifactState"] == state
        assert artifact["humanGate"]["canConfirmPass"] is False


def test_confirm_scope_expansion_moves_partial_success_to_needs_input(client: TestClient) -> None:
    task_id = _create_task(OWNER_A, WS_A, "partial scope task")
    store.add_artifact(
        task_id,
        kind="stage_summary",
        title="Partial summary",
        artifact_id="a_partial_scope",
        storage_type="inline",
        inline_content="# partial",
        extra_fields={"artifactState": "partial_success"},
    )

    before = client.get("/api/artifacts/a_partial_scope", headers=_headers(OWNER_A, WS_A)).json()
    assert before["humanGate"]["allowedActions"] == ["confirm_scope_expansion"]
    assert before["humanGate"]["primaryAction"] == "confirm_scope_expansion"

    r = client.post(
        "/api/artifacts/a_partial_scope/confirm-scope-expansion",
        json={"note": "Scope expansion accepted by test user."},
        headers=_headers(OWNER_A, WS_A),
    )

    assert r.status_code == 200
    body = r.json()
    assert body["artifactState"] == "needs_input"
    assert body["validatorResult"]["usable"] is False
    assert body["validatorResult"]["missingInputs"] == ["expanded_scope_materials_or_execution"]
    assert body["humanGate"]["canUseDownstream"] is False
    assert body["humanGate"]["canConfirmPass"] is False
    assert body["humanGate"]["allowedActions"] == []
    assert body["humanGate"]["primaryAction"] == ""

    task = store.get_task(task_id)
    artifact = next(a for a in task["artifacts"] if a["id"] == "a_partial_scope")
    assert artifact["scopeExpansionConfirmedAt"]
    assert artifact["scopeExpansionConfirmedBy"] == "openair_system"
    assert artifact["scopeExpansionReviewSource"] == "user"
    assert artifact["trustRecords"][-1]["usedFor"] == "scope_expansion_gate"
    assert artifact["trustRecords"][-1]["trustContribution"] == "needs_confirmation"


@pytest.mark.parametrize(
    ("state", "actions", "primary"),
    [
        ("usable", ["use_downstream"], "use_downstream"),
        ("needs_review", ["confirm_pass", "request_revision"], "confirm_pass"),
        ("partial_success", ["confirm_scope_expansion"], "confirm_scope_expansion"),
        ("needs_input", [], ""),
        ("insufficient_materials", [], ""),
        ("failed", [], ""),
        ("rejected_or_needs_revision", [], ""),
    ],
)
def test_artifact_human_gate_action_authority_matrix(
    state: str, actions: list[str], primary: str
) -> None:
    gate = human_gate_for_state(state, missing_inputs=["source"] if state == "needs_input" else [])
    assert gate["allowedActions"] == actions
    assert gate["primaryAction"] == primary
    assert not primary or primary in actions
    assert "view_trace" not in actions
    assert not {
        "supplement_input",
        "continue_research",
        "resume",
        "explicit_retry",
        "reconcile_external_result",
        "retry",
        "return",
    }.intersection(actions)


def test_confirm_scope_expansion_rejects_non_scope_expansion_artifact(client: TestClient) -> None:
    task_id = _create_task(OWNER_A, WS_A, "review task")
    store.add_artifact(
        task_id,
        kind="research_note",
        title="Normal pending review",
        artifact_id="a_normal_review",
        storage_type="inline",
        inline_content="# review",
        extra_fields={"artifactState": "needs_review"},
    )

    r = client.post(
        "/api/artifacts/a_normal_review/confirm-scope-expansion",
        json={},
        headers=_headers(OWNER_A, WS_A),
    )

    assert r.status_code == 409
    assert "does not require scope expansion confirmation" in r.json()["detail"]
    artifact = next(a for a in store.get_task(task_id)["artifacts"] if a["id"] == "a_normal_review")
    assert artifact["artifactState"] == "needs_review"


def test_needs_input_gate_cannot_authorize_task_recovery_actions(client: TestClient) -> None:
    task_id = _create_task(OWNER_A, WS_A, "needs input task")
    store.add_artifact(
        task_id,
        kind="research_note",
        title="Needs source records",
        artifact_id="a_needs_input",
        storage_type="inline",
        inline_content="# blocked",
        extra_fields={"artifactState": "needs_input", "missingInputs": ["source_records"]},
    )

    r = client.get("/api/artifacts/a_needs_input", headers=_headers(OWNER_A, WS_A))

    assert r.status_code == 200
    gate = r.json()["humanGate"]
    assert gate["canConfirmPass"] is False
    assert gate["primaryAction"] == ""
    assert gate["allowedActions"] == []
    assert not {
        "supplement_input",
        "supplement_materials",
        "allow_retrieval",
        "continue_research",
        "resume",
        "explicit_retry",
        "retry",
        "return",
        "reconcile_external_result",
        "view_trace",
    }.intersection(gate["allowedActions"])
    assert r.json()["validatorResult"]["missingInputs"] == ["source_records"]


def test_validator_result_preserves_existing_evaluator_details(client: TestClient) -> None:
    task_id = _create_task(OWNER_A, WS_A, "critic task")
    store.add_artifact(
        task_id,
        kind="research_note",
        title="Critic blocked artifact",
        artifact_id="a_critic",
        storage_type="inline",
        inline_content="# blocked",
        extra_fields={
            "validatorResult": {
                "contractVersion": "artifact-evaluator.v1",
                "evaluator": "critic_agent",
                "artifactState": "insufficient_materials",
                "usable": False,
                "missingInputs": ["doi_or_source_link"],
                "issues": [
                    {
                        "code": "source_not_traceable",
                        "severity": "blocker",
                        "message": "The artifact lacks a traceable source.",
                    }
                ],
                "nextAction": "supplement_materials",
            },
        },
    )

    r = client.get("/api/artifacts/a_critic", headers=_headers(OWNER_A, WS_A))

    assert r.status_code == 200
    body = r.json()
    assert body["artifactState"] == "insufficient_materials"
    assert body["validatorResult"]["evaluator"] == "critic_agent"
    assert body["validatorResult"]["issues"][0]["code"] == "source_not_traceable"
    assert body["validatorResult"]["missingInputs"] == ["doi_or_source_link"]
    assert body["humanGate"]["canConfirmPass"] is False


def test_validator_result_is_available_on_artifact_detail_and_call_trace(client: TestClient) -> None:
    task_id = _create_task(OWNER_A, WS_A, "trace task")
    call = store.add_capability_call(
        task_id,
        skill_id="source_check",
        status="needs_input",
        runtime="test",
    )
    assert call is not None
    store.add_artifact(
        task_id,
        kind="research_note",
        title="Traceable blocked artifact",
        artifact_id="a_trace",
        source_capability_call_id=call["id"],
        storage_type="inline",
        inline_content="# blocked",
    )

    detail = client.get("/api/artifacts/a_trace", headers=_headers(OWNER_A, WS_A)).json()
    assert detail["validatorResult"]["artifactState"] == "needs_review"
    assert detail["humanGate"]["allowedActions"] == ["confirm_pass", "request_revision"]

    task = store.get_task(task_id)
    traced_call = next(c for c in task["capabilityCalls"] if c["id"] == call["id"])
    assert traced_call["artifactValidatorResults"][0]["artifactId"] == "a_trace"
    assert traced_call["artifactValidatorResults"][0]["artifactState"] == "needs_review"


def test_blocked_capability_call_regates_existing_artifact(client: TestClient) -> None:
    task_id = _create_task(OWNER_A, WS_A, "blocked call task")
    call = store.add_capability_call(
        task_id,
        skill_id="intent-understanding",
        status="running",
        runtime="test",
    )
    assert call is not None
    artifact = store.add_artifact(
        task_id,
        kind="stage_summary",
        title="Intent output",
        artifact_id="a_blocked_call_output",
        source_capability_call_id=call["id"],
        storage_type="inline",
        inline_content="# draft while running",
        extra_fields={"artifactState": "needs_review"},
    )
    assert artifact is not None
    assert artifact["humanGate"]["canConfirmPass"] is True

    patched = store.patch_capability_call(
        task_id,
        call["id"],
        status="blocked_recoverable",
        result_summary="No usable business artifact was formed; user materials or public retrieval permission are required.",
        ended_at="2026-07-15T00:00:00Z",
    )

    assert patched is not None
    task = store.get_task(task_id)
    persisted = next(a for a in task["artifacts"] if a["id"] == "a_blocked_call_output")
    assert persisted["artifactState"] == "needs_review"
    assert persisted["validatorResult"]["artifactState"] == "needs_review"
    assert persisted["validatorResult"]["missingInputs"] == []
    assert persisted["humanGate"]["canConfirmPass"] is True
    assert persisted["humanGate"]["canUseDownstream"] is False
    assert persisted["humanGate"]["allowedActions"] == ["confirm_pass", "request_revision"]
    assert persisted["humanGate"]["primaryAction"] == "confirm_pass"

    detail = client.get("/api/artifacts/a_blocked_call_output", headers=_headers(OWNER_A, WS_A)).json()
    assert detail["artifactState"] == "needs_review"
    assert detail["humanGate"]["canConfirmPass"] is True

    traced_call = next(c for c in task["capabilityCalls"] if c["id"] == call["id"])
    assert traced_call["artifactValidatorResults"][0]["artifactId"] == "a_blocked_call_output"
    assert traced_call["artifactValidatorResults"][0]["artifactState"] == "needs_review"
