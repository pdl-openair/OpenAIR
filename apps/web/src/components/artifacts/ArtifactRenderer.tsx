import { projectStructuredArtifactForDisplay } from "./structuredArtifactProjection";
export { projectStructuredArtifactMarkdown } from "./structuredArtifactProjection";
import { ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Collapse,
  Empty,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  artifactTypeLabel,
  canonicalArtifactStatePresentation,
  sanitizeWorkbenchText,
} from "../../lib/workbenchUi";
import { getApiBaseUrl } from "../../api/client";
import { withArtifactAuth } from "../../api/http";
import type { ArtifactEvidenceRef } from "@openair/shared";
import type { WorkbenchArtifact } from "../../model/workbenchTask";
import { CodeFolderViewer } from "./CodeFolderViewer";
import { CifViewer } from "./CifViewer";
import {
  ScientificArtifactPresentation,
  scientificArtifactPresentationKind,
} from "./ScientificArtifactPresentation";
import { researchArtifactTitle } from "../../lib/researchArtifactTitle";
import { StreamdownMarkdown } from "./StreamdownMarkdown";
import type { WorkbenchTaskModel } from "../../model/workbenchTask";

export interface ArtifactRendererProps {
  artifact: WorkbenchArtifact;
  taskId?: string;
  model?: WorkbenchTaskModel;
  loading?: boolean;
  unavailableReason?: "not_found" | "transient";
  onRetry?: () => void;
  /** Workbench center can request the research body without duplicate metadata chrome. */
  bodyOnly?: boolean;
}

function MarkdownBody({
  content,
  taskId,
  artifactId,
  sourceTable = false,
}: {
  content: string;
  taskId?: string;
  artifactId?: string;
  sourceTable?: boolean;
}) {
  const baseUrl =
    taskId && artifactId
      ? `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifactId)}/files/`
      : "";

  let processed = baseUrl
    ? content.replace(
        /(!\[[^\]]*\]\()([^:)]+)(\))/g,
        (_, prefix, src, suffix) => {
          if (
            src.startsWith("http") ||
            src.startsWith("data:") ||
            src.startsWith("/")
          )
            return prefix + src + suffix;
          return prefix + baseUrl + src + suffix;
        },
      )
    : content;
  // Same-origin artifact media (raw URLs rewritten by the backend or relative
  // refs above) cannot attach an Authorization header from <img>; append the
  // query token so inline figures actually load in authenticated deployments.
  processed = processed.replace(
    /((?:src=|!\[[^\]]*\]\())("?)(\/api\/tasks\/[^"\s)]+\/raw)/g,
    (_match, lead: string, quote: string, rawUrl: string) =>
      lead + quote + withArtifactAuth(rawUrl),
  );
  // Canonical durable body is presentation-trusted; do not keyword-sanitize research prose.
  const renderedContent = sourceTable
    ? projectSourceTableMarkdown(processed)
    : processed;

  return (
    <StreamdownMarkdown
      content={renderedContent}
      testId={artifactId ? `artifact-markdown-${artifactId}` : undefined}
    />
  );
}

function projectSourceTableMarkdown(content: string): string {
  const lines = content.split(/\r?\n/);
  const headerIndex = lines.findIndex(
    (line) =>
      /\|\s*(Ref|编号)\s*\|/i.test(line) &&
      /\|\s*(Title|标题)\s*\|/i.test(line),
  );
  if (
    headerIndex < 0 ||
    !/^\s*\|[-:|\s]+\|\s*$/.test(lines[headerIndex + 1] ?? "")
  )
    return content;

  const headers = lines[headerIndex]
    .split("|")
    .slice(1, -1)
    .map((value) => value.trim().toLowerCase());
  const indexOf = (...names: string[]) =>
    headers.findIndex((header) => names.includes(header));
  const columns = [
    indexOf("ref", "编号"),
    indexOf("title", "标题"),
    indexOf("year", "年份"),
    indexOf("source", "来源"),
    indexOf("url", "doi / arxiv / url", "链接"),
    indexOf(
      "selectionreason / exclusionreason",
      "入选/排除说明",
      "入选理由",
      "阅读建议",
    ),
  ];
  if (columns.some((index) => index < 0)) return content;

  const tableRows = lines
    .slice(headerIndex + 2)
    .filter((line) => line.trim().startsWith("|"))
    .map((line) =>
      line
        .split("|")
        .slice(1, -1)
        .map((value) => value.trim()),
    )
    .map(
      (row) => `| ${columns.map((index) => row[index] ?? "").join(" | ")} |`,
    );

  if (tableRows.length === 0) return content;
  return [
    "# 来源表",
    "",
    "| 编号 | 标题 | 年份 | 来源 | 链接 | 入选理由 |",
    "|---|---|---:|---|---|---|",
    ...tableRows,
  ].join("\n");
}

