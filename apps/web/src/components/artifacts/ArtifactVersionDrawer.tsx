import { EditOutlined, HistoryOutlined, EyeOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Drawer, Input, Modal, Space, Spin, Tabs, Tag, Timeline, Typography, message } from "antd";
import { useEffect, useMemo, useState } from "react";
import {
  editArtifactContent,
  fetchArtifactContent,
  fetchArtifactContentById,
  fetchArtifactVersion,
  fetchArtifactVersionContent,
  fetchArtifactVersions,
  regenerateArtifact,
  restoreArtifactVersion,
  type ArtifactVersionsResponse,
} from "../../api/client";
import type { ArtifactVersion, ArtifactVersionSource } from "@openair/shared";
import { StreamdownMarkdown } from "./StreamdownMarkdown";

/**
 * 产物版本管理抽屉（issue #302）：在线编辑 Markdown / 退回并重生成 / 查看与恢复历史版本。
 *
 * 自包含：打开时拉取版本历史与当前正文，编辑/恢复/重生成后通过 onChanged 通知宿主刷新。
 * 编辑用 Input.TextArea，预览用 Streamdown。
 */
export interface ArtifactVersionDrawerProps {
  open: boolean;
  artifactId: string | null;
  artifactTitle?: string | null;
  /** task-scoped 内容拉取（WorkbenchPage）；library 传 undefined 走 artifact-scoped。 */
  taskId?: string;
  /** 是否允许编辑（文本类产物）。 */
  editable: boolean;
  /** 是否允许退回+重生成（排除 research_start_draft 等设置态草案）。 */
  canRegenerate: boolean;
  onClose: () => void;
  /** 任何写入操作成功后回调（宿主据此 reload 任务）。 */
  onChanged: () => void;
}

const SOURCE_LABEL: Record<ArtifactVersionSource, string> = {
  initial: "初始版本",
  user_edit: "用户编辑",
  agent_regenerate: "重新生成",
  restore: "恢复历史",
  revision_requested: "退回修改",
};

const SOURCE_COLOR: Record<ArtifactVersionSource, string> = {
  initial: "default",
  user_edit: "processing",
  agent_regenerate: "processing",
  restore: "default",
  revision_requested: "warning",
};

function formatTime(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return iso;
  }
}

