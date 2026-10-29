import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { MessageSquare } from "lucide-react";
import {
  Button,
  Card,
  Drawer,
  Empty,
  Input,
  List,
  Pagination,
  Radio,
  Skeleton,
  Spin,
  Tag,
  Typography,
  message,
} from "antd";
import type { RadioChangeEvent } from "antd";
import {
  addComment,
  fetchComments,
  fetchCommunityPublications,
  forkPublication,
  type CommunityComment,
  type CommunityPublication,
  type CommunityResourceType,
} from "../api/community";
import { formatBeijingDate } from "../lib/beijingTime";

type FilterType = "all" | CommunityResourceType;

const TYPE_LABELS: Record<CommunityResourceType, string> = {
  task: "整项目",
  artifact: "单产物",
};

const FILTER_OPTIONS: Array<{ label: string; value: FilterType }> = [
  { label: "全部", value: "all" },
  { label: "整项目", value: "task" },
  { label: "单产物", value: "artifact" },
];

const PAGE_SIZE = 20;

/** Prefer the user's display name; fall back to username-less owner id. */
function publisherLabel(pub: CommunityPublication): string {
  return pub.publisherDisplayName?.trim() || pub.publisherOwnerId;
}

function commenterLabel(comment: CommunityComment): string {
  return comment.authorDisplayName?.trim() || comment.authorOwnerId;
}