function GraphPlaceholder({ title }: { title: string }) {
  return (
    <div className="wb-graph-placeholder">
      <Typography.Text type="secondary">
        图谱类产物「{title}」：交互视图尚未调用，后续按 graph_data
        契约接入渲染。
      </Typography.Text>
    </div>
  );
}

function ArtifactSummaryFallback({
  artifact,
}: {
  artifact: WorkbenchArtifact;
}) {
  const summary = sanitizeWorkbenchText(artifact.summary ?? "");
  return (
    <section
      className="wb-artifact-summary-fallback"
      data-testid="artifact-summary-fallback"
    >
      <div className="wb-artifact-summary-fallback__head">
        <Typography.Title level={4}>{artifact.title}</Typography.Title>
        <Tag>{artifactTypeLabel(artifact.type)}</Tag>
      </div>
      {summary ? (
        <Typography.Paragraph className="wb-artifact-summary-fallback__summary">
          {summary}
        </Typography.Paragraph>
      ) : (
        <Typography.Text type="secondary">暂无正文内容。</Typography.Text>
      )}
      <Typography.Text
        type="secondary"
        className="wb-artifact-summary-fallback__note"
      >
        该产物当前只有摘要记录；完整正文、附件或结构化数据生成后会在这里展开。
      </Typography.Text>
    </section>
  );
}

function CodeBlock({ content, title }: { content: string; title: string }) {
  return (
    <pre className="wb-artifact-code" aria-label={title}>
      {sanitizeWorkbenchText(content)}
    </pre>
  );
}

function TableFromCsv({ content, title }: { content: string; title: string }) {
  const lines = content.trim().split(/\r?\n/).filter(Boolean);
  if (lines.length < 2) {
    return <Typography.Paragraph>{content}</Typography.Paragraph>;
  }
  const headers = lines[0].split(",").map((h) => h.trim());
  const dataSource = lines.slice(1).map((line, i) => {
    const cells = line.split(",");
    const row: Record<string, string> = { key: String(i) };
    headers.forEach((h, j) => {
      row[h] = (cells[j] ?? "").trim();
    });
    return row;
  });
  return (
    <div
      className="wb-artifact-table-viewer"
      data-testid="artifact-table-viewer"
    >
      <Typography.Title level={4}>{title}</Typography.Title>
      <Table
        size="small"
        pagination={false}
        scroll={{ x: Math.max(760, headers.length * 180) }}
        columns={headers.map((h) => ({
          title: h,
          dataIndex: h,
          ellipsis: true,
        }))}
        dataSource={dataSource}
      />
    </div>
  );
}

