"""产物版本管理测试（issue #302）。

覆盖：懒加载 v1 兼容、编辑新版本、退回/重生成、恢复历史版本、agent 重生成折叠、
scope 隔离（403）与不存在（404）、research_start_draft 可编辑但不可退回。
后端为 json（无 postgres），直接打在内存 store + TestClient 上。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.task_store import store

OWNER = "ownerA"
WS = "wsA"
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


def _headers(owner: str = OWNER, ws: str = WS) -> dict[str, str]:
    return {"X-OpenAIR-Owner-Id": owner, "X-OpenAIR-Workspace-Id": ws}


def _create_task(owner: str = OWNER, ws: str = WS, title: str = "task") -> str:
    task = store.create_task(
        {
            "title": title,
            "goalSummary": "versioning test",
            "domain": "research_task",
            "currentStage": "problem_modeling",
            "status": "running",
            "ownerId": owner,
            "workspaceId": ws,
        }
    )
    return str(task["taskId"])


def _add_markdown_artifact(
    task_id: str,
    *,
    content: str = "# initial\n",
    artifact_id: str = "art_v",
    kind: str = "stage_summary",
    fmt: str = "markdown",
) -> dict:
    return store.add_artifact(
        task_id,
        kind=kind,
        title="artifact",
        format=fmt,
        storage_type="inline",
        inline_content=content,
        artifact_id=artifact_id,
        related_stage="problem_modeling",
        substage_instance_id="ssi_test",
    )


# ---------------------------------------------------------------------------
# lazy v1 backfill
# ---------------------------------------------------------------------------


def test_legacy_artifact_backfilled_to_single_initial_version(client: TestClient) -> None:
    task_id = _create_task()
    _add_markdown_artifact(task_id, artifact_id="art_legacy")

    r = client.get("/api/artifacts/art_legacy/versions", headers=_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["artifactId"] == "art_legacy"
    versions = body["versions"]
    assert len(versions) == 1
    assert versions[0]["source"] == "initial"
    assert versions[0]["versionNumber"] == 1
    assert body["currentVersionId"] == versions[0]["versionId"]
    # summaries must not carry inlineContent
    assert "inlineContent" not in versions[0]


def test_versioned_view_is_deterministic_across_reads(client: TestClient) -> None:
    task_id = _create_task()
    _add_markdown_artifact(task_id, artifact_id="art_det")
    r1 = client.get("/api/artifacts/art_det/versions", headers=_headers()).json()
    r2 = client.get("/api/artifacts/art_det/versions", headers=_headers()).json()
    assert r1["versions"][0]["versionId"] == r2["versions"][0]["versionId"]


# ---------------------------------------------------------------------------
# edit → user_edit version
# ---------------------------------------------------------------------------


def test_edit_creates_new_version_and_preserves_original(client: TestClient) -> None:
    task_id = _create_task()
    _add_markdown_artifact(task_id, content="# v1\n", artifact_id="art_edit")

    r = client.patch(
        "/api/artifacts/art_edit/content",
        headers=_headers(),
        json={"content": "# v2 edited\n", "changeReason": "fix typo"},
    )
    assert r.status_code == 200, r.text
    detail = r.json()
    # metadata response never carries body bytes; canonical content mirrors v2.
    assert "inlineContent" not in detail
    current_content = client.get("/api/artifacts/art_edit/content", headers=_headers())
    assert current_content.status_code == 200
    assert current_content.json()["content"] == "# v2 edited\n"
    assert detail["storageType"] == "inline"
    assert detail["contentSize"] == len("# v2 edited\n".encode("utf-8"))
    assert detail["contentSha256"]
    assert detail["currentVersionId"]

    versions = client.get("/api/artifacts/art_edit/versions", headers=_headers()).json()["versions"]
    assert [v["versionNumber"] for v in versions] == [1, 2]
    assert versions[1]["source"] == "user_edit"
    assert versions[1]["feedback"] == "fix typo"
    # original v1 preserved and still viewable
    v1_id = versions[0]["versionId"]
    historical = client.get(f"/api/artifacts/art_edit/versions/{v1_id}", headers=_headers()).json()
    assert "inlineContent" not in historical
    historical_content = client.get(
        f"/api/artifacts/art_edit/versions/{v1_id}/content", headers=_headers()
    )
    assert historical_content.status_code == 200
    assert historical_content.json()["content"] == "# v1\n"
    assert historical["source"] == "initial"


def test_edit_rejects_empty_content(client: TestClient) -> None:
    task_id = _create_task()
    _add_markdown_artifact(task_id, artifact_id="art_empty")
    r = client.patch("/api/artifacts/art_empty/content", headers=_headers(), json={"content": "   "})
    assert r.status_code == 409


def test_edit_non_text_artifact_rejected(client: TestClient) -> None:
    task_id = _create_task()
    store.add_artifact(
        task_id,
        kind="dataset",
        title="csv",
        format="csv",
        storage_type="local",
        path="workspace/x/data.csv",
        artifact_id="art_csv",
    )
    r = client.patch("/api/artifacts/art_csv/content", headers=_headers(), json={"content": "a,b"})
    assert r.status_code == 409


def test_edit_does_not_change_stage_or_substage(client: TestClient) -> None:
    task_id = _create_task()
    _add_markdown_artifact(task_id, artifact_id="art_stage")
    before = store.get_artifact(task_id, "art_stage")
    client.patch(
        "/api/artifacts/art_stage/content",
        headers=_headers(),
        json={"content": "# edited\n"},
    )
    after = store.get_artifact(task_id, "art_stage")
    for key in ("relatedStage", "stage", "subStageInstanceId", "substage", "relatedSkillId"):
        assert after.get(key) == before.get(key)


def test_verified_artifact_resets_to_pending_after_edit(client: TestClient) -> None:
    task_id = _create_task()
    _add_markdown_artifact(task_id, kind="report", artifact_id="art_veri")
    # move to verified via review (report is reviewable)
    client.post("/api/artifacts/art_veri/review", headers=_headers(), json={"decision": "approved"})
    assert store.get_artifact(task_id, "art_veri")["artifactState"] == "usable"
    client.patch("/api/artifacts/art_veri/content", headers=_headers(), json={"content": "# new\n"})
    assert store.get_artifact(task_id, "art_veri")["artifactState"] == "needs_review"


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


def test_restore_creates_new_version_copying_old_content(client: TestClient) -> None:
    task_id = _create_task()
    _add_markdown_artifact(task_id, content="# v1\n", artifact_id="art_rest")
    client.patch("/api/artifacts/art_rest/content", headers=_headers(), json={"content": "# v2\n"})
    versions = client.get("/api/artifacts/art_rest/versions", headers=_headers()).json()["versions"]
    v1_id = versions[0]["versionId"]

    r = client.post(f"/api/artifacts/art_rest/versions/{v1_id}/restore", headers=_headers())
    assert r.status_code == 200, r.text
    assert "inlineContent" not in r.json()
    current_content = client.get("/api/artifacts/art_rest/content", headers=_headers())
    assert current_content.status_code == 200
    assert current_content.json()["content"] == "# v1\n"

    versions = client.get("/api/artifacts/art_rest/versions", headers=_headers()).json()["versions"]
    assert [v["source"] for v in versions] == ["initial", "user_edit", "restore"]
    restored = versions[-1]
    assert restored["restoredFromVersionId"] == v1_id
    assert restored["versionNumber"] == 3


def test_restore_revision_requested_event_rejected(client: TestClient) -> None:
    task_id = _create_task()
    _add_markdown_artifact(task_id, artifact_id="art_rev")
    # create a revision_requested event
    store.regenerate_artifact(task_id, "art_rev", feedback="bad", requested_by="u")
    versions = client.get("/api/artifacts/art_rev/versions", headers=_headers()).json()["versions"]
    rev = next(v for v in versions if v["source"] == "revision_requested")
    r = client.post(f"/api/artifacts/art_rev/versions/{rev['versionId']}/restore", headers=_headers())
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# regenerate / revision
# ---------------------------------------------------------------------------


def test_regenerate_endpoint_rejects_without_revision_or_pending(client: TestClient) -> None:
    task_id = _create_task()
    _add_markdown_artifact(task_id, artifact_id="art_regen")
    before = store.get_artifact(task_id, "art_regen")
    before_versions = [dict(version) for version in before["versions"]]

    r = client.post(
        "/api/artifacts/art_regen/regenerate",
        headers=_headers(),
        json={"feedback": "需要补充基线对比", "skillId": "baseline_comparison"},
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "artifact_regeneration_requires_durable_gate"
    after = store.get_artifact(task_id, "art_regen")
    assert after["versions"] == before_versions
    assert after.get("artifactState") == before.get("artifactState")
    assert after.get("inlineContent") == before.get("inlineContent")
    assert store.get_task(task_id).get("pendingRegenerate") in (None, {})


def test_regenerate_with_skill_id_fails_closed_without_mutation(client: TestClient, monkeypatch) -> None:
    """The legacy HTTP endpoint rejects before persistence or execution."""
    import app.execution.agent_service as agent_service

    invoked = {"count": 0}

    async def fake_execute_skill(_task_id: str, _skill_id: str) -> None:
        invoked["count"] += 1

    monkeypatch.setattr(agent_service, "execute_skill", fake_execute_skill)
    task_id = _create_task()
    _add_markdown_artifact(task_id, artifact_id="art_regen_skill")
    before = store.get_artifact(task_id, "art_regen_skill")
    before_versions = [dict(version) for version in before["versions"]]
    before_state = before.get("artifactState")
    before_content = before.get("inlineContent")
    before_pending = store.get_task(task_id).get("pendingRegenerate")

    response = client.post(
        "/api/artifacts/art_regen_skill/regenerate",
        headers=_headers(),
        json={"feedback": "redo", "skillId": "baseline_comparison"},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "artifact_regeneration_requires_durable_gate"
    after = store.get_artifact(task_id, "art_regen_skill")
    assert after["versions"] == before_versions
    assert after.get("artifactState") == before_state
    assert after.get("inlineContent") == before_content
    assert store.get_task(task_id).get("pendingRegenerate") == before_pending
    assert invoked["count"] == 0


def test_review_needs_revision_also_records_version_event(client: TestClient) -> None:
    task_id = _create_task()
    _add_markdown_artifact(task_id, kind="report", artifact_id="art_review")
    client.post(
        "/api/artifacts/art_review/review",
        headers=_headers(),
        json={"decision": "needs_revision", "note": "证据不足"},
    )
    versions = client.get("/api/artifacts/art_review/versions", headers=_headers()).json()["versions"]
    assert any(v["source"] == "revision_requested" and v["feedback"] == "证据不足" for v in versions)


def test_research_start_draft_editable_but_not_regenerable(client: TestClient) -> None:
    task_id = _create_task()
    store.add_artifact(
        task_id,
        kind="research_start_draft",
        title="研究起点草案",
        format="markdown",
        storage_type="inline",
        inline_content="# draft\n",
        artifact_id="art_draft",
        related_stage="problem_modeling",
    )
    # edit allowed
    r = client.patch("/api/artifacts/art_draft/content", headers=_headers(), json={"content": "# edited draft\n"})
    assert r.status_code == 200, r.text
    # regenerate (return) forbidden
    r2 = client.post("/api/artifacts/art_draft/regenerate", headers=_headers(), json={"feedback": "x"})
    assert r2.status_code == 409
    # review still forbidden
    r3 = client.post("/api/artifacts/art_draft/review", headers=_headers(), json={"decision": "approved"})
    assert r3.status_code == 409


# ---------------------------------------------------------------------------
# adopt (agent regenerate fold)
# ---------------------------------------------------------------------------


def test_adopt_artifact_as_version_folds_new_output(client: TestClient) -> None:
    task_id = _create_task()
    _add_markdown_artifact(task_id, content="# v1\n", artifact_id="art_adopt")
    # arm regeneration
    store.regenerate_artifact(task_id, "art_adopt", feedback="redo", requested_by="u")
    assert store.get_task(task_id).get("pendingRegenerate", {}).get("artifactId") == "art_adopt"

    # simulate the worker producing new content → fold
    updated = store.adopt_artifact_as_version(
        task_id,
        "art_adopt",
        feedback="redo",
        storage_type="inline",
        inline_content="# regenerated\n",
        content_sha256="abc",
        content_size=14,
        requested_by="agent",
    )
    assert updated is not None
    assert updated["inlineContent"] == "# regenerated\n"
    assert updated["artifactState"] == "needs_review"
    assert updated["revisionFeedback"] is None
    # pending marker consumed
    assert store.get_task(task_id).get("pendingRegenerate") in (None, {})

    versions = updated["versions"]
    assert versions[-1]["source"] == "agent_regenerate"
    assert versions[-1]["parentVersionId"] is not None
    assert updated["currentVersionId"] == versions[-1]["versionId"]


def test_fold_hook_folds_new_output_into_original_artifact(tmp_path) -> None:
    """The agent_service registration hook (_maybe_fold_regenerated_output) folds a
    freshly-produced workspace output into the armed original artifact."""
    from app.execution.agent_service import _maybe_fold_regenerated_output

    task_id = _create_task()
    _add_markdown_artifact(task_id, content="# v1\n", artifact_id="art_hook")
    # arm regeneration (sets revision_requested + pendingRegenerate)
    store.regenerate_artifact(task_id, "art_hook", feedback="redo", requested_by="u")
    assert store.get_task(task_id).get("pendingRegenerate", {}).get("artifactId") == "art_hook"

    # simulate the worker writing a new output file
    out = tmp_path / "out.md"
    out.write_text("# regenerated content\n", encoding="utf-8")

    folded = _maybe_fold_regenerated_output(
        task_id, "ssi_test", out, f"workspace/{task_id}/out.md", "# regenerated content\n", True
    )
    assert folded == ["art_hook"]

    artifact = store.get_artifact(task_id, "art_hook")
    assert artifact["inlineContent"] == "# regenerated content\n"
    assert artifact["artifactState"] == "needs_review"
    assert artifact["versions"][-1]["source"] == "agent_regenerate"
    # pending marker consumed by adopt
    assert store.get_task(task_id).get("pendingRegenerate") in (None, {})


def test_fold_hook_noop_without_pending(tmp_path) -> None:
    """No pending regeneration → hook returns None and registers nothing."""
    from app.execution.agent_service import _maybe_fold_regenerated_output

    task_id = _create_task()
    _add_markdown_artifact(task_id, artifact_id="art_nofold")
    out = tmp_path / "out.md"
    out.write_text("# x\n", encoding="utf-8")
    assert _maybe_fold_regenerated_output(task_id, None, out, "p", "# x\n", True) is None


def test_fold_rejects_wrong_or_empty_substage(tmp_path) -> None:
    """Blocking r3: a regeneration is bound to ONE substage instance. An unrelated
    output (wrong substage, or no substage context) must NOT fold into the armed
    artifact — the loose-match hole is closed."""
    from app.execution.agent_service import _maybe_fold_regenerated_output

    task_id = _create_task()
    _add_markdown_artifact(task_id, content="# v1\n", artifact_id="art_r3")
    store.regenerate_artifact(task_id, "art_r3", feedback="redo", requested_by="u")
    # armed artifact's subStageInstanceId == "ssi_test"

    out = tmp_path / "other.md"
    out.write_text("# unrelated\n", encoding="utf-8")
    # wrong substage → no fold
    assert (
        _maybe_fold_regenerated_output(
            task_id, "ssi_DIFFERENT", out, "workspace/x/other.md", "# unrelated\n", True
        )
        is None
    )
    # empty substage → no fold (the previous loose-match hole)
    assert (
        _maybe_fold_regenerated_output(task_id, None, out, "workspace/x/other.md", "# unrelated\n", True)
        is None
    )
    # pending still armed, no agent_regenerate version produced
    assert store.get_task(task_id)["pendingRegenerate"]["artifactId"] == "art_r3"
    art = store.get_artifact(task_id, "art_r3")
    assert all(v["source"] != "agent_regenerate" for v in art["versions"])


def test_patch_storage_keeps_initial_version_consistent_with_top_level(client: TestClient) -> None:
    """Regression (issue #302 review): the registration flow creates an artifact
    shell (path only) then patches the real storage/inline content via
    _patch_artifact_storage. The initial version MUST mirror the patched content —
    otherwise viewing/restoring v1 returns stale/empty content."""
    from app.execution.agent_service import _patch_artifact_storage

    task_id = _create_task()
    # step 1: add_artifact creates the shell (no inline content yet)
    store.add_artifact(
        task_id,
        kind="stage_summary",
        title="substage output",
        format="markdown",
        path=f"workspace/{task_id}/out.md",
        artifact_id="art_patch",
    )
    # step 2: storage decision patches top-level content (real agent_service path)
    _patch_artifact_storage(
        task_id,
        "art_patch",
        storage_type="inline",
        inline_content="# real generated content\n",
    )

    versions = client.get("/api/artifacts/art_patch/versions", headers=_headers()).json()["versions"]
    assert len(versions) == 1
    v1 = client.get(
        f"/api/artifacts/art_patch/versions/{versions[0]['versionId']}", headers=_headers()
    ).json()
    # core contract: metadata is body-free and both canonical content routes agree.
    assert "inlineContent" not in v1
    assert v1["storageType"] == "inline"
    v1_content = client.get(
        f"/api/artifacts/art_patch/versions/{v1['versionId']}/content", headers=_headers()
    ).json()["content"]

    detail = client.get("/api/artifacts/art_patch", headers=_headers()).json()
    assert "inlineContent" not in detail
    current_content = client.get("/api/artifacts/art_patch/content", headers=_headers()).json()["content"]
    assert current_content == v1_content == "# real generated content\n"
    assert detail["currentVersionId"] == v1["versionId"]


def test_patch_storage_then_edit_preserves_original(client: TestClient) -> None:
    """After the two-step registration produces a correct v1, editing creates v2 and
    v1 still holds the original generated content (round-trip through restore)."""
    from app.execution.agent_service import _patch_artifact_storage

    task_id = _create_task()
    store.add_artifact(
        task_id,
        kind="stage_summary",
        title="out",
        format="markdown",
        path=f"workspace/{task_id}/out.md",
        artifact_id="art_patch2",
    )
    _patch_artifact_storage(task_id, "art_patch2", storage_type="inline", inline_content="# original\n")
    # user edits
    client.patch(
        "/api/artifacts/art_patch2/content", headers=_headers(), json={"content": "# edited\n"}
    )
    versions = client.get("/api/artifacts/art_patch2/versions", headers=_headers()).json()["versions"]
    assert [v["source"] for v in versions] == ["initial", "user_edit"]
    v1 = client.get(
        f"/api/artifacts/art_patch2/versions/{versions[0]['versionId']}", headers=_headers()
    ).json()
    assert "inlineContent" not in v1
    v1_content = client.get(
        f"/api/artifacts/art_patch2/versions/{versions[0]['versionId']}/content",
        headers=_headers(),
    )
    assert v1_content.status_code == 200
    assert v1_content.json()["content"] == "# original\n"



# ---------------------------------------------------------------------------
# scope isolation
# ---------------------------------------------------------------------------


def test_versions_scope_isolation(client: TestClient) -> None:
    _create_task(OWNER, WS)
    _add_markdown_artifact(store.list_tasks()[0]["taskId"], artifact_id="art_scope")
    # cross-owner → 403
    r = client.get("/api/artifacts/art_scope/versions", headers=_headers(OWNER_B, WS_B))
    assert r.status_code == 403
    # unknown → 404
    r2 = client.get("/api/artifacts/art_unknown/versions", headers=_headers())
    assert r2.status_code == 404


def test_edit_scope_isolation(client: TestClient) -> None:
    _create_task(OWNER, WS)
    _add_markdown_artifact(store.list_tasks()[0]["taskId"], artifact_id="art_edit_scope")
    r = client.patch(
        "/api/artifacts/art_edit_scope/content",
        headers=_headers(OWNER_B, WS_B),
        json={"content": "# hack\n"},
    )
    assert r.status_code == 403


def test_version_detail_not_found(client: TestClient) -> None:
    _create_task()
    _add_markdown_artifact(store.list_tasks()[0]["taskId"], artifact_id="art_vd")
    r = client.get("/api/artifacts/art_vd/versions/v_unknown", headers=_headers())
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# PR #306 review hardening (prod / edge paths)
# ---------------------------------------------------------------------------


def test_artifact_repository_round_trips_versioning_fields() -> None:
    """Hard#2: versioning fields must survive the artifact_repository metadata
    mirror, or the artifact library (served from the catalog) loses version status
    and pending-revision state in production (postgres backend)."""
    from app.modules.artifact_repository import _metadata_from_artifact, _row_to_dict

    artifact = {
        "id": "art_repo",
        "kind": "stage_summary",
        "title": "substage output",
        "storageType": "inline",
        "inlineContent": "# v2\n",
        "artifactState": "rejected_or_needs_revision",
        "currentVersionId": "v_abc",
        "versions": [{"versionId": "v_abc", "versionNumber": 2, "source": "user_edit"}],
        "revisionRequestedAt": "2026-07-14T00:00:00Z",
        "revisionFeedback": "redo",
        "resultProtocol": {
            "version": "result-protocol.v1",
            "status": "degraded",
            "accepted_items": [{"path": "queries", "count": 7}],
            "quarantined_items": [
                {"path": "queries[7]", "reason": "invalid_query"}
            ],
            "repaired_items": [],
            "warnings": ["one query was skipped"],
            "coverage": {"accepted_query_count": 7},
        },
        "artifactQuality": "degraded",
        "warnings": ["one query was skipped"],
    }
    metadata = _metadata_from_artifact(artifact)
    for key in ("currentVersionId", "versions", "revisionRequestedAt", "revisionFeedback"):
        assert key in metadata, f"{key} not persisted into repository metadata"

    # simulate a catalog row reconstructed from the persisted metadata
    row = {
        "artifact_id": "art_repo",
        "owner_id": "ownerA",
        "workspace_id": "wsA",
        "kind": "stage_summary",
        "title": "substage output",
        "storage_type": "inline",
        "storage_ref": "# v2\n",
        "metadata": metadata,
    }
    rebuilt = _row_to_dict(row)
    assert rebuilt["currentVersionId"] == "v_abc"
    assert rebuilt["versions"][0]["versionId"] == "v_abc"
    assert rebuilt["revisionRequestedAt"] == "2026-07-14T00:00:00Z"
    assert rebuilt["revisionFeedback"] == "redo"
    assert rebuilt["resultProtocol"]["status"] == "degraded"
    assert rebuilt["resultProtocol"]["quarantined_items"] == [
        {"path": "queries[7]", "reason": "invalid_query"}
    ]
    assert rebuilt["artifactQuality"] == "degraded"
    assert rebuilt["warnings"] == ["one query was skipped"]


def test_fallback_scan_folds_into_original_when_pending(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    """Hard#3: when the agent writes a non-configured filename, the workspace
    fallback scan must still fold into the original armed artifact (no duplicate),
    and consume the pending marker — not call add_artifact directly."""
    import app.execution.agent_service as agent_service
    from app.execution.agent_service import _register_workspace_outputs_fallback

    task_id = _create_task()
    _add_markdown_artifact(task_id, content="# v1\n", artifact_id="art_fb")
    store.regenerate_artifact(task_id, "art_fb", feedback="redo", requested_by="u")
    assert store.get_task(task_id)["pendingRegenerate"]["artifactId"] == "art_fb"

    # agent wrote a DIFFERENT filename than any configured output_file
    (tmp_path / "references.md").write_text("# regenerated\n", encoding="utf-8")
    monkeypatch.setattr(agent_service, "get_task_workspace_dir", lambda _tid: tmp_path)

    created = _register_workspace_outputs_fallback(
        task_id, None, {"subStageInstanceId": "ssi_test"}, "call_1"
    )
    assert created == ["art_fb"]  # folded into the original, not a new id

    artifact = store.get_artifact(task_id, "art_fb")
    assert artifact["inlineContent"] == "# regenerated\n"
    assert artifact["versions"][-1]["source"] == "agent_regenerate"
    # pending marker consumed, no duplicate artifact
    assert store.get_task(task_id).get("pendingRegenerate") in (None, {})
    assert len(store.get_task(task_id)["artifacts"]) == 1


def test_edit_ignores_client_supplied_editor(client: TestClient) -> None:
    """Minor#1: the version author is derived from the authenticated identity,
    never from the request body — a client-supplied `editor` must be ignored."""
    task_id = _create_task()
    _add_markdown_artifact(task_id, artifact_id="art_editor")
    r = client.patch(
        "/api/artifacts/art_editor/content",
        headers=_headers(),
        json={"content": "# edited\n", "editor": "attacker"},
    )
    assert r.status_code == 200, r.text
    versions = client.get("/api/artifacts/art_editor/versions", headers=_headers()).json()["versions"]
    assert versions[-1]["createdBy"] != "attacker"


def test_regenerate_endpoint_rejects_all_artifact_origins(client: TestClient) -> None:
    """The durable-gate requirement applies to every in-scope artifact origin."""
    task_id = _create_task()
    store.add_artifact(
        task_id,
        kind="user_upload",
        title="upload",
        format="markdown",
        storage_type="inline",
        inline_content="# x\n",
        artifact_id="art_up",
        extra_fields={"origin": "user_upload"},
    )
    r = client.post("/api/artifacts/art_up/regenerate", headers=_headers(), json={"feedback": "x"})
    assert r.status_code == 409
    assert r.json()["detail"] == "artifact_regeneration_requires_durable_gate"

    _add_markdown_artifact(task_id, artifact_id="art_ss")
    r2 = client.post("/api/artifacts/art_ss/regenerate", headers=_headers(), json={"feedback": "x"})
    assert r2.status_code == 409
    assert r2.json()["detail"] == "artifact_regeneration_requires_durable_gate"
    assert store.get_task(task_id).get("pendingRegenerate") in (None, {})


# ---------------------------------------------------------------------------
# PR #306 review round 2 (state flow + fold boundary)
# ---------------------------------------------------------------------------


def test_edit_after_revision_resets_to_pending_review(client: TestClient) -> None:
    """Blocking r2: after a return-for-modification, a user edit produces a new
    version AND moves the artifact out of rejected_or_needs_revision back to
    pending_review, clearing the stale revisionRequestedAt / revisionFeedback —
    otherwise the UI keeps showing 'needs revision'."""
    task_id = _create_task()
    _add_markdown_artifact(task_id, artifact_id="art_edit_rev")
    store.regenerate_artifact(task_id, "art_edit_rev", feedback="redo", requested_by="u")
    detail = client.get("/api/artifacts/art_edit_rev", headers=_headers()).json()
    assert detail["artifactState"] == "rejected_or_needs_revision"
    assert detail["revisionFeedback"] == "redo"

    r = client.patch(
        "/api/artifacts/art_edit_rev/content", headers=_headers(), json={"content": "# fixed\n"}
    )
    assert r.status_code == 200, r.text
    edited = r.json()
    assert edited["artifactState"] == "needs_review"
    assert edited["revisionRequestedAt"] is None
    assert edited["revisionFeedback"] is None


def test_restore_after_revision_resets_to_pending_review(client: TestClient) -> None:
    """Blocking r2: restoring a historical version also clears the pending-revision
    state (the restored content supersedes the requested revision)."""
    task_id = _create_task()
    _add_markdown_artifact(task_id, content="# v1\n", artifact_id="art_restore_rev")
    store.regenerate_artifact(task_id, "art_restore_rev", feedback="redo", requested_by="u")
    assert (
        client.get("/api/artifacts/art_restore_rev", headers=_headers()).json()["artifactState"]
        == "rejected_or_needs_revision"
    )

    v1_id = (
        client.get("/api/artifacts/art_restore_rev/versions", headers=_headers())
        .json()["versions"][0]["versionId"]
    )
    r = client.post(
        f"/api/artifacts/art_restore_rev/versions/{v1_id}/restore", headers=_headers()
    )
    assert r.status_code == 200, r.text
    assert r.json()["artifactState"] == "needs_review"
    assert r.json()["revisionFeedback"] is None


def test_regenerate_endpoint_without_substage_context_fails_closed(client: TestClient) -> None:
    """The legacy endpoint rejects without attempting substage resolution."""
    task_id = _create_task()
    store.add_artifact(
        task_id,
        kind="stage_summary",
        title="no substage context",
        format="markdown",
        storage_type="inline",
        inline_content="# x\n",
        artifact_id="art_noctx",
        related_stage="problem_modeling",
        # deliberately NO substage_instance_id
    )
    r = client.post("/api/artifacts/art_noctx/regenerate", headers=_headers(), json={"feedback": "x"})
    assert r.status_code == 409
    assert r.json()["detail"] == "artifact_regeneration_requires_durable_gate"
    assert store.get_task(task_id).get("pendingRegenerate") in (None, {})


# ---------------------------------------------------------------------------
# Legacy regenerate endpoint durable-gate boundary
# ---------------------------------------------------------------------------


def test_regenerate_endpoint_never_calls_executor(client: TestClient, monkeypatch) -> None:
    """Failing closed neither executes nor folds a generated version."""
    import app.execution.agent_service as agent_service

    invoked = {"count": 0}

    async def fake_execute_skill(_task_id: str, _skill_id: str) -> None:
        invoked["count"] += 1

    monkeypatch.setattr(agent_service, "execute_skill", fake_execute_skill)
    task_id = _create_task()
    store.add_artifact(
        task_id,
        kind="stage_summary",
        title="lit review",
        format="markdown",
        storage_type="inline",
        inline_content="# v1 initial review\n",
        artifact_id="art_flow",
        related_stage="problem_modeling",
        related_skill_id="literature_search",
        substage_instance_id="ssi_flow",
    )
    before = store.get_artifact(task_id, "art_flow")
    before_versions = [dict(version) for version in before["versions"]]

    r = client.post(
        "/api/artifacts/art_flow/regenerate",
        headers=_headers(),
        json={"feedback": "redo", "skillId": "literature_search"},
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "artifact_regeneration_requires_durable_gate"
    after = store.get_artifact(task_id, "art_flow")
    assert after["versions"] == before_versions
    assert after.get("artifactState") == before.get("artifactState")
    assert after.get("inlineContent") == before.get("inlineContent")
    assert store.get_task(task_id).get("pendingRegenerate") in (None, {})
    assert invoked["count"] == 0
    assert all(version["source"] != "agent_regenerate" for version in after["versions"])
    assert len(store.get_task(task_id)["artifacts"]) == 1


def test_regenerate_without_skill_id_also_fails_closed(client: TestClient, monkeypatch) -> None:
    """Omitting skillId does not bypass the durable-gate requirement."""
    import app.execution.agent_service as agent_service

    invoked = {"count": 0}

    async def fake_execute_skill(_task_id, _skill_id):
        invoked["count"] += 1
        return {"callId": "c", "status": "completed"}

    monkeypatch.setattr(agent_service, "execute_skill", fake_execute_skill)

    task_id = _create_task()
    # stage_summary with no relatedSkillId / substage and no body.skillId
    store.add_artifact(
        task_id,
        kind="stage_summary",
        title="no skill",
        format="markdown",
        storage_type="inline",
        inline_content="# v1\n",
        artifact_id="art_noskill",
        related_stage="problem_modeling",
        substage_instance_id="ssi_noskill",
    )
    before_versions = [
        dict(version) for version in store.get_artifact(task_id, "art_noskill")["versions"]
    ]
    r = client.post(
        "/api/artifacts/art_noskill/regenerate", headers=_headers(), json={"feedback": "redo"}
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "artifact_regeneration_requires_durable_gate"
    assert store.get_artifact(task_id, "art_noskill")["versions"] == before_versions
    assert invoked["count"] == 0
    assert store.get_task(task_id).get("pendingRegenerate") in (None, {})


# ---------------------------------------------------------------------------
# Admin review round 4 (immutable version content + per-version read)
# ---------------------------------------------------------------------------


def test_local_version_content_is_frozen_not_overwritten(monkeypatch, tmp_path) -> None:
    """Blocking r4 #1: a local-file version's content must be frozen into an
    immutable per-version file. When a later regenerate re-run writes the same
    shared output_file, the historical version still reads its ORIGINAL bytes
    (the old behavior stored one shared path on every version, so regenerating
    silently corrupted history)."""
    from app.execution import agent_service, execution_context

    monkeypatch.setattr(execution_context, "get_task_workspace_dir", lambda _tid: tmp_path)
    monkeypatch.setattr(agent_service, "get_task_workspace_dir", lambda _tid: tmp_path)

    task_id = _create_task()
    shared = tmp_path / "out.md"
    shared.write_text("# v1 original\n", encoding="utf-8")
    # registration: shell + storage patch(local) — mirrors the skill-output flow
    store.add_artifact(
        task_id,
        kind="stage_summary",
        title="out",
        format="markdown",
        path=f"workspace/{task_id}/out.md",
        artifact_id="art_local",
        related_stage="problem_modeling",
        substage_instance_id="ssi_l",
    )
    agent_service._patch_artifact_storage(task_id, "art_local", storage_type="local")

    initial = store.get_artifact(task_id, "art_local")
    initial_path = initial["versions"][0].get("path") or ""
    # the initial version was materialized into its own immutable file
    assert initial["versions"][0]["storageType"] == "local"
    assert "_versions/" in initial_path, initial_path

    # a later regenerate overwrites the shared output file with new bytes, then folds
    shared.write_text("# v2 regenerated\n", encoding="utf-8")
    store.regenerate_artifact(task_id, "art_local", feedback="redo", requested_by="u")
    folded = agent_service._maybe_fold_regenerated_output(
        task_id, "ssi_l", shared, f"workspace/{task_id}/out.md", None, False
    )
    assert folded == ["art_local"]

    art = store.get_artifact(task_id, "art_local")
    sources = [v["source"] for v in art["versions"]]
    # regenerate inserts a metadata-only revision_requested event between the two
    assert sources == ["initial", "revision_requested", "agent_regenerate"]
    # every CONTENT version (not the metadata-only event) has its own immutable file
    content_versions = [v for v in art["versions"] if v["source"] != "revision_requested"]
    assert all("_versions/" in (v.get("path") or "") for v in content_versions)
    assert len({v.get("path") for v in content_versions}) == 2  # two distinct files

    # the historical initial STILL reads the original bytes (not overwritten)
    v1 = next(v for v in art["versions"] if v["source"] == "initial")
    v1_file = tmp_path / v1["path"].split("/", 2)[2]
    assert v1_file.read_text(encoding="utf-8") == "# v1 original\n"
    # the current version reads the regenerated bytes
    v2 = next(v for v in art["versions"] if v["source"] == "agent_regenerate")
    v2_file = tmp_path / v2["path"].split("/", 2)[2]
    assert v2_file.read_text(encoding="utf-8") == "# v2 regenerated\n"


def test_version_content_endpoint_returns_local_version_bytes(
    client: TestClient, monkeypatch, tmp_path
) -> None:
    """Blocking r4 #2: GET /versions/{id}/content returns a local version's frozen
    bytes — large/local/external versions are no longer 'no inline content'."""
    from app.execution import agent_service, execution_context
    from app.modules import artifact_resolver

    monkeypatch.setattr(execution_context, "get_task_workspace_dir", lambda _tid: tmp_path)
    monkeypatch.setattr(agent_service, "get_task_workspace_dir", lambda _tid: tmp_path)
    monkeypatch.setattr(artifact_resolver, "get_task_workspace_dir", lambda _tid: tmp_path)

    task_id = _create_task()
    (tmp_path / "out.md").write_text("# v1 original\n", encoding="utf-8")
    store.add_artifact(
        task_id,
        kind="stage_summary",
        title="out",
        format="markdown",
        path=f"workspace/{task_id}/out.md",
        artifact_id="art_vc",
        related_stage="problem_modeling",
        substage_instance_id="ssi_vc",
    )
    agent_service._patch_artifact_storage(task_id, "art_vc", storage_type="local")

    versions = client.get("/api/artifacts/art_vc/versions", headers=_headers()).json()["versions"]
    r = client.get(
        f"/api/artifacts/art_vc/versions/{versions[0]['versionId']}/content", headers=_headers()
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content"] == "# v1 original\n"
    assert body["sourceMode"] == "local"


def test_version_content_endpoint_returns_inline_version(client: TestClient) -> None:
    """The per-version content endpoint also serves inline (user_edit) versions."""
    task_id = _create_task()
    _add_markdown_artifact(task_id, content="# v1\n", artifact_id="art_inl")
    client.patch(
        "/api/artifacts/art_inl/content", headers=_headers(), json={"content": "# edited\n"}
    )
    versions = client.get("/api/artifacts/art_inl/versions", headers=_headers()).json()["versions"]
    v2 = next(v for v in versions if v["source"] == "user_edit")
    r = client.get(
        f"/api/artifacts/art_inl/versions/{v2['versionId']}/content", headers=_headers()
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content"] == "# edited\n"
    assert body["sourceMode"] == "inline"


def test_version_content_endpoint_revision_requested_is_404(client: TestClient) -> None:
    """revision_requested is a metadata-only event with no content snapshot."""
    task_id = _create_task()
    _add_markdown_artifact(task_id, artifact_id="art_rev404")
    store.request_artifact_revision(task_id, "art_rev404", feedback="x", requested_by="u")
    versions = client.get("/api/artifacts/art_rev404/versions", headers=_headers()).json()["versions"]
    rev = next(v for v in versions if v["source"] == "revision_requested")
    r = client.get(
        f"/api/artifacts/art_rev404/versions/{rev['versionId']}/content", headers=_headers()
    )
    assert r.status_code == 404



# ---- B1: postgres edit/restore (ArtifactRepository; was 409) ----

def _full_artifact(artifact_id: str, **over) -> dict:
    base = {
        "id": artifact_id,
        "taskId": "t1",
        "sourceTaskId": "t1",
        "ownerId": OWNER,
        "workspaceId": WS,
        "createdBy": OWNER,
        "kind": "user_upload",
        "title": "T",
        "createdAt": "2026-08-11T00:00:00+00:00",
        "format": "md",
        "storageType": "inline",
        "artifactState": "needs_review",
    }
    base.update(over)
    return base


def test_edit_content_postgres_uses_artifact_repository(client, monkeypatch):
    """B1: postgres PATCH /content calls ArtifactRepository.edit_artifact_content (no 409)."""
    from app.routes import artifacts as art_route
    from app.config import settings

    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(
        art_route, "_read_artifact_context", lambda aid, ident: ({"taskId": "t1"}, {"id": aid})
    )
    captured: dict = {}

    class FakeRepo:
        def edit_artifact_content(self, **kw):
            captured.update(kw)
            return _full_artifact(kw["artifact_id"], currentVersionId="v_new")

    monkeypatch.setattr(art_route, "ArtifactRepository", FakeRepo)
    monkeypatch.setattr(art_route, "_artifact_detail", lambda task, art: art)

    r = client.patch("/api/artifacts/art_x/content", json={"content": "edited"}, headers=_headers())
    assert r.status_code == 200, r.text
    assert captured["content"] == "edited"
    assert captured["owner_id"] == OWNER


def test_restore_version_postgres_uses_artifact_repository(client, monkeypatch):
    """B1: postgres POST /restore calls ArtifactRepository.restore_artifact_version (no 409)."""
    from app.routes import artifacts as art_route
    from app.config import settings

    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(
        art_route, "_read_artifact_context", lambda aid, ident: ({"taskId": "t1"}, {"id": aid})
    )
    captured: dict = {}

    class FakeRepo:
        def restore_artifact_version(self, **kw):
            captured.update(kw)
            return _full_artifact(kw["artifact_id"], currentVersionId="v_restored")

    monkeypatch.setattr(art_route, "ArtifactRepository", FakeRepo)
    monkeypatch.setattr(art_route, "_artifact_detail", lambda task, art: art)

    r = client.post("/api/artifacts/art_x/versions/v1/restore", headers=_headers())
    assert r.status_code == 200, r.text
    assert captured["version_id"] == "v1"


def test_edit_content_postgres_propagates_value_error_as_409(client, monkeypatch):
    """B1: a non-editable artifact surfaces the repo ValueError as 409."""
    from app.routes import artifacts as art_route
    from app.config import settings

    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(
        art_route, "_read_artifact_context", lambda aid, ident: ({"taskId": "t1"}, {"id": aid})
    )

    class FakeRepo:
        def edit_artifact_content(self, **kw):
            raise ValueError("only markdown/text artifacts can be edited inline")

    monkeypatch.setattr(art_route, "ArtifactRepository", FakeRepo)
    r = client.patch("/api/artifacts/art_x/content", json={"content": "x"}, headers=_headers())
    assert r.status_code == 409


def test_append_version_carries_storage_scope_for_external():
    """B1 review C1: external restore must carry storageScope into the version dict,
    or the openair_sync_artifact_storage_references trigger raises
    artifact_external_storage_scope_required on the new historical version."""
    from app.modules.artifact_repository import _append_version_into_artifact

    artifact = {"id": "art_e", "versions": [], "currentVersionId": None}
    version = _append_version_into_artifact(
        artifact,
        source="restore",
        content={
            "storageType": "external",
            "externalKey": "bucket/key",
            "storageScope": "minio:prod",
            "contentSha256": "abc",
            "contentSize": 3,
        },
        feedback=None,
        parent_version_id=None,
        restored_from_version_id="v_old",
        created_by="user",
        created_at="2026-08-11T00:00:00+00:00",
    )
    assert version["storageScope"] == "minio:prod"
    assert artifact["storageScope"] == "minio:prod"
    assert version["externalKey"] == "bucket/key"
