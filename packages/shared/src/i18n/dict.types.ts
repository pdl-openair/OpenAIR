/** Agent registry supportedRole（开放枚举） */
export type AgentSupportedRole = "primary" | "support" | "reviewer";

export type ConfirmationDecision = "approved" | "rejected" | "needs_revision";

/** 产物类型（基于 fixture 已知值，开放枚举；新增类型时需同步 ARTIFACT_TYPE_LABELS） */
export type ArtifactType =
  | "research_note"
  | "stage_summary"
  | "evidence_pack"
  | "graph_data"
  | "report_section"
  | "code_result"
  | "experiment_record"
  | "error_report";