function PdfViewer({
  taskId,
  artifactId,
}: {
  taskId: string;
  artifactId: string;
}) {
  const url = withArtifactAuth(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifactId)}/raw`);
  return (
    <iframe
      src={url}
      style={{
        width: "100%",
        height: "100%",
        minHeight: 600,
        border: "none",
        borderRadius: 6,
      }}
      title="PDF 查看"
    />
  );
}

function PdfPlaceholder({ artifact }: { artifact: WorkbenchArtifact }) {
  const pageText = artifact.pageCount ? `${artifact.pageCount} 页` : "页数未知";
  return (
    <div className="wb-pdf-placeholder" data-testid="artifact-pdf-placeholder">
      <div className="wb-pdf-placeholder__icon">PDF</div>
      <div className="wb-pdf-placeholder__body">
        <Typography.Title level={4}>{artifact.title}</Typography.Title>
        <div className="wb-pdf-placeholder__meta">
          <span>
            文件名：
            {artifact.title.endsWith(".pdf")
              ? artifact.title
              : `${artifact.title}.pdf`}
          </span>
          <span>来源：{artifact.sourceLabel ?? "需人工接入"}</span>
          <span>页数：{pageText}</span>
          <span>
            处理状态：
            {artifact.processingStatus ?? "等待补充，尚未进行真实 PDF 解析"}
          </span>
        </div>
        <Alert
          type="info"
          message="PDF / 论文 / 附件待接入"
          description="这里展示附件待接入状态，不表示已经接入真实 PDF 解析、外部数据库或全文下载。"
        />
        <Space wrap>
          <Button disabled>打开附件</Button>
          <Button disabled>提取正文</Button>
          <Button disabled>加入复核清单</Button>
        </Space>
      </div>
    </div>
  );
}

function safeExternalSourceUrl(value: string): string | null {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" || parsed.protocol === "http:"
      ? parsed.toString()
      : null;
  } catch {
    return null;
  }
}

function evidenceRefTestId(refId: string, part: string): string {
  return `artifact-evidence-${part}-${refId}`;
}

async function copyText(value: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    // fallback below
  }

  try {
    const el = document.createElement("textarea");
    el.value = value;
    el.setAttribute("readonly", "true");
    el.style.position = "absolute";
    el.style.left = "-9999px";
    document.body.appendChild(el);
    el.select();
    const success = document.execCommand("copy");
    document.body.removeChild(el);
    return success;
  } catch {
    return false;
  }
}

function ArtifactEvidencePanel({
  evidenceRefs,
}: {
  evidenceRefs?: ArtifactEvidenceRef[];
}) {
  const [copyFeedbackByRef, setCopyFeedbackByRef] = useState<
    Record<string, string>
  >({});
  const copyFeedbackTimeoutsRef = useRef<Record<string, number>>({});

  useEffect(() => {
    const timeouts = copyFeedbackTimeoutsRef.current;
    return () => {
      Object.values(timeouts).forEach((timeoutId) =>
        window.clearTimeout(timeoutId),
      );
    };
  }, []);

  if (!evidenceRefs || evidenceRefs.length === 0) return null;

  const setCopyFeedback = (refId: string, message: string) => {
    setCopyFeedbackByRef((prev) => ({ ...prev, [refId]: message }));
    const existingTimeout = copyFeedbackTimeoutsRef.current[refId];
    if (existingTimeout) window.clearTimeout(existingTimeout);
    copyFeedbackTimeoutsRef.current[refId] = window.setTimeout(() => {
      setCopyFeedbackByRef((prev) => {
        const next = { ...prev };
        delete next[refId];
        return next;
      });
      delete copyFeedbackTimeoutsRef.current[refId];
    }, 2200);
  };

  const handleCopyLink = async (refId: string, safeUrl: string) => {
    const ok = await copyText(safeUrl);
    setCopyFeedback(
      refId,
      ok ? "链接已复制，可稍后重试打开。" : "复制失败，请手动复制来源信息。",
    );
  };

  const handleCopySourceInfo = async (
    ref: ArtifactEvidenceRef,
    safeUrl: string | null,
  ) => {
    const lines = [
      `来源标题：${ref.sourceTitle}`,
      ref.sourceIdentifier ? `来源标识：${ref.sourceIdentifier}` : null,
      ref.locator ? `定位：${ref.locator}` : null,
      `证据片段：${ref.excerpt}`,
      safeUrl ? `来源链接：${safeUrl}` : "来源链接：不可用",
      "使用说明：该来源信息需要结合上下文判断后再作为科研结论依据。",
    ].filter(Boolean);
    const ok = await copyText(lines.join("\n"));
    setCopyFeedback(
      ref.id,
      ok ? "来源信息已复制，可继续核对。" : "复制失败，请手动记录来源信息。",
    );
  };

  return (
    <section
      className="wb-artifact-evidence"
      data-testid="artifact-evidence-panel"
      aria-label="结构化证据与来源回溯"
    >
      <div className="wb-artifact-evidence-head">
        <Typography.Title level={5}>证据与来源回溯</Typography.Title>
        <Typography.Text type="secondary">
          结论、外部来源与原文片段已关联。
        </Typography.Text>
      </div>
      <div className="wb-artifact-evidence-list">
        {evidenceRefs.map((ref) => {
          const safeUrl = safeExternalSourceUrl(ref.sourceUrl);
          return (
            <article
              className="wb-artifact-evidence-item"
              data-testid={evidenceRefTestId(ref.id, "item")}
              key={ref.id}
            >
              <Typography.Text strong className="wb-artifact-evidence-claim">
                {ref.claim}
              </Typography.Text>
              <div className="wb-artifact-evidence-meta">
                {ref.sourceIdentifier ? (
                  <Tag>{ref.sourceIdentifier}</Tag>
                ) : null}
                {ref.locator ? <Tag>{ref.locator}</Tag> : null}
              </div>
              <Typography.Text className="wb-artifact-evidence-title">
                {ref.sourceTitle}
              </Typography.Text>
              <blockquote className="wb-artifact-evidence-excerpt">
                {ref.excerpt}
              </blockquote>
              {safeUrl ? (
                <>
                  <a
                    data-testid={evidenceRefTestId(ref.id, "source-link")}
                    href={safeUrl}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    打开外部来源
                  </a>
                  <Typography.Paragraph
                    data-testid={evidenceRefTestId(
                      ref.id,
                      "source-boundary-hint",
                    )}
                    type="secondary"
                    style={{ marginTop: 6, marginBottom: 8 }}
                  >
                    若外部站点暂时打不开，不等于已保存证据片段失效。你仍可基于来源标题、来源标识与证据片段继续复核；正式引用前请再次核对原始来源。
                  </Typography.Paragraph>
                  <div
                    style={{
                      display: "flex",
                      gap: 8,
                      marginTop: 4,
                      flexWrap: "wrap",
                    }}
                  >
                    <Button
                      type="link"
                      size="small"
                      data-testid={evidenceRefTestId(ref.id, "copy-link")}
                      onClick={() => {
                        void handleCopyLink(ref.id, safeUrl);
                      }}
                    >
                      复制链接
                    </Button>
                    <Button
                      type="link"
                      size="small"
                      data-testid={evidenceRefTestId(ref.id, "copy-source")}
                      onClick={() => {
                        void handleCopySourceInfo(ref, safeUrl);
                      }}
                    >
                      复制来源信息
                    </Button>
                  </div>
                  {copyFeedbackByRef[ref.id] ? (
                    <Typography.Text
                      type="secondary"
                      data-testid={evidenceRefTestId(ref.id, "copy-feedback")}
                    >
                      {copyFeedbackByRef[ref.id]}
                    </Typography.Text>
                  ) : null}
                </>
              ) : (
                <>
                  <Typography.Text
                    type="secondary"
                    data-testid={evidenceRefTestId(
                      ref.id,
                      "source-unavailable",
                    )}
                  >
                    外部来源地址不可用
                  </Typography.Text>
                  <Typography.Paragraph
                    data-testid={evidenceRefTestId(
                      ref.id,
                      "source-boundary-hint",
                    )}
                    type="secondary"
                    style={{ marginTop: 6, marginBottom: 8 }}
                  >
                    当前无法直接打开该来源，但已保存的证据片段与来源标识仍可用于复核。正式引用前，请通过来源标题或标识再次核对原始内容。
                  </Typography.Paragraph>
                  <div style={{ marginTop: 4 }}>
                    <Button
                      type="link"
                      size="small"
                      data-testid={evidenceRefTestId(ref.id, "copy-source")}
                      onClick={() => {
                        void handleCopySourceInfo(ref, null);
                      }}
                    >
                      复制来源信息
                    </Button>
                  </div>
                  {copyFeedbackByRef[ref.id] ? (
                    <Typography.Text
                      type="secondary"
                      data-testid={evidenceRefTestId(ref.id, "copy-feedback")}
                    >
                      {copyFeedbackByRef[ref.id]}
                    </Typography.Text>
                  ) : null}
                </>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function artifactTrustMeta(
  artifact: WorkbenchArtifact,
  unavailableReason?: "not_found" | "transient",
): {
  label: string;
  color: string;
  alertType: "info" | "warning" | "error" | "success";
  note: string;
} {
  const canonical = canonicalArtifactStatePresentation(artifact);
  if (
    unavailableReason ||
    artifact.trustStatus === "failed" ||
    canonical.state === "failed"
  ) {
    return {
      label: "处理失败",
      color: "error",
      alertType: "error",
      note: "该产物当前不可作为科研结论使用；任务骨架和其它产物仍可继续查看。",
    };
  }
  if (artifact.trustStatus === "sample_placeholder") {
    return {
      label: "示例占位",
      color: "default",
      alertType: "info",
      note: "该内容是明确标注的示例占位，不代表已产生真实科研结果。",
    };
  }
  if (artifact.trustStatus === "low_confidence") {
    return {
      label: "低置信",
      color: "default",
      alertType: "warning",
      note: "该研究产出的可信度较低；正式使用前需要补充来源并人工复核。",
    };
  }
  if (artifact.trustStatus === "citable" || canonical.state === "usable") {
    return {
      label: canonical.state === "usable" ? canonical.label : "可引用",
      color: "success",
      alertType: "success",
      note: "该研究产出已具备当前任务的使用条件；正式使用时仍应保留来源和上下文。",
    };
  }
  if (canonical.state !== "unknown") {
    return {
      label: canonical.label,
      color: canonical.color,
      alertType:
        canonical.state === "rejected_or_needs_revision" ? "error" : "info",
      note: "请按当前页面提供的操作继续处理该研究产出。",
    };
  }
  return {
    label: "状态待确认",
    color: "default",
    alertType: "info",
    note: "当前还无法确认该研究产出的可信状态；页面不会自行推断为可用。",
  };
}

function artifactSourceText(
  artifact: WorkbenchArtifact,
  model?: WorkbenchTaskModel,
): string {
  if (artifact.sourceLabel) return artifact.sourceLabel;
  const mode = artifact.sourceMode ?? model?.resultSource;
  if (mode === "live") return "本次任务产物";
  if (mode === "external") return "外部来源";
  if (mode === "inline") return "项目产物";
  if (mode === "replay") return "历史回放结果";
  if (mode === "demo") return "演示结果";
  if (mode === "fixture") return "本地示例数据";
  if (mode === "none") return "来源未提供";
  return "来源未记录";
}

function artifactProcessingStatus(
  artifact: WorkbenchArtifact,
  unavailableReason?: "not_found" | "transient",
): string {
  if (unavailableReason)
    return unavailableReason === "not_found"
      ? "正文未找到"
      : "加载失败，可重试";
  if (artifact.processingStatus) return artifact.processingStatus;
  if (artifact.format === "pdf" && !artifact.path && !artifact.externalKey)
    return "等待补充";
  if (artifact.content)
    return `正文已加载 · ${canonicalArtifactStatePresentation(artifact).label}`;
  return "等待加载或补充正文";
}

function ArtifactMetadataPanel({
  artifact,
  model,
  unavailableReason,
}: {
  artifact: WorkbenchArtifact;
  model?: WorkbenchTaskModel;
  unavailableReason?: "not_found" | "transient";
}) {
  const trust = artifactTrustMeta(artifact, unavailableReason);
  const protocol = artifact.resultProtocol;
  const degraded =
    protocol?.status === "degraded" || artifact.artifactQuality === "degraded";
  const quarantinedItems = protocol?.quarantined_items ?? [];
  const repairedItems = protocol?.repaired_items ?? [];
  const quarantinedCount = quarantinedItems.length;
  const repairedCount = repairedItems.length;
  const protocolWarnings = (protocol?.warnings ?? artifact.warnings ?? []).map(
    sanitizeWorkbenchText,
  );
  const nextActions = artifact.handoffActions?.length
    ? artifact.handoffActions.map(sanitizeWorkbenchText)
    : ["确认后进入下一步", "需要时退回修改"];
  return (
    <section
      className="wb-artifact-meta-panel"
      data-testid="artifact-metadata-panel"
    >
      <div className="wb-artifact-meta-panel__head">
        <div>
          <Typography.Text type="secondary">复核信息</Typography.Text>
          <Typography.Title level={4}>{artifact.title}</Typography.Title>
        </div>
        <Tag color={trust.color}>{trust.label}</Tag>
      </div>
      <Typography.Paragraph
        type="secondary"
        className="wb-artifact-meta-user-note"
      >
        使用前请核对来源、边界和上下文；确认可接受后再进入下一步。
      </Typography.Paragraph>
      {degraded ? (
        <Alert
          data-testid="artifact-degraded-result"
          className="wb-artifact-meta-alert"
          type="warning"
          showIcon
          title="已形成可用结果，部分内容已跳过或修复"
          description={`当前产物仍可继续使用。已隔离 ${quarantinedCount} 项，已修复 ${repairedCount} 项；详细缺口保留在复核信息中。`}
        />
      ) : null}
      <Collapse
        className="wb-artifact-meta-details"
        ghost
        size="small"
        items={[
          {
            key: "artifact-metadata",
            label: "查看产物详情",
            children: (
              <>
                <div className="wb-artifact-meta-grid">
                  <div>
                    <Typography.Text type="secondary">产物类型</Typography.Text>
                    <strong>{artifactTypeLabel(artifact.type)}</strong>
                  </div>
                  <div>
                    <Typography.Text type="secondary">来源</Typography.Text>
                    <strong>{artifactSourceText(artifact, model)}</strong>
                  </div>
                  <div>
                    <Typography.Text type="secondary">可信状态</Typography.Text>
                    <strong>{trust.label}</strong>
                  </div>
                  <div>
                    <Typography.Text type="secondary">更新时间</Typography.Text>
                    <strong>
                      {artifact.createdAt ?? model?.updatedAt ?? "未记录"}
                    </strong>
                  </div>
                  <div>
                    <Typography.Text type="secondary">正文状态</Typography.Text>
                    <strong>
                      {artifactProcessingStatus(artifact, unavailableReason)}
                    </strong>
                  </div>
                  <div>
                    <Typography.Text type="secondary">
                      建议下一步
                    </Typography.Text>
                    <Space wrap size={4}>
                      {nextActions.map((action) => (
                        <Tag key={action}>{action}</Tag>
                      ))}
                    </Space>
                  </div>
                </div>
                {trust.label !== "可引用" ? (
                  <Alert
                    className="wb-artifact-meta-alert"
                    type={trust.alertType}
                    showIcon
                    message={trust.label}
                    description={trust.note}
                  />
                ) : null}
              </>
            ),
          },
          ...(degraded
            ? [
                {
                  key: "artifact-degraded-details",
                  label: "查看降级与跳过项",
                  children: (
                    <div
                      data-testid="artifact-degraded-details"
                      style={{ display: "grid", gap: 6 }}
                    >
                      <Typography.Text>
                        已隔离：{quarantinedCount} 项；已修复：{repairedCount}{" "}
                        项。
                      </Typography.Text>
                      {quarantinedItems.length ? (
                        <div>
                          <Typography.Text strong>
                            未纳入当前结果
                          </Typography.Text>
                          <ul style={{ margin: 0, paddingLeft: 20 }}>
                            {quarantinedItems.map((item, index) => (
                              <li
                                key={`${String(item.path ?? "item")}-${index}`}
                              >
                                {sanitizeWorkbenchText(
                                  String(item.path ?? "局部内容"),
                                )}
                                ：
                                {sanitizeWorkbenchText(
                                  String(item.reason ?? "未通过当前结果检查"),
                                )}
                              </li>
                            ))}
                          </ul>
                        </div>
                      ) : null}
                      {repairedItems.length ? (
                        <div>
                          <Typography.Text strong>系统已修复</Typography.Text>
                          <ul style={{ margin: 0, paddingLeft: 20 }}>
                            {repairedItems.map((item, index) => (
                              <li
                                key={`${String(item.path ?? "item")}-${index}`}
                              >
                                {sanitizeWorkbenchText(
                                  String(item.path ?? "局部内容"),
                                )}
                                ：
                                {sanitizeWorkbenchText(
                                  String(item.action ?? "已完成确定性修复"),
                                )}
                              </li>
                            ))}
                          </ul>
                        </div>
                      ) : null}
                      {protocolWarnings.length ? (
                        <div>
                          <Typography.Text strong>当前提醒</Typography.Text>
                          <ul style={{ margin: 0, paddingLeft: 20 }}>
                            {protocolWarnings.map((warning, index) => (
                              <li key={`${warning}-${index}`}>{warning}</li>
                            ))}
                          </ul>
                        </div>
                      ) : quarantinedItems.length ||
                        repairedItems.length ? null : (
                        <Typography.Text type="secondary">
                          没有额外警告；当前结果仅标记为降级可用。
                        </Typography.Text>
                      )}
                    </div>
                  ),
                },
              ]
            : []),
        ]}
      />
    </section>
  );
}

function withEvidencePanel(
  body: ReactNode,
  artifact: WorkbenchArtifact,
  model?: WorkbenchTaskModel,
  unavailableReason?: "not_found" | "transient",
) {
  return (
    <div className="wb-artifact-with-evidence">
      {body}
      <ArtifactMetadataPanel
        artifact={artifact}
        model={model}
        unavailableReason={unavailableReason}
      />
      <div
        className="wb-artifact-review-note"
        data-testid="artifact-human-review-note"
      >
        使用说明：AI
        辅助科研输出需要结合来源、边界和上下文判断后，再用于结论、引用或实验决策。
      </div>
      <ArtifactEvidencePanel evidenceRefs={artifact.evidenceRefs} />
    </div>
  );
}

function ArtifactUnavailableRecovery({
  reason,
  onRetry,
}: {
  reason: "not_found" | "transient";
  onRetry?: () => void;
}) {
  const isNotFound = reason === "not_found";
  const failureCopy = isNotFound
    ? {
        title: "科研产物暂时无法打开 / Artifact unavailable",
        success:
          "当前任务和其他科研产物不受影响。 Successful: task state, other artifacts, capability-call links, and saved evidence remain available.",
        failed:
          "Failed: this artifact body could not be opened from its current storage path.",
        impact:
          "Impact: do not rely on this artifact body for a research decision until it is restored or regenerated.",
        next: "Next step: retry loading, return to task detail, or use another linked artifact/evidence packet for review.",
      }
    : {
        title: "科研产物加载失败 / Artifact load failed",
        success:
          "当前任务和其他科研产物不受影响。 Successful: task state and other artifacts are unaffected.",
        failed: "Failed: the artifact body did not load in this request.",
        impact:
          "Impact: the current artifact view is incomplete; research output still needs human review.",
        next: "Next step: retry loading after the service or network recovers.",
      };
  return (
    <div
      data-testid="artifact-unavailable-recovery"
      data-failure-reason={reason}
    >
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <>
            <Typography.Text strong>{failureCopy.title}</Typography.Text>
            <div
              style={{
                marginTop: 8,
                display: "grid",
                gap: 4,
                textAlign: "left",
              }}
            >
              <Typography.Text type="secondary">
                {failureCopy.success}
              </Typography.Text>
              <Typography.Text type="secondary">
                {failureCopy.failed}
              </Typography.Text>
              <Typography.Text type="secondary">
                {failureCopy.impact}
              </Typography.Text>
              <Typography.Text type="secondary">
                {failureCopy.next}
              </Typography.Text>
            </div>
          </>
        }
      >
        {onRetry ? (
          <Button
            type="primary"
            icon={<ReloadOutlined />}
            data-testid="artifact-reload-button"
            onClick={onRetry}
          >
            {isNotFound ? "重新加载产物" : "重试加载"}
          </Button>
        ) : null}
      </Empty>
    </div>
  );
}

/** 按产物 type + format 选择渲染方式（通用契约，不写死学科） */
export function ArtifactRenderer({
  artifact,
  taskId,
  model,
  loading = false,
  unavailableReason,
  onRetry,
  bodyOnly = false,
}: ArtifactRendererProps) {
  const { type, format, content } = artifact;
  const title = researchArtifactTitle(artifact);
  const present = (body: ReactNode, reason?: "not_found" | "transient") =>
    bodyOnly ? body : withEvidencePanel(body, artifact, model, reason);

  if (loading) {
    return <Spin />;
  }

  if (unavailableReason) {
    if (bodyOnly) {
      return (
        <div
          data-testid="artifact-body-unavailable"
          className="flex items-center gap-2"
        >
          <Typography.Text type="secondary">
            {unavailableReason === "not_found"
              ? "当前产物正文暂时无法打开。"
              : "当前产物正文加载失败。"}
          </Typography.Text>
          {onRetry ? (
            <Button size="small" onClick={onRetry} icon={<ReloadOutlined />}>
              重新加载
            </Button>
          ) : null}
        </div>
      );
    }
    return present(
      <ArtifactUnavailableRecovery
        reason={unavailableReason}
        onRetry={onRetry}
      />,
      unavailableReason,
    );
  }

  if (format === "pdf" && taskId) {
    if (
      artifact.sourceMode === "fixture" ||
      (!artifact.path &&
        !artifact.externalKey &&
        artifact.storageType !== "inline")
    ) {
      return present(<PdfPlaceholder artifact={artifact} />);
    }
    return present(<PdfViewer taskId={taskId} artifactId={artifact.id} />);
  }

  if (format === "pdf") {
    return present(<PdfPlaceholder artifact={artifact} />);
  }

  if ((format === "png" || format === "svg" || format === "jpg" || format === "jpeg") && taskId) {
    // Binary images stream through the /raw route (served with the right MIME
    // type); never render them from inline content.
    const url = withArtifactAuth(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifact.id)}/raw`);
    return present(
      <div className="flex min-h-[400px] items-center justify-center rounded-lg border border-border bg-white p-4">
        <img
          src={url}
          alt={title || "产物图片"}
          style={{ maxWidth: "100%", maxHeight: "70vh", objectFit: "contain" }}
        />
      </div>,
    );
  }

  if ((format === "directory" || type === "code_folder") && taskId) {
    return present(
      <CodeFolderViewer taskId={taskId} artifactId={artifact.id} />,
    );
  }

  if (format === "markdown" && content) {
    return present(
      <MarkdownBody
        content={content}
        taskId={taskId}
        artifactId={artifact.id}
        sourceTable={type === "source_table"}
      />,
    );
  }

  if (format === "cif" && content) {
    return present(<CifViewer content={content} title={title} />);
  }

  if (format === "json" && content) {
    // Scientific artifacts (OT) have their own structured presentation.
    if (scientificArtifactPresentationKind(artifact, model)) {
      return present(
        <ScientificArtifactPresentation artifact={artifact} model={model} />,
      );
    }
    // Try to project structured JSON (assumptions/handoff/evidence/…) into a
    // readable Markdown body before falling back to a raw code block. Without
    // this, the artifact library sidebar shows the raw JSON for dispatch/
    // stage_summary artifacts instead of the rendered structure.
    const projected = projectStructuredArtifactForDisplay(content, title);
    if (projected) {
      return present(
        <>
          <MarkdownBody
            content={projected.markdown}
            taskId={taskId}
            artifactId={artifact.id}
          />
          {projected.technicalDetails ? (
            <details
              className="mt-4 rounded-lg border border-border p-4"
              data-testid="artifact-technical-details"
            >
              <summary className="cursor-pointer text-sm font-medium text-text">
                技术核查详情
              </summary>
              <div className="mt-3">
                <MarkdownBody content={projected.technicalDetails} />
              </div>
            </details>
          ) : null}
        </>,
      );
    }
    if (content) return present(<CodeBlock content={content} title={title} />);
  }

  if (type === "graph_data") {
    return present(<GraphPlaceholder title={title} />);
  }

  if (
    type === "code_result" ||
    type === "code_config" ||
    format === "code" ||
    format === "yaml" ||
    format === "yml"
  ) {
    if (content) return present(<CodeBlock content={content} title={title} />);
  }

  if (format === "csv" || format === "table") {
    if (content)
      return present(<TableFromCsv content={content} title={title} />);
  }

  if (content) {
    return present(
      <MarkdownBody
        content={content}
        taskId={taskId}
        artifactId={artifact.id}
      />,
    );
  }

  if (artifact.summary?.trim()) {
    if (bodyOnly) {
      return (
        <MarkdownBody
          content={artifact.summary}
          taskId={taskId}
          artifactId={artifact.id}
        />
      );
    }
    return present(<ArtifactSummaryFallback artifact={artifact} />);
  }

  if (bodyOnly) {
    return (
      <Typography.Text type="secondary">当前产物暂无正文。</Typography.Text>
    );
  }

  return present(
    <Empty
      image={Empty.PRESENTED_IMAGE_SIMPLE}
      description={
        <>
          <div>{title}</div>
          <Typography.Text type="secondary">暂无正文内容</Typography.Text>
        </>
      }
    />,
  );
}