export function ArtifactVersionDrawer({
  open,
  artifactId,
  artifactTitle,
  taskId,
  editable,
  canRegenerate,
  onClose,
  onChanged,
}: ArtifactVersionDrawerProps) {
  const [tab, setTab] = useState<"edit" | "history">("edit");
  const [versions, setVersions] = useState<ArtifactVersionsResponse | null>(null);
  const [currentContent, setCurrentContent] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState("");
  const [reason, setReason] = useState("");
  const [showPreview, setShowPreview] = useState(false);
  const [saving, setSaving] = useState(false);
  const [regenFeedback, setRegenFeedback] = useState("");
  const [regenerating, setRegenerating] = useState(false);
  const [viewing, setViewing] = useState<ArtifactVersion | null>(null);
  const [viewContent, setViewContent] = useState<string>("");
  const [viewLoading, setViewLoading] = useState(false);
  const [restoringId, setRestoringId] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !artifactId) return;
    setTab(editable ? "edit" : "history");
    setDraft("");
    setReason("");
    setRegenFeedback("");
    setShowPreview(false);
    setVersions(null);
    setCurrentContent("");
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const [vers, content] = await Promise.all([
          fetchArtifactVersions(artifactId),
          taskId
            ? fetchArtifactContent(taskId, artifactId)
            : fetchArtifactContentById(artifactId),
        ]);
        if (cancelled) return;
        setVersions(vers);
        const text = content?.content ?? "";
        setCurrentContent(text);
        setDraft(text);
      } catch {
        if (!cancelled) message.error("加载产物版本信息失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, artifactId, taskId, editable]);

  const currentVersionNumber = useMemo(() => {
    const cid = versions?.currentVersionId;
    const hit = versions?.versions.find((v) => v.versionId === cid);
    return hit?.versionNumber ?? null;
  }, [versions]);

  const dirty = draft !== currentContent;

  const handleSave = async () => {
    if (!artifactId) return;
    if (!draft.trim()) {
      message.warning("内容不能为空");
      return;
    }
    const revision = versions?.revision;
    if (!Number.isInteger(revision)) {
      message.error("版本状态已失效，请重新打开版本历史后再试");
      return;
    }
    setSaving(true);
    try {
      await editArtifactContent(artifactId, draft, reason.trim() || undefined, revision as number);
      setCurrentContent(draft);
      setReason("");
      message.success("已保存为新版本，原版本已保留");
      const vers = await fetchArtifactVersions(artifactId);
      setVersions(vers);
      onChanged();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const handleRegenerate = async () => {
    if (!artifactId) return;
    setRegenerating(true);
    try {
      await regenerateArtifact(artifactId, regenFeedback.trim() || undefined);
      message.success("已记录退回反馈，产物标记为待修改");
      const vers = await fetchArtifactVersions(artifactId);
      setVersions(vers);
      setRegenFeedback("");
      onChanged();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "重新生成失败");
    } finally {
      setRegenerating(false);
    }
  };

  const handleView = async (versionId: string) => {
    if (!artifactId) return;
    setViewLoading(true);
    setViewing(null);
    setViewContent("");
    try {
      const v = await fetchArtifactVersion(artifactId, versionId);
      if (!v) return;
      // inline 版本自带正文；local/external 大文件按版本独立拉取（issue #302 r4）。
      const content = v.inlineContent ?? (await fetchArtifactVersionContent(artifactId, versionId)) ?? "";
      setViewing(v);
      setViewContent(content);
    } catch {
      message.error("加载历史版本失败");
    } finally {
      setViewLoading(false);
    }
  };

  const handleRestore = async (versionId: string, label: string) => {
    if (!artifactId) return;
    const revision = versions?.revision;
    if (!Number.isInteger(revision)) {
      message.error("版本状态已失效，请重新打开版本历史后再试");
      return;
    }
    setRestoringId(versionId);
    try {
      await restoreArtifactVersion(artifactId, versionId, revision as number);
      message.success(`已恢复${label}，并创建为新版本`);
      const vers = await fetchArtifactVersions(artifactId);
      setVersions(vers);
      onChanged();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "恢复失败");
    } finally {
      setRestoringId(null);
    }
  };

  const tabItems = [];
  if (editable) {
    tabItems.push({
      key: "edit",
      label: (
        <span>
          <EditOutlined /> 编辑
        </span>
      ),
      children: (
        <div className="flex flex-col gap-3">
          <Space>
            <Button size="small" onClick={() => setShowPreview((v) => !v)}>
              {showPreview ? "返回编辑" : "预览"}
            </Button>
            {currentVersionNumber ? (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                当前版本 v{currentVersionNumber}
              </Typography.Text>
            ) : null}
          </Space>
          {showPreview ? (
            <StreamdownMarkdown
              content={draft}
              className="wb-artifact-md min-h-[280px] rounded-md border border-border bg-surface-soft p-3"
            />
          ) : (
            <Input.TextArea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              autoSize={{ minRows: 14, maxRows: 28 }}
              spellCheck={false}
            />
          )}
          <Input.TextArea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="修改原因（可选）"
            autoSize={{ minRows: 1, maxRows: 3 }}
          />
          <Space>
            <Button type="primary" loading={saving} disabled={!dirty} onClick={handleSave}>
              保存为新版本
            </Button>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              保存后会生成新版本，原版本保留在历史中。
            </Typography.Text>
          </Space>
        </div>
      ),
    });
  }
  if (canRegenerate) {
    tabItems.push({
      key: "regenerate",
      label: (
        <span>
          <ReloadOutlined /> 退回 / 重生成
        </span>
      ),
      children: (
        <div className="flex flex-col gap-3">
          <Alert
            type="info"
            showIcon
            message="退回修改会记录反馈并将产物标记为待修改；智能体后续重新生成时会创建新版本。"
          />
          <Input.TextArea
            value={regenFeedback}
            onChange={(e) => setRegenFeedback(e.target.value)}
            placeholder="退回反馈 / 修改要求（例如：需要补充基线对比、结论证据不足）"
            autoSize={{ minRows: 3, maxRows: 6 }}
          />
          <Button danger loading={regenerating} onClick={handleRegenerate}>
            退回并标记待修改
          </Button>
        </div>
      ),
    });
  }
  tabItems.push({
    key: "history",
    label: (
      <span>
        <HistoryOutlined /> 版本历史
        {versions && versions.versions.length > 0 ? ` (${versions.versions.length})` : ""}
      </span>
    ),
    children: (
      <Spin spinning={loading}>
        {versions && versions.versions.length > 0 ? (
          <Timeline
            items={[...versions.versions]
              .sort((a, b) => b.versionNumber - a.versionNumber)
              .map((v) => {
                const isCurrent = v.versionId === versions.currentVersionId;
                const canRestore = !isCurrent && v.source !== "revision_requested";
                return {
                  color: isCurrent ? "green" : SOURCE_COLOR[v.source] ?? "gray",
                  children: (
                    <div className="flex flex-col gap-1">
                      <Space size={6} wrap>
                        <Tag color={SOURCE_COLOR[v.source] ?? "default"}>v{v.versionNumber}</Tag>
                        <Typography.Text strong>{SOURCE_LABEL[v.source] ?? v.source}</Typography.Text>
                        {isCurrent ? <Tag color="success">当前生效</Tag> : null}
                      </Space>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {formatTime(v.createdAt)} · {v.createdBy}
                      </Typography.Text>
                      {v.feedback ? (
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          原因：{v.feedback}
                        </Typography.Text>
                      ) : null}
                      {v.source === "restore" && v.restoredFromVersionId ? (
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          恢复自历史版本
                        </Typography.Text>
                      ) : null}
                      {v.source !== "revision_requested" ? (
                        <Space size={8}>
                          {!isCurrent && (
                            <Button size="small" type="link" icon={<EyeOutlined />} onClick={() => handleView(v.versionId)}>
                              查看
                            </Button>
                          )}
                          {canRestore && (
                            <Button
                              size="small"
                              type="link"
                              loading={restoringId === v.versionId}
                              onClick={() => handleRestore(v.versionId, ` v${v.versionNumber}`)}
                            >
                              恢复此版本
                            </Button>
                          )}
                        </Space>
                      ) : null}
                    </div>
                  ),
                };
              })}
          />
        ) : (
          <Typography.Text type="secondary">暂无版本历史</Typography.Text>
        )}
      </Spin>
    ),
  });

  return (
    <>
      <Drawer
        title={
          <Space direction="vertical" size={0}>
            <span>产物版本管理</span>
            {artifactTitle ? (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {artifactTitle}
              </Typography.Text>
            ) : null}
          </Space>
        }
        placement="right"
        width="60%"
        open={open}
        onClose={onClose}
        destroyOnClose
      >
        <Spin spinning={loading && !versions}>
          <Tabs
            defaultActiveKey={editable ? "edit" : "history"}
            activeKey={tab}
            onChange={(k) => setTab(k as typeof tab)}
            items={tabItems}
          />
        </Spin>
      </Drawer>

      <Modal
        title={viewing ? `版本 v${viewing.versionNumber} · ${SOURCE_LABEL[viewing.source] ?? viewing.source}` : "历史版本"}
        open={viewing !== null || viewLoading}
        onCancel={() => {
          setViewing(null);
          setViewContent("");
        }}
        footer={[
          <Button
            key="restore"
            disabled={!viewing || viewing.source === "revision_requested"}
            onClick={() => viewing && handleRestore(viewing.versionId, ` v${viewing.versionNumber}`)}
          >
            恢复此版本
          </Button>,
          <Button key="close" type="primary" onClick={() => setViewing(null)}>
            关闭
          </Button>,
        ]}
        width={720}
        destroyOnClose
      >
        <Spin spinning={viewLoading}>
          {viewing ? (
            <StreamdownMarkdown
              content={viewContent || "（该版本无正文）"}
              className="wb-artifact-md max-h-[60vh] overflow-y-auto rounded-md border border-border bg-surface-soft p-3"
            />
          ) : (
            <Typography.Text type="secondary">加载中…</Typography.Text>
          )}
        </Spin>
      </Modal>
    </>
  );
}
