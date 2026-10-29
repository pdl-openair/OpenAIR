"""Community interface routes (C1).

Publish a task/artifact to the community (cross-workspace forkable), browse
community publications, and (commit 3) comment. Fork endpoints (commit 2) reuse
the v1.2 clone + content-addressed reuse mechanics, bypassing the owner-scope
fence only for published resources.

Routes live under /api/community and are exempt from the legacy-taskstore
postgres mutation guard (they write through CommunityRepository, never TaskStore).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from app.auth import RequestIdentity, ensure_task_in_scope, resolve_request_identity
from app.models.schemas import CloneTaskIn
from app.modules.artifact_catalog import find_artifact_by_id, list_catalog_artifacts, reuse_artifact
from app.modules.artifact_content import load_artifact_binary
from app.modules.community_repository import CommunityRepository, VALID_RESOURCE_TYPES
from app.modules.task_repository import TaskRepository
from app.routes.tasks import _clone_source_into

router = APIRouter(prefix="/community", tags=["community"])


# ---- models ----

class CommunityPublicationCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resourceType: str
    resourceId: str
    title: str | None = None
    summary: str | None = None
    # debug.md #6 publish-side granularity hint. 'full' = whole project (default),
    # 'template' = workflow-template-only. Forker still chooses at fork time; this
    # is the publisher's recommendation, surfaced as a card label and a fork default.
    recommendedGranularity: str = "full"


class CommunityPublicationOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    publicationId: str
    resourceType: str
    resourceId: str
    publisherOwnerId: str
    publisherDisplayName: str | None = None
    publisherWorkspaceId: str
    title: str
    summary: str | None = None
    recommendedGranularity: str = "full"
    createdAt: str | None = None
    commentCount: int = 0


class CommunityPublicationListOut(BaseModel):
    publications: list[CommunityPublicationOut]
    total: int
    page: int
    pageSize: int


def _publication_out(row: dict[str, Any], comment_count: int = 0) -> CommunityPublicationOut:
    created = row.get("created_at")
    return CommunityPublicationOut(
        publicationId=row["publication_id"],
        resourceType=row["resource_type"],
        resourceId=row["resource_id"],
        publisherOwnerId=row["publisher_owner_id"],
        publisherDisplayName=row.get("publisher_display_name"),
        publisherWorkspaceId=row["publisher_workspace_id"],
        title=row["title"],
        summary=row.get("summary"),
        recommendedGranularity=row.get("recommended_granularity") or "full",
        createdAt=str(created) if created is not None else None,
        commentCount=comment_count,
    )


def _resolve_resource_for_publish(
    resource_type: str, resource_id: str, identity: RequestIdentity
) -> tuple[str, str]:
    """Verify the caller owns the resource; return (title, owner_id).

    Raising 404/403 here keeps publication to resources the publisher controls.
    """
    if resource_type == "task":
        task = TaskRepository().get_task(resource_id)
        if not task:
            raise HTTPException(status_code=404, detail="resource not found")
        if task.get("ownerId") != identity["ownerId"]:
            raise HTTPException(status_code=403, detail="only the owner can publish this resource")
        return str(task.get("title") or "untitled"), str(task.get("ownerId"))
    if resource_type == "artifact":
        try:
            _source_task, artifact = find_artifact_by_id(resource_id)
        except Exception as exc:  # find_artifact_by_id raises if not found
            raise HTTPException(status_code=404, detail="resource not found") from exc
        if not artifact:
            raise HTTPException(status_code=404, detail="resource not found")
        if artifact.get("ownerId") != identity["ownerId"]:
            raise HTTPException(status_code=403, detail="only the owner can publish this resource")
        return str(artifact.get("title") or "untitled"), str(artifact.get("ownerId"))
    raise HTTPException(status_code=400, detail="resourceType must be 'task' or 'artifact'")


# ---- publish / browse ----

@router.post("/publications", response_model=CommunityPublicationOut, status_code=201)
def publish_resource(
    body: CommunityPublicationCreateIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> CommunityPublicationOut:
    """Publish one of the caller's own tasks/artifacts to the community.

    Idempotent per (resourceType, resourceId): re-publishing refreshes the
    listing metadata.
    """
    if body.resourceType not in VALID_RESOURCE_TYPES:
        raise HTTPException(status_code=400, detail="resourceType must be 'task' or 'artifact'")
    # granularity only applies to whole-task publications; single artifacts have
    # no workflow-template concept, so force 'full'.
    granularity = body.recommendedGranularity if body.recommendedGranularity in ("full", "template") else "full"
    if body.resourceType != "task":
        granularity = "full"
    title, _owner_id = _resolve_resource_for_publish(body.resourceType, body.resourceId, identity)
    repo = CommunityRepository()
    row = repo.publish(
        resource_type=body.resourceType,
        resource_id=body.resourceId,
        publisher_owner_id=identity["ownerId"],
        publisher_workspace_id=identity["workspaceId"],
        title=body.title or title,
        summary=body.summary,
        recommended_granularity=granularity,
        manifest={"sourceTitle": title},
    )
    return _publication_out(row)


@router.get("/publications", response_model=CommunityPublicationListOut)
def list_publications(
    resourceType: str | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> CommunityPublicationListOut:
    """Browse community publications (cross-user)."""
    repo = CommunityRepository()
    rows, total = repo.list_publications(
        resource_type=resourceType if resourceType in VALID_RESOURCE_TYPES else None,
        q=q,
        page=page,
        page_size=pageSize,
    )
    counts = repo.comment_counts([r["publication_id"] for r in rows]) if rows else {}
    return CommunityPublicationListOut(
        publications=[_publication_out(r, counts.get(r["publication_id"], 0)) for r in rows],
        total=total,
        page=page,
        pageSize=pageSize,
    )


@router.get("/publications/{publication_id}", response_model=CommunityPublicationOut)
def get_publication(
    publication_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> CommunityPublicationOut:
    repo = CommunityRepository()
    row = repo.get_publication(publication_id)
    if not row:
        raise HTTPException(status_code=404, detail="publication not found")
    counts = repo.comment_counts([row["publication_id"]])
    return _publication_out(row, counts.get(row["publication_id"], 0))


# ---- fork (clone whole-project / reuse single-artifact, cross-workspace) ----

class CommunityForkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    includeArtifacts: bool = True  # task fork only
    targetTaskId: str | None = None  # artifact fork only: caller's task to reuse into


class CommunityForkOut(BaseModel):
    publicationId: str
    resourceType: str
    taskId: str | None = None
    artifactId: str | None = None


# ---- preview (read-only cross-workspace glimpse of the published source) ----

class CommunityTaskPreviewSubStage(BaseModel):
    subStage: str
    label: str | None = None
    status: str | None = None


class CommunityTaskPreviewStage(BaseModel):
    stageKey: str
    label: str | None = None
    status: str | None = None
    summary: str | None = None
    subStages: list[CommunityTaskPreviewSubStage] = []


class CommunityTaskPreviewArtifact(BaseModel):
    artifactId: str
    title: str
    artifactType: str | None = None
    format: str | None = None


class CommunityTaskPreview(BaseModel):
    """A read-only, desensitized snapshot of a published task so the community
    detail page can show what the project contains before forking. No execution
    state, no messages, no owner-private fields."""
    goalSummary: str | None = None
    domain: str | None = None
    skillPack: str | None = None
    stages: list[CommunityTaskPreviewStage] = []
    artifacts: list[CommunityTaskPreviewArtifact] = []


class CommunityArtifactPreview(BaseModel):
    """A read-only glimpse of a published single artifact: its metadata + body
    content (capped) so the community detail page can render it."""
    title: str
    mimeType: str | None = None
    format: str | None = None
    artifactType: str | None = None
    content: str | None = None
    contentTruncated: bool = False


class CommunityPreviewOut(BaseModel):
    publicationId: str
    resourceType: str
    task: CommunityTaskPreview | None = None
    artifact: CommunityArtifactPreview | None = None


@router.post("/publications/{publication_id}/fork", response_model=CommunityForkOut, status_code=201)
def fork_publication(
    publication_id: str,
    body: CommunityForkIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> CommunityForkOut:
    """Fork a published community resource into the caller's workspace.

    - task: deep-copy clone (v1.2 fork + content-addressed artifacts) — the new
      task belongs to the caller. No source scope check; publication is the gate.
    - artifact: content-addressed reuse into the caller's target task.
    """
    repo = CommunityRepository()
    pub = repo.get_publication(publication_id)
    if not pub:
        raise HTTPException(status_code=404, detail="publication not found")
    resource_type = pub["resource_type"]
    resource_id = pub["resource_id"]

    if resource_type == "task":
        source = TaskRepository().get_task(resource_id)
        if not source:
            raise HTTPException(status_code=404, detail="published source no longer exists")
        # Template publications are workflow-only: they never carry artifacts,
        # so force includeArtifacts=False regardless of what the caller asked.
        is_template = (pub.get("recommended_granularity") or "full") == "template"
        include_artifacts = False if is_template else body.includeArtifacts
        new_task = _clone_source_into(
            source,
            identity,
            CloneTaskIn(title=body.title, includeArtifacts=include_artifacts),
        )
        return CommunityForkOut(
            publicationId=publication_id, resourceType="task", taskId=new_task["taskId"]
        )

    if resource_type == "artifact":
        if not body.targetTaskId:
            raise HTTPException(
                status_code=400, detail="targetTaskId is required to fork an artifact"
            )
        target = TaskRepository().get_task(body.targetTaskId)
        ensure_task_in_scope(target, identity)  # target must be the caller's own
        try:
            source_task, source_artifact = find_artifact_by_id(resource_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail="published source no longer exists") from exc
        # find_artifact_by_id returns (None, artifact) when the artifact row exists
        # but its source task was deleted (no FK) — guard both, else reuse_artifact
        # would dereference a None source_task and 500.
        if not source_task or not source_artifact:
            raise HTTPException(status_code=404, detail="published source no longer exists")
        new_artifact = reuse_artifact(
            source_artifact, source_task, body.targetTaskId, identity=identity
        )
        if not new_artifact:
            raise HTTPException(status_code=409, detail="artifact could not be reused")
        return CommunityForkOut(
            publicationId=publication_id,
            resourceType="artifact",
            artifactId=new_artifact["id"],
        )

    raise HTTPException(status_code=400, detail="unsupported resource type")


# ---- comments (user review / discussion) ----

# cap artifact body preview so we never stream a huge object-store blob in full
_ARTIFACT_PREVIEW_CHAR_LIMIT = 20_000


@router.get("/publications/{publication_id}/preview", response_model=CommunityPreviewOut)
def preview_publication(
    publication_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> CommunityPreviewOut:
    """A read-only, cross-workspace glimpse of a published source.

    Lets a community browser see what a project/artifact contains before forking,
    without exposing execution state, messages, or owner-private fields. The
    publication is the gate (no owner-scope check), mirroring fork.
    """
    repo = CommunityRepository()
    pub = repo.get_publication(publication_id)
    if not pub:
        raise HTTPException(status_code=404, detail="publication not found")
    resource_type = pub["resource_type"]
    resource_id = pub["resource_id"]

    if resource_type == "task":
        source = TaskRepository().get_task(resource_id)
        if not source:
            raise HTTPException(status_code=404, detail="published source no longer exists")
        # Build the workflow from stageDetails (richer: status + sub-stages). Fall
        # back to the plain stages string array when stageDetails is absent.
        #
        # Fallback for empty subStages: newer tasks materialize their workflow in
        # creationPlan.stages[].substages (and the v1.2 design instances) while
        # stageDetails stays a status-only shell with subStages=[]. The workbench
        # reads progressProjection (unaffected), but this community preview is the
        # only reader keyed on stageDetails.subStages — so without a fallback a
        # published task whose stageDetails is an empty shell shows no workflow at
        # all on the community detail page. Index creationPlan substages by stage
        # and use them when a stage's subStages list is empty.
        plan_substages_by_stage: dict[str, list[dict]] = {}
        for plan_stage in (source.get("creationPlan") or {}).get("stages") or []:
            if not isinstance(plan_stage, dict):
                continue
            sk = str(plan_stage.get("stageKey") or "")
            if not sk:
                continue
            plan_substages_by_stage[sk] = [
                sub for sub in (plan_stage.get("substages") or []) if isinstance(sub, dict)
            ]
        stage_details = source.get("stageDetails") or []
        stages: list[CommunityTaskPreviewStage] = []
        if isinstance(stage_details, list) and stage_details:
            for sd in stage_details:
                if not isinstance(sd, dict):
                    continue
                key = str(sd.get("stageKey") or "")
                if not key:
                    continue
                sub_rows = sd.get("subStages") or []
                # Empty shell → fall back to creationPlan substages for this stage.
                if not sub_rows:
                    sub_rows = plan_substages_by_stage.get(key, [])
                sub_stages = [
                    CommunityTaskPreviewSubStage(
                        subStage=str(sub.get("subStage") or sub.get("substage") or ""),
                        label=sub.get("label"),
                        status=sub.get("status"),
                    )
                    for sub in sub_rows
                    if isinstance(sub, dict)
                ]
                stages.append(CommunityTaskPreviewStage(
                    stageKey=key,
                    label=key,
                    status=sd.get("status"),
                    summary=sd.get("summary"),
                    subStages=sub_stages,
                ))
        else:
            for stage in source.get("stages") or []:
                key = stage if isinstance(stage, str) else str(stage.get("stageKey") or "")
                if key:
                    stages.append(CommunityTaskPreviewStage(stageKey=key, label=key))
        # Template publications carry no artifacts (template = workflow-only).
        # Skip the catalog lookup entirely so the preview reflects what a fork
        # would actually bring.
        is_template = (pub.get("recommended_granularity") or "full") == "template"
        artifacts: list[CommunityTaskPreviewArtifact] = []
        if not is_template:
            source_identity: RequestIdentity = {
                "ownerId": str(source.get("ownerId")),
                "workspaceId": str(source.get("workspaceId")),
                "createdBy": str(source.get("ownerId")),
            }
            try:
                catalog_rows, _ = list_catalog_artifacts(source_identity, source_task_id=resource_id, page_size=200)
            except Exception:
                catalog_rows = []
            artifacts = [
                CommunityTaskPreviewArtifact(
                    artifactId=str(row.get("id") or row.get("artifactId") or ""),
                    title=str(row.get("title") or "untitled"),
                    artifactType=row.get("kind") or row.get("artifactType"),
                    format=row.get("format"),
                )
                for row in catalog_rows
            ]
        return CommunityPreviewOut(
            publicationId=publication_id,
            resourceType="task",
            task=CommunityTaskPreview(
                goalSummary=source.get("goalSummary"),
                domain=source.get("domain"),
                skillPack=source.get("skillPack"),
                stages=stages,
                artifacts=artifacts,
            ),
        )

    if resource_type == "artifact":
        try:
            source_task, source_artifact = find_artifact_by_id(resource_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail="published source no longer exists") from exc
        if not source_task or not source_artifact:
            raise HTTPException(status_code=404, detail="published source no longer exists")
        content: str | None = None
        content_truncated = False
        try:
            blob, _mode = load_artifact_binary(
                str(source_task.get("taskId")),
                source_artifact,
                source_task.get("capabilityCalls") or [],
            )
            text = blob.decode("utf-8", errors="replace")
            if len(text) > _ARTIFACT_PREVIEW_CHAR_LIMIT:
                text = text[:_ARTIFACT_PREVIEW_CHAR_LIMIT]
                content_truncated = True
            content = text
        except Exception:
            content = None
        return CommunityPreviewOut(
            publicationId=publication_id,
            resourceType="artifact",
            artifact=CommunityArtifactPreview(
                title=str(source_artifact.get("title") or "untitled"),
                mimeType=source_artifact.get("mimeType") or source_artifact.get("mime_type"),
                format=source_artifact.get("format"),
                artifactType=source_artifact.get("kind") or source_artifact.get("artifactType"),
                content=content,
                contentTruncated=content_truncated,
            ),
        )

    raise HTTPException(status_code=400, detail="resourceType must be 'task' or 'artifact'")


@router.get("/publications/{publication_id}/artifacts/{artifact_id}/body", response_model=CommunityArtifactPreview)
def preview_publication_artifact_body(
    publication_id: str,
    artifact_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> CommunityArtifactPreview:
    """Read-only cross-workspace body of one artifact belonging to a published
    task. Lets the community detail page expand an artifact in the list to show
    its rendered body. The publication gate (task must be published, non-template)
    replaces the owner-scope check.
    """
    repo = CommunityRepository()
    pub = repo.get_publication(publication_id)
    if not pub:
        raise HTTPException(status_code=404, detail="publication not found")
    if pub["resource_type"] != "task":
        raise HTTPException(status_code=400, detail="artifact body is only available for task publications")
    # Template publications carry no artifacts.
    if (pub.get("recommended_granularity") or "full") == "template":
        raise HTTPException(status_code=404, detail="template publications carry no artifacts")
    source = TaskRepository().get_task(pub["resource_id"])
    if not source:
        raise HTTPException(status_code=404, detail="published source no longer exists")
    # find_artifact_by_id resolves across scopes; verify it belongs to this source task.
    try:
        source_task, artifact = find_artifact_by_id(artifact_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc
    if not source_task or not artifact:
        raise HTTPException(status_code=404, detail="artifact not found")
    if str(source_task.get("taskId")) != str(source.get("taskId")):
        raise HTTPException(status_code=404, detail="artifact does not belong to this publication")
    content: str | None = None
    content_truncated = False
    try:
        blob, _mode = load_artifact_binary(
            str(source_task.get("taskId")),
            artifact,
            source_task.get("capabilityCalls") or [],
        )
        text = blob.decode("utf-8", errors="replace")
        if len(text) > _ARTIFACT_PREVIEW_CHAR_LIMIT:
            text = text[:_ARTIFACT_PREVIEW_CHAR_LIMIT]
            content_truncated = True
        content = text
    except Exception:
        content = None
    return CommunityArtifactPreview(
        title=str(artifact.get("title") or "untitled"),
        mimeType=artifact.get("mimeType") or artifact.get("mime_type"),
        format=artifact.get("format"),
        artifactType=artifact.get("kind") or artifact.get("artifactType"),
        content=content,
        contentTruncated=content_truncated,
    )




class CommunityCommentCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str
    parentCommentId: str | None = None


class CommunityCommentOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    commentId: str
    publicationId: str
    authorOwnerId: str
    authorDisplayName: str | None = None
    body: str
    parentCommentId: str | None = None
    createdAt: str | None = None


class CommunityCommentListOut(BaseModel):
    comments: list[CommunityCommentOut]
    total: int
    page: int
    pageSize: int


def _comment_out(row: dict[str, Any]) -> CommunityCommentOut:
    created = row.get("created_at")
    return CommunityCommentOut(
        commentId=row["comment_id"],
        publicationId=row["publication_id"],
        authorOwnerId=row["author_owner_id"],
        authorDisplayName=row.get("author_display_name"),
        body=row["body"],
        parentCommentId=row.get("parent_comment_id"),
        createdAt=str(created) if created is not None else None,
    )


@router.post(
    "/publications/{publication_id}/comments",
    response_model=CommunityCommentOut,
    status_code=201,
)
def add_comment(
    publication_id: str,
    body: CommunityCommentCreateIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> CommunityCommentOut:
    """Post a comment on a community publication (append-only discussion)."""
    if not body.body or not body.body.strip():
        raise HTTPException(status_code=400, detail="comment body must not be empty")
    repo = CommunityRepository()
    if not repo.get_publication(publication_id):
        raise HTTPException(status_code=404, detail="publication not found")
    row = repo.add_comment(
        publication_id=publication_id,
        author_owner_id=identity["ownerId"],
        author_workspace_id=identity["workspaceId"],
        body=body.body.strip(),
        parent_comment_id=body.parentCommentId,
    )
    return _comment_out(row)


@router.get(
    "/publications/{publication_id}/comments",
    response_model=CommunityCommentListOut,
)
def list_comments(
    publication_id: str,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=50, ge=1, le=200),
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> CommunityCommentListOut:
    """List comments on a community publication, oldest first."""
    repo = CommunityRepository()
    if not repo.get_publication(publication_id):
        raise HTTPException(status_code=404, detail="publication not found")
    rows, total = repo.list_comments(publication_id, page=page, page_size=pageSize)
    return CommunityCommentListOut(
        comments=[_comment_out(r) for r in rows],
        total=total,
        page=page,
        pageSize=pageSize,
    )