export default function CommunityPage() {
  const navigate = useNavigate();
  const [filterType, setFilterType] = useState<FilterType>("all");
  const [keyword, setKeyword] = useState("");
  const [committedKeyword, setCommittedKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [publications, setPublications] = useState<CommunityPublication[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [retryKey, setRetryKey] = useState(0);

  // Drawer / comments state
  const [activePublication, setActivePublication] = useState<CommunityPublication | null>(null);
  const [comments, setComments] = useState<CommunityComment[]>([]);
  const [commentsLoading, setCommentsLoading] = useState(false);
  const [commentBody, setCommentBody] = useState("");
  const [submittingComment, setSubmittingComment] = useState(false);
  const [forking, setForking] = useState(false);

  // Artifact-fork modal state
  const [forkTarget, setForkTarget] = useState<CommunityPublication | null>(null);
  const [targetTaskId, setTargetTaskId] = useState("");

  // Task-fork granularity modal state (debug.md #6): full-with-artifacts vs template-only.
  const [forkTaskTarget, setForkTaskTarget] = useState<CommunityPublication | null>(null);
  const [forkIncludeArtifacts, setForkIncludeArtifacts] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setFailed(false);
    fetchCommunityPublications(
      {
        resourceType: filterType === "all" ? undefined : filterType,
        q: committedKeyword || undefined,
        page,
        pageSize: PAGE_SIZE,
      },
      controller.signal,
    )
      .then((result) => {
        setPublications(result.publications);
        setTotal(result.total);
      })
      .catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setFailed(true);
        setPublications([]);
        setTotal(0);
        message.error("加载社区内容失败，请稍后重试");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [filterType, committedKeyword, page, retryKey]);

  const submitSearch = () => {
    setCommittedKeyword(keyword.trim());
    setPage(1);
  };

  const onFilterChange = (event: RadioChangeEvent) => {
    setFilterType(event.target.value as FilterType);
    setPage(1);
  };

  const loadComments = useCallback(async (publicationId: string) => {
    setCommentsLoading(true);
    try {
      const result = await fetchComments(publicationId);
      setComments(result.comments);
    } catch {
      message.error("加载评论失败");
      setComments([]);
    } finally {
      setCommentsLoading(false);
    }
  }, []);

  const openDetail = (publication: CommunityPublication) => {
    setActivePublication(publication);
    setCommentBody("");
    setComments([]);
    void loadComments(publication.publicationId);
  };

  const closeDetail = () => {
    setActivePublication(null);
    setCommentBody("");
  };

  const handleSubmitComment = async () => {
    if (!activePublication) return;
    const body = commentBody.trim();
    if (!body) {
      message.warning("评论内容不能为空");
      return;
    }
    setSubmittingComment(true);
    try {
      await addComment(activePublication.publicationId, { body });
      setCommentBody("");
      message.success("评论已发布");
      await loadComments(activePublication.publicationId);
      // Bump the local commentCount so the card stays roughly in sync.
      setActivePublication((prev) =>
        prev ? { ...prev, commentCount: prev.commentCount + 1 } : prev,
      );
      setPublications((prev) =>
        prev.map((item) =>
          item.publicationId === activePublication.publicationId
            ? { ...item, commentCount: item.commentCount + 1 }
            : item,
        ),
      );
    } catch {
      message.error("发布评论失败");
    } finally {
      setSubmittingComment(false);
    }
  };

  const openForkTask = (publication: CommunityPublication) => {
    setForkTaskTarget(publication);
    // Default the fork granularity from the publisher's recommendation:
    // a 'template' publication defaults to spec-only, anything else to full.
    setForkIncludeArtifacts(publication.recommendedGranularity !== "template");
  };

  const handleForkTask = async () => {
    const publication = forkTaskTarget;
    if (!publication) return;
    setForking(true);
    try {
      const result = await forkPublication(publication.publicationId, {
        includeArtifacts: forkIncludeArtifacts,
      });
      if (!result.taskId) {
        message.error("克隆项目未返回任务 ID");
        return;
      }
      message.success(forkIncludeArtifacts ? "已克隆到我的项目" : "已克隆项目模板到我的项目");
      setForkTaskTarget(null);
      navigate(`/tasks/${encodeURIComponent(result.taskId)}`);
    } catch {
      message.error("克隆项目失败，请稍后重试");
    } finally {
      setForking(false);
    }
  };

  const openForkArtifact = (publication: CommunityPublication) => {
    setForkTarget(publication);
    setTargetTaskId("");
  };

  const handleForkArtifact = async () => {
    if (!forkTarget) return;
    const trimmed = targetTaskId.trim();
    if (!trimmed) {
      message.warning("请填写目标项目 ID");
      return;
    }
    setForking(true);
    try {
      await forkPublication(forkTarget.publicationId, { targetTaskId: trimmed });
      message.success("已复用到目标项目");
      setForkTarget(null);
      navigate(`/tasks/${encodeURIComponent(trimmed)}`);
    } catch {
      message.error("复用产物失败，请检查目标项目 ID");
    } finally {
      setForking(false);
    }
  };

  const reload = () => {
    setPage(1);
    setRetryKey((value) => value + 1);
  };

  const totalCountText = useMemo(() => `共 ${total} 项`, [total]);

  return (
    <div className="mx-auto flex w-full max-w-[1100px] flex-col gap-4 px-2 pb-8" data-testid="community-page">
      <header>
        <Typography.Title level={1} className="mb-1! text-[28px]! leading-9!">研究社区</Typography.Title>
        <Typography.Paragraph className="mb-0! text-sm text-text-secondary">
          浏览社区发布的整项目与单产物，克隆到自己的工作区继续研究，或在原帖下交流。
        </Typography.Paragraph>
      </header>

      <section className="flex flex-wrap items-center gap-3" data-testid="community-filters">
        <Radio.Group options={FILTER_OPTIONS} value={filterType} onChange={onFilterChange} optionType="button" buttonStyle="solid" />
        <Input.Search
          className="min-w-[240px] flex-1"
          size="middle"
          placeholder="搜索标题或摘要"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          onSearch={submitSearch}
          allowClear
          onClear={() => {
            setKeyword("");
            setCommittedKeyword("");
            setPage(1);
          }}
          enterButton="搜索"
        />
      </section>

      <section aria-live="polite" data-testid="community-results">
        <div className="mb-3 flex items-center justify-between">
          <span className="text-sm text-text-secondary">{totalCountText}</span>
        </div>

        {loading ? (
          <div className="grid gap-3">
            {[0, 1, 2].map((index) => (
              <Card key={index} className="border border-border">
                <Skeleton active paragraph={{ rows: 2 }} />
              </Card>
            ))}
          </div>
        ) : null}

        {!loading && failed ? (
          <Card className="border border-border">
            <Empty description="加载失败，请重试">
              <Button onClick={reload}>重试</Button>
            </Empty>
          </Card>
        ) : null}

        {!loading && !failed && publications.length === 0 ? (
          <Card className="border border-border">
            <Empty description="社区暂无内容" />
          </Card>
        ) : null}

        {!loading && !failed && publications.length > 0 ? (
          <>
            <div className="grid gap-3">
              {publications.map((publication) => (
                <Card
                  key={publication.publicationId}
                  className="border border-border transition-shadow hover:shadow-sm"
                  data-testid={`community-card-${publication.publicationId}`}
                >
                  <div className="flex flex-col gap-3 max-md:flex-col">
                    <div className="min-w-0 flex-1">
                      <div className="mb-1 flex flex-wrap items-center gap-2">
                        <Tag
                          color={publication.resourceType === "task" ? "blue" : "purple"}
                          className="m-0"
                        >
                          {TYPE_LABELS[publication.resourceType]}
                        </Tag>
                        {publication.resourceType === "task" && publication.recommendedGranularity === "template" ? (
                          <Tag color="default" className="m-0">模板</Tag>
                        ) : null}
                        <Typography.Text strong className="text-base text-text">
                          {/* debug.md #4: title is a link into the community detail page */}
                          <button
                            type="button"
                            className="border-0 bg-transparent p-0 text-left text-text underline-offset-2 hover:underline cursor-pointer"
                            onClick={() => navigate(`/community/${encodeURIComponent(publication.publicationId)}`)}
                            data-testid={`community-card-title-${publication.publicationId}`}
                          >
                            {publication.title || "未命名"}
                          </button>
                        </Typography.Text>
                      </div>
                      {publication.summary ? (
                        <Typography.Paragraph
                          className="mb-2! cursor-pointer text-sm leading-6 text-text-secondary"
                          ellipsis={{ rows: 2 }}
                          onClick={() => navigate(`/community/${encodeURIComponent(publication.publicationId)}`)}
                        >
                          {publication.summary}
                        </Typography.Paragraph>
                      ) : null}
                      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-muted">
                        <span>作者：{publisherLabel(publication)}</span>
                        <span>发布于 {formatBeijingDate(publication.createdAt)}</span>
                        <span className="inline-flex items-center gap-1">
                          <MessageSquare size={13} aria-hidden="true" />
                          {publication.commentCount} 条评论
                        </span>
                      </div>
                    </div>
                    <div className="flex shrink-0 flex-wrap items-center gap-2">
                      {publication.resourceType === "task" ? (
                        <Button
                          type="primary"
                          loading={forking}
                          onClick={() => openForkTask(publication)}
                          data-testid={`community-fork-task-${publication.publicationId}`}
                        >
                          克隆到我的项目
                        </Button>
                      ) : (
                        <Button
                          type="primary"
                          loading={forking}
                          onClick={() => openForkArtifact(publication)}
                          data-testid={`community-fork-artifact-${publication.publicationId}`}
                        >
                          复用到...
                        </Button>
                      )}
                      <Button onClick={() => openDetail(publication)} data-testid={`community-comments-${publication.publicationId}`}>
                        查看评论
                      </Button>
                    </div>
                  </div>
                </Card>
              ))}
            </div>
            <div className="mt-5 flex justify-end">
              <Pagination
                current={page}
                pageSize={PAGE_SIZE}
                total={total}
                showSizeChanger={false}
                showTotal={(value) => `共 ${value} 项`}
                onChange={(next) => setPage(next)}
              />
            </div>
          </>
        ) : null}
      </section>

      <Drawer
        title={activePublication ? activePublication.title || "未命名" : "详情"}
        open={activePublication !== null}
        onClose={closeDetail}
        width={520}
        destroyOnClose
        footer={null}
      >
        {activePublication ? (
          <div className="flex h-full flex-col gap-4">
            <div className="flex flex-wrap items-center gap-2 text-sm text-text-muted">
              <Tag color={activePublication.resourceType === "task" ? "blue" : "purple"} className="m-0">
                {TYPE_LABELS[activePublication.resourceType]}
              </Tag>
              <span>作者：{publisherLabel(activePublication)}</span>
              <span>发布于 {formatBeijingDate(activePublication.createdAt)}</span>
            </div>
            {activePublication.summary ? (
              <Typography.Paragraph className="mb-0! text-sm leading-6 text-text">
                {activePublication.summary}
              </Typography.Paragraph>
            ) : null}

            <div className="flex min-h-0 flex-1 flex-col">
              <Typography.Title level={5} className="mb-2!">
                评论（{activePublication.commentCount}）
              </Typography.Title>
              {commentsLoading ? (
                <div className="flex justify-center py-8">
                  <Spin />
                </div>
              ) : comments.length === 0 ? (
                <Empty description="暂无评论" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              ) : (
                <List
                  className="min-h-0 flex-1 overflow-auto"
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
            </div>

            <div className="flex flex-col gap-2 border-t border-border pt-3">
              <Input.TextArea
                value={commentBody}
                onChange={(event) => setCommentBody(event.target.value)}
                placeholder="写下你的评论..."
                autoSize={{ minRows: 2, maxRows: 5 }}
                maxLength={2000}
              />
              <div className="flex justify-end">
                <Button type="primary" loading={submittingComment} onClick={handleSubmitComment}>
                  发布评论
                </Button>
              </div>
            </div>
          </div>
        ) : null}
      </Drawer>

      {/* Artifact fork: prompt for target task id */}
      <Drawer
        title="复用产物到目标项目"
        open={forkTarget !== null}
        onClose={() => (forking ? null : setForkTarget(null))}
        width={440}
        destroyOnClose
        footer={null}
      >
        {forkTarget ? (
          <div className="flex flex-col gap-3">
            <Typography.Paragraph className="text-sm text-text-secondary">
              将产物「{forkTarget.title || "未命名"}」复用到你的某个项目。请填写目标项目 ID（可在项目详情页 URL 中找到）。
            </Typography.Paragraph>
            <Input
              placeholder="目标项目 ID"
              value={targetTaskId}
              onChange={(event) => setTargetTaskId(event.target.value)}
              autoFocus
            />
            <div className="flex justify-end gap-2">
              <Button disabled={forking} onClick={() => setForkTarget(null)}>
                取消
              </Button>
              <Button type="primary" loading={forking} onClick={handleForkArtifact}>
                确认复用
              </Button>
            </div>
          </div>
        ) : null}
      </Drawer>

      {/* Task fork: choose granularity (debug.md #6) — full-with-artifacts vs template-only */}
      <Drawer
        title="克隆项目到我的工作区"
        open={forkTaskTarget !== null}
        onClose={() => (forking ? null : setForkTaskTarget(null))}
        width={440}
        destroyOnClose
        footer={null}
      >
        {forkTaskTarget ? (
          <div className="flex flex-col gap-3">
            <Typography.Paragraph className="text-sm text-text-secondary">
              选择克隆粒度：
            </Typography.Paragraph>
            <Radio.Group
              value={forkIncludeArtifacts}
              onChange={(event) => setForkIncludeArtifacts(event.target.value)}
              className="flex flex-col gap-2"
            >
              <Radio value={true}>
                <span className="text-sm font-medium text-text">完整克隆</span>
                <div className="text-xs text-text-muted">带入研究设计与全部产物（内容寻址，零拷贝）</div>
              </Radio>
              <Radio value={false}>
                <span className="text-sm font-medium text-text">仅工作流模板</span>
                <div className="text-xs text-text-muted">只带研究设计，不含产物——从空开始执行</div>
              </Radio>
            </Radio.Group>
            <div className="flex justify-end gap-2 pt-2">
              <Button disabled={forking} onClick={() => setForkTaskTarget(null)}>
                取消
              </Button>
              <Button type="primary" loading={forking} onClick={handleForkTask} data-testid="community-fork-task-confirm">
                确认克隆
              </Button>
            </div>
          </div>
        ) : null}
      </Drawer>
    </div>
  );
}
