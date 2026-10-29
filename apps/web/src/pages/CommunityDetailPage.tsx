import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { MessageSquare } from "lucide-react";
import {
  Button,
  Card,
  Collapse,
  Drawer,
  Empty,
  Input,
  List,
  Radio,
  Skeleton,
  Spin,
  Tag,
  Typography,
  message,
} from "antd";
import {
  addComment,
  fetchComments,
  fetchCommunityArtifactBody,
  fetchCommunityPreview,
  fetchCommunityPublication,
  forkPublication,
  type CommunityArtifactPreview,
  type CommunityComment,
  type CommunityPreview,
  type CommunityPublication,
  type CommunityTaskPreviewArtifact,
} from "../api/community";
import { usePageHeader } from "../context/PageHeaderContext";
import { ArtifactRenderer } from "../components/artifacts/ArtifactRenderer";
import { formatBeijingDate } from "../lib/beijingTime";

const TYPE_LABELS: Record<"task" | "artifact", string> = {
  task: "整项目",
  artifact: "单产物",
};

function publisherLabel(pub: CommunityPublication): string {
  return pub.publisherDisplayName?.trim() || pub.publisherOwnerId;
}

function commenterLabel(comment: CommunityComment): string {
  return comment.authorDisplayName?.trim() || comment.authorOwnerId;
}

