import { STAGE_LABELS } from "@openair/shared";
import type { WorkbenchArtifact, WorkbenchCapabilityCall, WorkbenchTaskModel } from "../model/workbenchTask";
import { buildRuntimeAuditSummary, findRuntimeAuditCall } from "./runtimeAudit";
import { artifactTypeLabel } from "./workbenchUi";

export type EvidenceLayer = "source" | "evidence" | "summary" | "inference" | "recommendation" | "boundary";

export interface EvidenceChainRow {
  id: string;
  layer: EvidenceLayer;
  layerLabel: string;
  sourceRef: string;
  evidenceSummary: string;
  supportsClaim: string;
  userVisible: boolean;
  auditVisible: boolean;
  verdict: "PASS" | "GAP";
}

export interface RuntimeProofRow {
  label: string;
  value: string;
  verdict: "PASS" | "GAP";
}

export interface RuntimeProofSummary {
  call: WorkbenchCapabilityCall | null;
  rows: RuntimeProofRow[];
  gaps: string[];
}

export interface ArtifactTraceabilityRow {
  label: string;
  value: string;
  verdict: "PASS" | "GAP";
}

const LAYER_LABELS: Record<EvidenceLayer, string> = {
  source: "来源",
  evidence: "证据",
  summary: "系统归纳",
  inference: "推断",
  recommendation: "建议",
  boundary: "边界 / 待验证",
};

function missing(label: string): string {
  return `${label}未记录`;
}

function nonEmpty(value?: string | null): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

function fieldRow(label: string, value: string | null | undefined, gapText?: string): RuntimeProofRow {
  const normalized = nonEmpty(value);
  return {
    label,
    value: normalized ?? gapText ?? missing(label),
    verdict: normalized ? "PASS" : "GAP",
  };
}

function artifactCall(model: WorkbenchTaskModel | undefined, artifact: WorkbenchArtifact): WorkbenchCapabilityCall | null {
  return findRuntimeAuditCall(model, artifact);
}

function taskStarterId(model: WorkbenchTaskModel | undefined, call: WorkbenchCapabilityCall | null): string {
  const starterId = call?.sourceMetadata?.starterId;
  if (typeof starterId === "string" && starterId.trim()) {
    if (starterId.includes("source_evidence")) return "整理来源与证据";
    if (starterId.includes("direction_review") || starterId.includes("literature_research")) return "调研一个研究方向";
    if (starterId.includes("work_plan")) return "研究计划智能体";
    return "已记录的研究起点";
  }
  if (!model) return "本轮未接入";
  if (model.title.includes("调研") || model.title.includes("文献")) return "调研一个研究方向";
  if (model.title.includes("来源") || model.title.includes("证据")) return "整理来源与证据";
  if (model.title.includes("计划")) return "研究计划智能体";
  return "未记录";
}

function sourceModeBoundary(mode?: string | null): string {
  if (!mode) return "来源模式未记录；需要结合任务结果来源和 capability call 复核。";
  if (mode === "live") return "本次运行；仍受题录、摘要或可访问来源边界约束。";
  if (mode === "metadata_or_abstract_bounded") return "只基于题录、摘要、来源清单或可读性线索；不是全文精读。";
  if (mode === "replay") return "历史回放结果；不能当作本次 Live 运行。";
  if (mode === "fixture") return "来源仍需补充；不能当作最终科研结论。";
  return `来源模式：${mode}`;
}

function row(
  layer: EvidenceLayer,
  id: string,
  sourceRef: string,
  evidenceSummary: string,
  supportsClaim: string,
  verdict: "PASS" | "GAP" = "PASS",
): EvidenceChainRow {
  return {
    id,
    layer,
    layerLabel: LAYER_LABELS[layer],
    sourceRef,
    evidenceSummary,
    supportsClaim,
    userVisible: layer !== "source" && layer !== "evidence" ? true : true,
    auditVisible: true,
    verdict,
  };
}

