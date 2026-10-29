"""Artifact-03 关系/来源链测试。

json 后端默认不启用关系仓库（record 为 no-op）。这里塞一个内存假仓库，覆盖 reuse→reuses、
derived_from 钩子、relations 路由 scope/403；以及 ``record_relations_for_call`` 的
inputCallRefs 展开（R1）纯单元测试。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.artifact_provenance import record_relations_for_call
from app.modules.task_store import store

OWNER_A = "ownerA"
WS_A = "wsA"
OWNER_B = "ownerB"
WS_B = "wsB"


class InMemoryRelationRepo:
    """镜像 ArtifactRelationRepository 接口的内存实现。"""

    def __init__(self) -> None:
        self._rows: list[dict[str, object]] = []

    def upsert_relation(self, *, source_artifact_id, target_artifact_id, relation_type, owner_id, workspace_id, capability_call_id=None, metadata=None, created_at=None):  # type: ignore[no-untyped-def]
        cap = capability_call_id or ""
        for r in self._rows:
            if (r["s"], r["t"], r["type"], r["cap"]) == (source_artifact_id, target_artifact_id, relation_type, cap):  # type: ignore[index]
                r["meta"] = metadata or {}  # type: ignore[index]
                return
        self._rows.append({"s": source_artifact_id, "t": target_artifact_id, "type": relation_type, "owner": owner_id, "ws": workspace_id, "cap": cap, "meta": metadata or {}, "created": created_at})

    def list_for(self, artifact_id, *, owner_id, workspace_id, direction="both", relation_type=None):  # type: ignore[no-untyped-def]
        out: list[dict[str, object]] = []
        for r in self._rows:
            if r["owner"] != owner_id or r["ws"] != workspace_id:  # type: ignore[index]
                continue
            if direction == "outgoing" and r["s"] != artifact_id:  # type: ignore[index]
                continue
            if direction == "incoming" and r["t"] != artifact_id:  # type: ignore[index]
                continue
            if direction == "both" and r["s"] != artifact_id and r["t"] != artifact_id:  # type: ignore[index]
                continue
            if relation_type and r["type"] != relation_type:  # type: ignore[index]
                continue
            out.append({"sourceArtifactId": r["s"], "targetArtifactId": r["t"], "relationType": r["type"], "capabilityCallId": r["cap"] or None, "metadata": r["meta"], "createdAt": r["created"]})  # type: ignore[index]
        return out


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolated_store():
    original = store._tasks.copy()
    original_rel = store._relation_repository
    store._tasks.clear()
    store.enable_relation_repository(InMemoryRelationRepo())
    try:
        yield
    finally:
        store._tasks.clear()
        store._tasks.update(original)
        store.enable_relation_repository(original_rel)


def _headers(owner: str, ws: str) -> dict[str, str]:
    return {"X-OpenAIR-Owner-Id": owner, "X-OpenAIR-Workspace-Id": ws}


def _create_task(owner: str, ws: str, title: str = "t") -> str:
    t = store.create_task(
        {
            "title": title,
            "goalSummary": "g",
            "domain": "research_task",
            "currentStage": "problem_modeling",
            "status": "running",
            "ownerId": owner,
            "workspaceId": ws,
        }
    )
    return str(t["taskId"])


# ---------------------------------------------------------------------------
# pure unit: derived_from inputCallRefs 展开（R1）
# ---------------------------------------------------------------------------


def test_record_relations_expands_inputCallRefs() -> None:
    class Spy:
        def __init__(self) -> None:
            self.recorded: list[dict[str, object]] = []

        def record_artifact_relation(self, **kw: object) -> None:  # type: ignore[no-untyped-def]
            self.recorded.append(kw)

    spy = Spy()
    task = {
        "ownerId": "o",
        "workspaceId": "w",
        "capabilityCalls": [
            {"id": "c1", "outputArtifactRefs": ["a1"]},
            {"id": "c2", "inputCallRefs": ["c1"], "outputArtifactRefs": ["a2"]},
        ],
    }
    call = task["capabilityCalls"][1]
    record_relations_for_call(task, call, spy)
    # c2 输出 a2；输入 = 展开 c1 的输出 = a1 → derived_from(a2, a1)
    pairs = {(r["source_artifact_id"], r["target_artifact_id"], r["relation_type"]) for r in spy.recorded}
    assert ("a2", "a1", "derived_from") in pairs


# ---------------------------------------------------------------------------
# route: reuse → reuses；derived_from 钩子；scope；403
# ---------------------------------------------------------------------------


def test_reuse_creates_reuses_relation(client: TestClient) -> None:
    src = _create_task(OWNER_A, WS_A, "src")
    store.add_artifact(src, kind="report", title="orig", artifact_id="a_src", extra_fields={"artifactState": "usable"})
    tgt = _create_task(OWNER_A, WS_A, "tgt")

    r = client.post("/api/artifacts/a_src/reuse", json={"targetTaskId": tgt}, headers=_headers(OWNER_A, WS_A))
    assert r.status_code == 200
    new_id = r.json()["artifact"]["id"]

    # 新 imported 产物：outgoing reuses → 来源
    rel_new = client.get(f"/api/artifacts/{new_id}/relations", headers=_headers(OWNER_A, WS_A)).json()["relations"]
    assert any(rr["relationType"] == "reuses" and rr["direction"] == "outgoing" and rr["artifactId"] == "a_src" for rr in rel_new)
    # 来源产物：incoming reuses ← 新产物
    rel_src = client.get("/api/artifacts/a_src/relations", headers=_headers(OWNER_A, WS_A)).json()["relations"]
    assert any(rr["relationType"] == "reuses" and rr["direction"] == "incoming" and rr["artifactId"] == new_id for rr in rel_src)


def test_derived_from_via_capability_call_hook(client: TestClient) -> None:
    t = _create_task(OWNER_A, WS_A)
    store.add_artifact(t, kind="dataset", title="input", artifact_id="a_in")
    call = store.add_capability_call(t, skill_id="sk", status="completed", runtime="claude_code")
    assert call
    # 给该 call 设输入（模拟 inputArtifactRefs 已填）
    for c in store._tasks[t]["capabilityCalls"]:
        if c["id"] == call["id"]:
            c["inputArtifactRefs"] = ["a_in"]
    # 注册输出产物 → add_artifact 触发 derived_from 钩子
    store.add_artifact(t, kind="report", title="output", artifact_id="a_out", source_capability_call_id=call["id"])

    rel = client.get("/api/artifacts/a_out/relations", headers=_headers(OWNER_A, WS_A)).json()["relations"]
    assert any(rr["relationType"] == "derived_from" and rr["direction"] == "outgoing" and rr["artifactId"] == "a_in" for rr in rel)


def test_relations_scoped(client: TestClient) -> None:
    ta = _create_task(OWNER_A, WS_A)
    store.add_artifact(ta, kind="report", title="x", artifact_id="x")
    store.record_artifact_relation(
        source_artifact_id="x",
        target_artifact_id="y",
        relation_type="derived_from",
        owner_id=OWNER_A,
        workspace_id=WS_A,
        capability_call_id="c1",
    )
    rel_a = client.get("/api/artifacts/x/relations", headers=_headers(OWNER_A, WS_A)).json()["relations"]
    assert len(rel_a) == 1
    # B 查 A 的产物 → scope check 403
    assert client.get("/api/artifacts/x/relations", headers=_headers(OWNER_B, WS_B)).status_code == 403


def test_relations_403_out_of_scope(client: TestClient) -> None:
    tb = _create_task(OWNER_B, WS_B)
    store.add_artifact(tb, kind="report", title="b", artifact_id="b1")
    r = client.get("/api/artifacts/b1/relations", headers=_headers(OWNER_A, WS_A))
    assert r.status_code == 403