export default function CommunityDetailPage() {
  const { publicationId = "" } = useParams<{ publicationId: string }>();
  const navigate = useNavigate();
  const [publication, setPublication] = useState<CommunityPublication | null>(null);
  const [preview, setPreview] = useState<CommunityPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  // comments
  const [comments, setComments] = useState<CommunityComment[]>([]);
  const [commentsLoading, setCommentsLoading] = useState(false);
  const [commentBody, setCommentBody] = useState("");
  const [submittingComment, setSubmittingComment] = useState(false);

  // fork
  const [forking, setForking] = useState(false);
  const [includeArtifacts, setIncludeArtifacts] = useState(true);
  // artifact fork
  const [targetTaskId, setTargetTaskId] = useState("");

  // expand-an-artifact-in-the-list drawer (debug3 #1.1)
  const [artifactBody, setArtifactBody] = useState<CommunityArtifactPreview | null>(null);
  const [artifactBodyLoading, setArtifactBodyLoading] = useState(false);

  const openArtifactBody = useCallback(
    (artifact: CommunityTaskPreviewArtifact) => {
      setArtifactBodyLoading(true);
      setArtifactBody({ title: artifact.title, mimeType: null, format: artifact.format, artifactType: artifact.artifactType, content: null, contentTruncated: false });
      fetchCommunityArtifactBody(publicationId, artifact.artifactId)
        .then((body) => setArtifactBody(body))
        .catch(() => setArtifactBody(null))
        .finally(() => setArtifactBodyLoading(false));
    },
    [publicationId],
  );

  // Surface the publication title as the page header + last breadcrumb segment
  // (首页 / 研究社区 / <publication title>). Falls back to the route default while loading.
  usePageHeader({ title: publication?.title });

  const loadComments = useCallback(async (pubId: string) => {
    setCommentsLoading(true);
    try {
      const result = await fetchComments(pubId);
      setComments(result.comments);
    } catch {
      setComments([]);
    } finally {
      setCommentsLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setLoading(true);
    setFailed(false);
    fetchCommunityPublication(publicationId, controller.signal)
      .then((pub) => {
        if (!active) return;
        setPublication(pub);
        // Default the fork granularity from the publisher's recommendation.
        setIncludeArtifacts(pub.recommendedGranularity !== "template");
        void loadComments(pub.publicationId);
        // Load the read-only source preview (task stages/artifacts or artifact body).
        fetchCommunityPreview(pub.publicationId, controller.signal)
          .then((p) => { if (active) setPreview(p); })
          .catch((err) => {
            if (!(controller.signal.aborted || err?.name === "AbortError")) setPreview(null);
          });
      })
      .catch((err) => {
        // Ignore aborts from StrictMode remount / navigation away; only flag real failures.
        if (controller.signal.aborted || err?.name === "AbortError") return;
        if (active) setFailed(true);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [publicationId, loadComments]);

  const handleSubmitComment = async () => {
    if (!publication) return;
    const body = commentBody.trim();
    if (!body) {
      message.warning("评论内容不能为空");
      return;
    }
    setSubmittingComment(true);
    try {
      await addComment(publication.publicationId, { body });
      setCommentBody("");
      message.success("评论已发布");
      await loadComments(publication.publicationId);
      setPublication((prev) => (prev ? { ...prev, commentCount: prev.commentCount + 1 } : prev));
    } catch {
      message.error("发布评论失败");
    } finally {
      setSubmittingComment(false);
    }
  };

  const handleForkTask = async () => {
    if (!publication) return;
    setForking(true);
    try {
      const result = await forkPublication(publication.publicationId, { includeArtifacts });
      if (!result.taskId) {
        message.error("克隆项目未返回任务 ID");
        return;
      }
      message.success(includeArtifacts ? "已克隆到我的项目" : "已克隆项目模板到我的项目");
      navigate(`/tasks/${encodeURIComponent(result.taskId)}`);
    } catch {
      message.error("克隆项目失败，请稍后重试");
    } finally {
      setForking(false);
    }
  };

  const handleForkArtifact = async () => {
    if (!publication) return;
    const trimmed = targetTaskId.trim();
    if (!trimmed) {
      message.warning("请填写目标项目 ID");
      return;
    }
    setForking(true);
    try {
      await forkPublication(publication.publicationId, { targetTaskId: trimmed });
      message.success("已复用到目标项目");
      navigate(`/tasks/${encodeURIComponent(trimmed)}`);
    } catch {
      message.error("复用产物失败，请检查目标项目 ID");
    } finally {
      setForking(false);
    }
  };

  return (
    <div className="mx-auto flex w-full max-w-[1100px] flex-col gap-4 px-2 pb-8" data-testid="community-detail-page">
      {loading ? (
        <Card className="border border-border">
          <Skeleton active paragraph={{ rows: 4 }} />
        </Card>
      ) : null}

      {!loading && failed ? (
        <Card className="border border-border">
          <Empty description="未找到该社区内容">
            <Button onClick={() => navigate("/community")}>返回研究社区</Button>
          </Empty>
        </Card>
      ) : null}

      {!loading && !failed && publication ? (
        <div className="grid gap-4 md:grid-cols-[1fr_320px]">
          {/* main: publication meta + comments */}
          <div className="flex flex-col gap-4">
            <Card className="border border-border">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <Tag color={publication.resourceType === "task" ? "blue" : "purple"} className="m-0">
                  {TYPE_LABELS[publication.resourceType]}
                </Tag>
                {publication.resourceType === "task" && publication.recommendedGranularity === "template" ? (
                  <Tag color="default" className="m-0">模板</Tag>
                ) : null}
                <Typography.Title level={3} className="m-0!">
                  {publication.title || "未命名"}
                </Typography.Title>
              </div>
              {publication.summary ? (
                <Typography.Paragraph className="mb-3! whitespace-pre-wrap text-sm leading-6 text-text-secondary">
                  {publication.summary}
                </Typography.Paragraph>
              ) : (
                <Typography.Paragraph className="mb-3! text-sm text-text-muted">（作者未填写摘要）</Typography.Paragraph>
              )}
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-muted">
                <span>作者：{publisherLabel(publication)}</span>
                <span>发布于 {formatBeijingDate(publication.createdAt)}</span>
                <span className="inline-flex items-center gap-1">
                  <MessageSquare size={13} aria-hidden="true" />
                  {publication.commentCount} 条评论
                </span>
              </div>
            </Card>

            {/* Source preview (debug.md #2 + debug3 #1.1): show the published task's
                workflow + expandable artifact list so the user can judge whether to fork. */}
            {preview?.task ? (
              <Card className="border border-border" title="项目内容">
                {preview.task.goalSummary ? (
                  <Typography.Paragraph className="mb-3! text-sm leading-6 text-text-secondary">
                    {preview.task.goalSummary}
                  </Typography.Paragraph>
                ) : null}
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  {preview.task.domain ? <Tag color="geekblue">{preview.task.domain}</Tag> : null}
                  {preview.task.skillPack ? <Tag color="cyan">技能包：{preview.task.skillPack}</Tag> : null}
                </div>

                {(preview.task.stages ?? []).some((s) => (s.subStages ?? []).length > 0) ? (
                  <div className="mb-3">
                    <Typography.Title level={5} className="mb-2!">工作流</Typography.Title>
                    {/* Only show stages that actually contain sub-stages, and render the
                        sub-stages inline (label + status) instead of just a count. */}
                    <Collapse
                      size="small"
                      defaultActiveKey={(preview.task.stages ?? [])
                        .filter((s) => (s.subStages ?? []).length > 0)
                        .map((s) => s.stageKey)}
                      items={(preview.task.stages ?? [])
                        .filter((stage) => (stage.subStages ?? []).length > 0)
                        .map((stage) => ({
                          key: stage.stageKey,
                          label: (
                            <span className="flex items-center gap-2">
                              <span className="font-medium">{stage.label || stage.stageKey}</span>
                              {stage.status ? <Tag className="m-0">{stage.status}</Tag> : null}
                              <span className="text-xs text-text-muted">{stage.subStages.length} 子阶段</span>
                            </span>
                          ),
                          children: (
                            <ul className="mb-0! list-disc pl-5">
                              {stage.subStages.map((sub) => (
                                <li key={sub.subStage || sub.label} className="text-sm leading-6 text-text-secondary">
                                  <span className="text-text">{sub.label || sub.subStage}</span>
                                  {sub.status ? <span className="ml-2 text-xs text-text-muted">({sub.status})</span> : null}
                                </li>
                              ))}
                            </ul>
                          ),
                        }))}
                    />
                  </div>
                ) : null}

                <Typography.Title level={5} className="mb-2!">
                  包含产物（{(preview.task.artifacts ?? []).length}）
                </Typography.Title>
                {(preview.task.artifacts ?? []).length === 0 ? (
                  <Typography.Text className="text-sm text-text-muted">无产物（模板项目仅含工作流）</Typography.Text>
                ) : (
                  <List
                    size="small"
                    dataSource={preview.task.artifacts ?? []}
                    renderItem={(artifact) => (
                      <List.Item className="px-0!">
                        <button
                          type="button"
                          onClick={() => openArtifactBody(artifact)}
                          className="flex min-w-0 flex-1 items-center justify-between gap-2 border-0 bg-transparent p-0 text-left cursor-pointer"
                        >
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm text-text hover:underline">{artifact.title}</span>
                            <span className="text-xs text-text-muted">
                              {[artifact.artifactType, artifact.format].filter(Boolean).join(" · ")}
                            </span>
                          </span>
                        </button>
                      </List.Item>
                    )}
                  />
                )}
              </Card>
            ) : null}

            {preview?.artifact ? (
              <Card className="border border-border" title="产物正文">
                {preview.artifact.content ? (
                  <>
                    <ArtifactRenderer
                      artifact={{
                        id: publication.resourceId,
                        type: preview.artifact.artifactType ?? preview.artifact.format ?? "execution_output",
                        artifactType: preview.artifact.artifactType ?? undefined,
                        title: preview.artifact.title,
                        format: preview.artifact.format ?? "json",
                        relatedStage: "insight_generation",
                        content: preview.artifact.content,
                      }}
                      bodyOnly
                    />
                    {preview.artifact.contentTruncated ? (
                      <Typography.Text className="mt-2 block text-xs text-text-muted">
                        正文较长，已截断预览；克隆后可查看完整内容。
                      </Typography.Text>
                    ) : null}
                  </>
                ) : (
                  <Typography.Text className="text-sm text-text-muted">产物正文暂不可用</Typography.Text>
                )}
              </Card>
            ) : null}

            <Card className="border border-border" title={`评论（${publication.commentCount}）`}>
              {commentsLoading ? (
                <div className="flex justify-center py-8">
                  <Spin />
                </div>
              ) : comments.length === 0 ? (
                <Empty description="暂无评论，成为第一个评论的人" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              ) : (
                <List
                  dataSource={comments}
                  renderItem={(comment) => (
                    <List.Item className="block! border-b! border-border! px-0!">
                      <div className="mb-1 flex items-center gap-2 text-xs text-text-muted">
                        <span className="font-medium text-text-secondary">{commenterLabel(comment)}</span>
                        <span>{formatBeijingDate(comment.createdAt)}</span>
                      </div>
                      <div className="whitespace-pre-wrap text-sm text-text">{comment.body}</div>
                    </List.Item>
                  )}
                />
              )}
              <div className="mt-3 flex flex-col gap-2">
                <Input.TextArea
                  rows={3}
                  placeholder="写下你的评论……"
                  value={commentBody}
                  onChange={(event) => setCommentBody(event.target.value)}
                  maxLength={2000}
                  showCount
                />
                <div className="flex justify-end">
                  <Button type="primary" loading={submittingComment} onClick={handleSubmitComment}>
                    发布评论
                  </Button>
                </div>
              </div>
            </Card>
          </div>

          {/* side: fork panel */}
          <div className="flex flex-col gap-3">
            <Card className="border border-border" title="克隆 / 复用">
              {publication.resourceType === "task" ? (
                <div className="flex flex-col gap-3">
                  <Radio.Group
                    value={includeArtifacts}
                    onChange={(event) => setIncludeArtifacts(event.target.value)}
                    className="flex flex-col gap-2"
                  >
                    <Radio value={true}>
                      <span className="text-sm font-medium text-text">完整克隆</span>
                      <div className="text-xs text-text-muted">带入研究设计与全部产物</div>
                    </Radio>
                    <Radio value={false}>
                      <span className="text-sm font-medium text-text">仅工作流模板</span>
                      <div className="text-xs text-text-muted">只带研究设计，不含产物</div>
                    </Radio>
                  </Radio.Group>
                  <Button type="primary" block loading={forking} onClick={handleForkTask}>
                    克隆到我的项目
                  </Button>
                </div>
              ) : (
                <div className="flex flex-col gap-3">
                  <Typography.Paragraph className="mb-0! text-xs text-text-muted">
                    将该产物复用到你的某个项目（填写目标项目 ID，可在项目详情页 URL 中找到）。
                  </Typography.Paragraph>
                  <Input
                    placeholder="目标项目 ID"
                    value={targetTaskId}
                    onChange={(event) => setTargetTaskId(event.target.value)}
                  />
                  <Button type="primary" block loading={forking} onClick={handleForkArtifact}>
                    复用到目标项目
                  </Button>
                </div>
              )}
            </Card>
          </div>
        </div>
      ) : null}

      {/* Expand-an-artifact drawer: renders the artifact body via ArtifactRenderer (#1.1). */}
      <Drawer
        title={artifactBody?.title || "产物正文"}
        open={artifactBody !== null}
        onClose={() => (artifactBodyLoading ? null : setArtifactBody(null))}
        width={640}
        destroyOnClose
        footer={null}
      >
        {artifactBodyLoading ? (
          <div className="flex justify-center py-8"><Spin /></div>
        ) : artifactBody?.content ? (
          <>
            <ArtifactRenderer
              artifact={{
                id: "preview",
                type: artifactBody.artifactType ?? artifactBody.format ?? "execution_output",
                artifactType: artifactBody.artifactType ?? undefined,
                title: artifactBody.title,
                format: artifactBody.format ?? "json",
                relatedStage: "insight_generation",
                content: artifactBody.content,
              }}
              bodyOnly
            />
            {artifactBody.contentTruncated ? (
              <Typography.Text className="mt-2 block text-xs text-text-muted">
                正文较长，已截断预览；克隆后可查看完整内容。
              </Typography.Text>
            ) : null}
          </>
        ) : (
          <Empty description="产物正文暂不可用" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </Drawer>
    </div>
  );
}
