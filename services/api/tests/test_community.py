"""Tests for POST/GET /api/community/publications (C1 submit + browse)."""

from __future__ import annotations

import pytest

from app.routes.community import CommunityPublicationCreateIn
from app.routes import community as community_route


def _pub_row(**over):
    base = {
        "publication_id": "pub_abc",
        "resource_type": "task",
        "resource_id": "task_src1",
        "publisher_owner_id": "owner1",
        "publisher_workspace_id": "ws1",
        "title": "T",
        "summary": "s",
        "published_manifest": {},
        "created_at": "2026-08-11T00:00:00+00:00",
    }
    base.update(over)
    return base


def _wire(monkeypatch, *, owner="owner1", pub_row=None, listed=None):
    identity = {"ownerId": owner, "workspaceId": "ws1", "createdBy": owner}

    class FakeTaskRepo:
        def get_task(self, tid):
            if tid == "task_src1":
                return {"taskId": tid, "ownerId": owner, "title": "Source Task"}
            if tid == "task_other":
                return {"taskId": tid, "ownerId": "someone_else", "title": "Not Mine"}
            return None

    monkeypatch.setattr(community_route, "TaskRepository", FakeTaskRepo)

    class FakeArtCatalog:
        def __init__(self):
            self.find = {"art_mine": ({"taskId": "task_src1"}, {"id": "art_mine", "ownerId": owner, "title": "My Artifact"})}

        def __call__(self, *a, **k):
            return self

        def find_artifact_by_id(self, aid):
            if aid in self.find:
                return self.find[aid]
            raise KeyError(aid)

    fake_art = FakeArtCatalog()
    # find_artifact_by_id is imported by name into community — patch the reference.
    monkeypatch.setattr(community_route, "find_artifact_by_id", fake_art.find_artifact_by_id)

    captured: dict = {}

    class FakeRepo:
        def publish(self, **kw):
            captured["publish"] = kw
            return pub_row or _pub_row(resource_type=kw["resource_type"], resource_id=kw["resource_id"], title=kw["title"])

        def list_publications(self, **kw):
            captured["list"] = kw
            rows = listed if listed is not None else [pub_row or _pub_row()]
            return rows, len(rows)

        def get_publication(self, pid):
            if pid == "pub_abc":
                return pub_row or _pub_row()
            return None

        def comment_counts(self, ids):
            return {i: 0 for i in ids}

    monkeypatch.setattr(community_route, "CommunityRepository", FakeRepo)
    return identity, captured


def test_publish_task_verifiesOwnership_and_records(monkeypatch):
    identity, captured = _wire(monkeypatch)
    out = community_route.publish_resource(
        CommunityPublicationCreateIn(resourceType="task", resourceId="task_src1", summary="x"),
        identity=identity,
    )
    assert out.resourceType == "task"
    assert out.resourceId == "task_src1"
    assert captured["publish"]["publisher_owner_id"] == "owner1"
    assert captured["publish"]["title"]  # defaulted from task title


def test_publish_rejects_non_owner(monkeypatch):
    identity, _ = _wire(monkeypatch)
    with pytest.raises(Exception) as exc:
        community_route.publish_resource(
            CommunityPublicationCreateIn(resourceType="task", resourceId="task_other"),
            identity=identity,
        )
    assert "403" in str(exc.value) or "owner" in str(exc.value).lower()


def test_publish_rejects_unknown_resource_type(monkeypatch):
    identity, _ = _wire(monkeypatch)
    with pytest.raises(Exception):
        community_route.publish_resource(
            CommunityPublicationCreateIn(resourceType="workflow", resourceId="task_src1"),
            identity=identity,
        )


def test_publish_artifact(monkeypatch):
    identity, captured = _wire(monkeypatch)
    out = community_route.publish_resource(
        CommunityPublicationCreateIn(resourceType="artifact", resourceId="art_mine"),
        identity=identity,
    )
    assert out.resourceType == "artifact"
    assert captured["publish"]["resource_type"] == "artifact"


def test_list_publications_cross_user(monkeypatch):
    # community browse is not owner-filtered
    identity, captured = _wire(monkeypatch, owner="user_OTHER", listed=[_pub_row(), _pub_row(publication_id="pub_def")])
    out = community_route.list_publications(
        resourceType=None, q=None, page=1, pageSize=20, identity=identity
    )
    assert out.total == 2
    assert len(out.publications) == 2


def test_get_publication_404_when_missing(monkeypatch):
    identity, _ = _wire(monkeypatch)
    with pytest.raises(Exception):
        community_route.get_publication("pub_missing", identity=identity)


# ---- fork ----