function artifactDefaultEvidence(model: WorkbenchTaskModel | undefined, artifact: WorkbenchArtifact): EvidenceChainRow[] {
  const stage = STAGE_LABELS[artifact.relatedStage];
  const title = artifact.title || artifactTypeLabel(artifact.type);
  const call = artifactCall(model, artifact);
  const sourceRef = call?.provider || call?.sourceAccess || artifact.relatedSkillId || "用户输入 / 任务上下文";
  const sourceMode = call?.sourceMode ?? artifact.sourceMode ?? model?.resultSource;
  const boundary = sourceModeBoundary(sourceMode);
  const base = [
    row("source", `${artifact.id}-source`, sourceRef, `来源类型：${sourceMode ?? "未记录"}；产物：${title}`, "说明该产物基于哪些输入、来源或能力调用生成。", sourceRef === "用户输入 / 任务上下文" && !model ? "GAP" : "PASS"),
    row("evidence", `${artifact.id}-evidence`, title, artifact.summary || `${stage}阶段产物，类型：${artifactTypeLabel(artifact.type)}。`, "支撑用户查看当前阶段完成了什么。"),
    row("summary", `${artifact.id}-summary`, title, model?.stages.find((s) => s.stage === artifact.relatedStage)?.summary || artifact.summary || "系统已整理当前产物摘要。", "系统归纳当前任务进展。"),
    row("inference", `${artifact.id}-inference`, title, "需要科研人员结合来源、证据和研究目标判断是否采纳。", "明确 AI 归纳不是最终科研结论。"),
    row("recommendation", `${artifact.id}-recommendation`, title, model?.stages.find((s) => s.stage === artifact.relatedStage)?.nextSteps || "下一步建议未记录，请先复核来源和证据再继续。", "指向后续研究动作。"),
    row("boundary", `${artifact.id}-boundary`, title, model?.stages.find((s) => s.stage === artifact.relatedStage)?.risks || boundary, "说明当前结果的使用边界。"),
  ];
  return base;
}

export function buildEvidenceChain(model: WorkbenchTaskModel | undefined, artifact: WorkbenchArtifact): EvidenceChainRow[] {
  const rows = artifactDefaultEvidence(model, artifact);
  const refs = artifact.evidenceRefs ?? [];
  if (refs.length === 0) return rows;
  const refRows = refs.flatMap((ref) => [
    row("source", `${ref.id}-source`, ref.sourceIdentifier || ref.sourceUrl || ref.sourceTitle, `来源标题：${ref.sourceTitle}`, ref.claim),
    row("evidence", `${ref.id}-evidence`, ref.sourceTitle, ref.excerpt, ref.claim),
  ]);
  return [...refRows, ...rows.filter((item) => item.layer !== "source" && item.layer !== "evidence")];
}

export function buildRuntimeProof(model: WorkbenchTaskModel | undefined, artifact?: WorkbenchArtifact | null): RuntimeProofSummary {
  const audit = buildRuntimeAuditSummary(model, artifact);
  const call = audit.call;
  const rows: RuntimeProofRow[] = [
    fieldRow("任务", model?.title ?? model?.goalSummary),
    fieldRow("处理记录", call?.id),
    fieldRow("起点来源", taskStarterId(model, call)),
    ...audit.fields.map((item) => ({
      label: item.label,
      value: item.value,
      verdict: item.complete ? "PASS" as const : "GAP" as const,
    })),
    fieldRow("开始时间", call?.startedAt),
    fieldRow("结束时间", call?.endedAt),
    fieldRow("来源获取状态", call?.sourceAccess || sourceModeBoundary(call?.sourceMode ?? model?.resultSource)),
  ];
  return {
    call,
    rows,
    gaps: [...new Set([...audit.missingLabels, ...rows.filter((item) => item.verdict === "GAP").map((item) => item.label)])],
  };
}

export function buildArtifactTraceability(model: WorkbenchTaskModel, artifact: WorkbenchArtifact): ArtifactTraceabilityRow[] {
  const call = artifactCall(model, artifact);
  const evidenceCount = artifact.evidenceRefs?.length ?? 0;
  const chain = buildEvidenceChain(model, artifact);
  return [
    fieldRow("任务", model.title || model.goalSummary),
    fieldRow("起点来源", taskStarterId(model, call)),
    fieldRow("产物", artifact.title),
    fieldRow("产物类型", artifact.type),
    fieldRow("创建时间", artifact.createdAt ?? model.updatedAt),
    fieldRow("来源摘要", artifact.summary || call?.provider || call?.sourceAccess || model.goalSummary),
    fieldRow("证据条目", String(evidenceCount || chain.filter((item) => item.layer === "evidence").length)),
    fieldRow("处理记录", call?.id),
    fieldRow("复现定位", call?.runId || call?.id),
    fieldRow("边界说明", chain.find((item) => item.layer === "boundary")?.evidenceSummary),
    fieldRow("下一步", chain.find((item) => item.layer === "recommendation")?.evidenceSummary),
  ];
}
