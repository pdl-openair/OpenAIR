import type {
  ToolResourceExecutionStatus,
  ToolResourceTrustContribution,
  ToolResourceTrustRecord,
} from "@openair/shared";
import type { WorkbenchArtifact, WorkbenchTaskModel } from "../model/workbenchTask";

export type TrustRecordGroupKey = "supported" | "gap" | "confirmation" | "unused";

export const EXECUTION_STATUS_LABEL: Record<ToolResourceExecutionStatus, string> = {
  invoked_success: "已调用",
  invoked_failed: "调用失败",
  waiting_authorization: "等待授权",
  manual_required: "需人工接入",
  input_recorded: "仅记录输入",
  not_connected: "未接入",
  reserved_internal: "内部预留",
};

export const TRUST_CONTRIBUTION_LABEL: Record<ToolResourceTrustContribution, string> = {
  evidence: "可作为证据",
  lead: "仅作线索",
  needs_confirmation: "需人工确认",
  not_citable: "不可引用",
  not_used: "未参与本产物",
};

export const TRUST_GROUP_TITLES: Record<TrustRecordGroupKey, string> = {
  supported: "已支撑本产物",
  gap: "可信度缺口",
  confirmation: "需要人工确认",
  unused: "未参与本次产物",
};

export function isMainPathTrustRecord(record: ToolResourceTrustRecord): boolean {
  return record.executionStatus !== "reserved_internal";
}

function isResearchStartDraftArtifact(artifact?: WorkbenchArtifact | null): boolean {
  if (!artifact) return false;
  return (
    artifact.type === "research_start_draft"
    || artifact.artifactType === "research_start_draft"
    || artifact.relatedSkillId === "research_start_draft"
  );
}

export function trustRecordDisplayName(record: ToolResourceTrustRecord): string {
  if (record.name === "Artifact review" || record.category === "human_review") return "人工核查记录";
  return record.name;
}

export function trustRecordSourceLabel(record: ToolResourceTrustRecord): string {
  if (record.sourceLabel === "User review") return "由你记录";
  if (record.sourceLabel === "Review agent") return "由复核智能体记录";
  return record.sourceLabel;
}

export function trustRecordUsedForLabel(record: ToolResourceTrustRecord): string {
  if (record.usedFor === "artifact_lifecycle_review") return "产物复核";
  return record.usedFor;
}

export function trustRecordLimitationLabel(record: ToolResourceTrustRecord): string {
  if (record.limitation === "Manual review recorded without additional notes.") return "已记录复核，但没有补充说明。";
  if (record.limitation === "Review agent recorded without additional notes.") return "复核智能体已记录，但没有补充说明。";
  if (record.limitation === "User confirmed this workbench artifact for formal use.") return "你已确认这份产物可进入后续使用。";
  if (record.limitation === "User requested revision from the workbench.") return "你已要求修改这份产物。";
  return record.limitation;
}

export function trustRecordNextActionLabel(record: ToolResourceTrustRecord): string {
  if (record.nextAction === "Artifact can be used as a verified artifact.") return "可作为已核查产物继续使用。";
  if (record.nextAction === "Revise or regenerate this artifact before using it downstream.") return "修改或重新生成后再继续使用。";
  return record.nextAction;
}

export function trustRecordStatusColor(record: ToolResourceTrustRecord): string {
  if (record.executionStatus === "invoked_failed" || record.trustContribution === "not_citable") return "error";
  if (record.executionStatus === "waiting_authorization" || record.trustContribution === "needs_confirmation") return "warning";
  if (record.executionStatus === "manual_required") return "gold";
  if (record.executionStatus === "invoked_success" && record.trustContribution === "evidence") return "success";
  if (record.trustContribution === "lead") return "processing";
  return "default";
}

export function collectVisibleTrustRecords(
  model: WorkbenchTaskModel,
  artifact?: WorkbenchArtifact | null,
): ToolResourceTrustRecord[] {
  if (isResearchStartDraftArtifact(artifact)) return [];
  const records = [...(model.trustRecords ?? []), ...(artifact?.trustRecords ?? [])];
  const deduped = new Map<string, ToolResourceTrustRecord>();
  for (const record of records) {
    if (!isMainPathTrustRecord(record)) continue;
    deduped.set(record.id, record);
  }
  return [...deduped.values()];
}

export function groupTrustRecords(records: ToolResourceTrustRecord[]): Record<TrustRecordGroupKey, ToolResourceTrustRecord[]> {
  const grouped: Record<TrustRecordGroupKey, ToolResourceTrustRecord[]> = {
    supported: [],
    gap: [],
    confirmation: [],
    unused: [],
  };

  for (const record of records) {
    if (record.trustContribution === "not_used") {
      grouped.unused.push(record);
      continue;
    }
    if (
      record.executionStatus === "manual_required" ||
      record.trustContribution === "needs_confirmation"
    ) {
      grouped.confirmation.push(record);
      continue;
    }
    if (
      record.executionStatus === "invoked_failed" ||
      record.executionStatus === "waiting_authorization" ||
      record.executionStatus === "not_connected" ||
      record.trustContribution === "not_citable"
    ) {
      grouped.gap.push(record);
      continue;
    }
    if (record.trustContribution === "evidence" || record.trustContribution === "lead") {
      grouped.supported.push(record);
      continue;
    }
    grouped.gap.push(record);
  }

  return grouped;
}