def _wire_fork(monkeypatch, *, pub_row, owner="user_OTHER"):
    identity = {"ownerId": owner, "workspaceId": "ws_OTHER", "createdBy": owner}

    class FakeRepo:
        def get_publication(self, pid):
            return pub_row if pid == pub_row["publication_id"] else None

    monkeypatch.setattr(community_route, "CommunityRepository", FakeRepo)

    cloned: dict = {}

    def fake_clone(source, ident, body):
        cloned["source_id"] = source["taskId"]
        cloned["ident"] = ident
        cloned["include"] = body.includeArtifacts
        return {"taskId": "task_forked"}

    monkeypatch.setattr(community_route, "_clone_source_into", fake_clone)

    reused: dict = {}

    def fake_reuse(src_art, src_task, target, *, identity=None):
        reused["target"] = target
        reused["identity"] = identity
        return {"id": "art_forked"}

    monkeypatch.setattr(community_route, "reuse_artifact", fake_reuse)
    monkeypatch.setattr(community_route, "find_artifact_by_id", lambda aid: ({"taskId": "task_src1"}, {"id": aid, "ownerId": "owner1"}))
    monkeypatch.setattr(community_route, "TaskRepository", lambda: type("R", (), {"get_task": staticmethod(lambda tid: {"taskId": tid, "ownerId": owner})})())
    monkeypatch.setattr(community_route, "ensure_task_in_scope", lambda task, ident: task)
    return identity, cloned, reused


def test_fork_task_cross_workspace(monkeypatch):
    pub = _pub_row(publication_id="pub_t", resource_type="task", resource_id="task_src1", publisher_owner_id="owner1")
    identity, cloned, _ = _wire_fork(monkeypatch, pub_row=pub)
    from app.routes.community import CommunityForkIn
    out = community_route.fork_publication(
        "pub_t", CommunityForkIn(title="copy", includeArtifacts=True), identity=identity
    )
    assert out.resourceType == "task"
    assert out.taskId == "task_forked"
    assert cloned["source_id"] == "task_src1"
    assert cloned["ident"]["ownerId"] == "user_OTHER"  # clone lands in caller's scope


def test_fork_artifact_cross_workspace(monkeypatch):
    pub = _pub_row(publication_id="pub_a", resource_type="artifact", resource_id="art_x", publisher_owner_id="owner1")
    identity, _, reused = _wire_fork(monkeypatch, pub_row=pub)
    from app.routes.community import CommunityForkIn
    out = community_route.fork_publication(
        "pub_a", CommunityForkIn(targetTaskId="task_mine"), identity=identity
    )
    assert out.resourceType == "artifact"
    assert out.artifactId == "art_forked"
    assert reused["target"] == "task_mine"


def test_fork_artifact_requires_target(monkeypatch):
    pub = _pub_row(publication_id="pub_a2", resource_type="artifact", resource_id="art_x")
    identity, _, _ = _wire_fork(monkeypatch, pub_row=pub)
    from app.routes.community import CommunityForkIn
    with pytest.raises(Exception):
        community_route.fork_publication("pub_a2", CommunityForkIn(), identity=identity)


def test_fork_artifact_404_when_source_task_deleted(monkeypatch):
    """If the published artifact's source task was deleted, find_artifact_by_id
    returns (None, artifact) — fork must 404, not 500 dereferencing None."""
    pub = _pub_row(publication_id="pub_a3", resource_type="artifact", resource_id="art_x")
    identity, _, _ = _wire_fork(monkeypatch, pub_row=pub)
    # source task gone: find_artifact_by_id returns (None, artifact)
    monkeypatch.setattr(
        community_route, "find_artifact_by_id", lambda aid: (None, {"id": aid, "ownerId": "owner1"})
    )
    from app.routes.community import CommunityForkIn
    with pytest.raises(Exception):
        community_route.fork_publication(
            "pub_a3", CommunityForkIn(targetTaskId="task_mine"), identity=identity
        )


def test_fork_unknown_publication_404(monkeypatch):
    pub = _pub_row(publication_id="pub_x")
    identity, _, _ = _wire_fork(monkeypatch, pub_row=pub)
    from app.routes.community import CommunityForkIn
    with pytest.raises(Exception):
        community_route.fork_publication("does_not_exist", CommunityForkIn(), identity=identity)


# ---- comments ----

def _wire_comments(monkeypatch, *, pub_exists=True):
    identity = {"ownerId": "owner1", "workspaceId": "ws1", "createdBy": "owner1"}
    stored: list[dict] = []

    class FakeRepo:
        def get_publication(self, pid):
            return _pub_row() if pub_exists else None

        def add_comment(self, **kw):
            row = {
                "comment_id": "cmc_1",
                "publication_id": kw["publication_id"],
                "author_owner_id": kw["author_owner_id"],
                "body": kw["body"],
                "parent_comment_id": kw.get("parent_comment_id"),
                "created_at": "2026-08-11T00:00:00+00:00",
            }
            stored.append(row)
            return row

        def list_comments(self, pid, *, page=1, page_size=50):
            return list(stored), len(stored)

    monkeypatch.setattr(community_route, "CommunityRepository", FakeRepo)
    return identity, stored


def test_add_comment(monkeypatch):
    identity, _ = _wire_comments(monkeypatch)
    from app.routes.community import CommunityCommentCreateIn
    out = community_route.add_comment(
        "pub_abc", CommunityCommentCreateIn(body="  nice work  "), identity=identity
    )
    assert out.body == "nice work"  # stripped
    assert out.authorOwnerId == "owner1"


def test_add_comment_rejects_empty(monkeypatch):
    identity, _ = _wire_comments(monkeypatch)
    from app.routes.community import CommunityCommentCreateIn
    with pytest.raises(Exception):
        community_route.add_comment("pub_abc", CommunityCommentCreateIn(body="   "), identity=identity)


def test_add_comment_unknown_publication(monkeypatch):
    identity, _ = _wire_comments(monkeypatch, pub_exists=False)
    from app.routes.community import CommunityCommentCreateIn
    with pytest.raises(Exception):
        community_route.add_comment("pub_abc", CommunityCommentCreateIn(body="hi"), identity=identity)


def test_list_comments(monkeypatch):
    identity, _ = _wire_comments(monkeypatch)
    from app.routes.community import CommunityCommentCreateIn
    community_route.add_comment("pub_abc", CommunityCommentCreateIn(body="first"), identity=identity)
    out = community_route.list_comments("pub_abc", page=1, pageSize=50, identity=identity)
    assert out.total == 1
    assert out.comments[0].body == "first"


# ---- publisher / author display name (debug.md #3) ----

def test_publication_out_carries_publisher_display_name(monkeypatch):
    """The browse/detail payload must surface publisherDisplayName (joined from
    openair_users) so the UI can show a name instead of a raw u_xxx owner id."""
    pub = _pub_row(publication_id="pub_dn", publisher_display_name="Alice")
    identity, _ = _wire(monkeypatch, owner="user_OTHER", listed=[pub])
    out = community_route.list_publications(
        resourceType=None, q=None, page=1, pageSize=20, identity=identity
    )
    assert out.publications[0].publisherDisplayName == "Alice"


def test_publication_out_display_name_falls_back_to_owner_when_absent(monkeypatch):
    pub = _pub_row(publication_id="pub_nodn")  # no publisher_display_name key
    identity, _ = _wire(monkeypatch, owner="user_OTHER", listed=[pub])
    out = community_route.list_publications(
        resourceType=None, q=None, page=1, pageSize=20, identity=identity
    )
    assert out.publications[0].publisherDisplayName is None


# ---- publish-side granularity hint (debug.md #6) ----

def test_publish_passes_recommended_granularity(monkeypatch):
    """Publishing with recommendedGranularity='template' threads it into the repo."""
    identity, captured = _wire(monkeypatch, owner="owner1")
    from app.routes.community import CommunityPublicationCreateIn
    community_route.publish_resource(
        CommunityPublicationCreateIn(
            resourceType="task", resourceId="task_src1",
            title="T", recommendedGranularity="template",
        ),
        identity=identity,
    )
    assert captured["publish"]["recommended_granularity"] == "template"


def test_publish_artifact_forces_full_granularity(monkeypatch):
    """A single artifact has no template concept — granularity is always 'full'."""
    identity, captured = _wire(monkeypatch, owner="owner1")
    from app.routes.community import CommunityPublicationCreateIn
    community_route.publish_resource(
        CommunityPublicationCreateIn(
            resourceType="artifact", resourceId="art_mine",
            title="A", recommendedGranularity="template",
        ),
        identity=identity,
    )
    assert captured["publish"]["recommended_granularity"] == "full"


def test_publication_out_carries_recommended_granularity(monkeypatch):
    """The browse payload surfaces recommendedGranularity so the UI can label it."""
    pub = _pub_row(publication_id="pub_g", recommended_granularity="template")
    identity, _ = _wire(monkeypatch, owner="user_OTHER", listed=[pub])
    out = community_route.list_publications(
        resourceType=None, q=None, page=1, pageSize=20, identity=identity
    )
    assert out.publications[0].recommendedGranularity == "template"


# ---- debug3 #1.2: template publications carry no artifacts ----

def test_preview_template_task_has_no_artifacts(monkeypatch):
    """A template publication must not expose source artifacts in its preview —
    template = workflow-only, matching what a fork would actually bring."""
    pub = _pub_row(publication_id="pub_tpl", resource_type="task",
                   resource_id="task_src1", recommended_granularity="template")
    identity, captured = _wire(monkeypatch, owner="user_OTHER")

    class FakeRepo:
        def get_publication(self, pid):
            return pub if pid == pub["publication_id"] else None
        def list_publications(self, **kw):
            return [], 0
        def comment_counts(self, ids):
            return {}

    monkeypatch.setattr(community_route, "CommunityRepository", FakeRepo)
    out = community_route.preview_publication(pub["publication_id"], identity=identity)
    assert out.task is not None
    assert out.task.artifacts == []  # template hides artifacts


def test_fork_template_task_forces_include_artifacts_false(monkeypatch):
    """Forking a template publication must ignore includeArtifacts=true and
    force a spec-only clone."""
    pub = _pub_row(publication_id="pub_tpl2", resource_type="task",
                   resource_id="task_src1", recommended_granularity="template")
    identity, cloned, _ = _wire_fork(monkeypatch, pub_row=pub, owner="user_OTHER")
    from app.routes.community import CommunityForkIn
    community_route.fork_publication(
        pub["publication_id"],
        CommunityForkIn(includeArtifacts=True),
        identity=identity,
    )
    assert cloned["include"] is False
