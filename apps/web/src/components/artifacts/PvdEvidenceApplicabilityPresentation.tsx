import { Tag, Typography } from "antd";
import type { ReactNode } from "react";
import { ScientificResultSourceDetails } from "./scientificResultDisplayValue";

// S4 — pvd_evidence_applicability (final_synthesis / comparability_analysis)
// Renders the evidence authorization table: per target support_level +
// allowed_uses + transfer_limits, plus per-record compatibility assessments.
// The "7 support_conclusion entries" keyed target_001..target_007 are the core.
// Projected directly from canonical artifact; no LLM rewording.

type JsonObject = Record<string, unknown>;

const SUPPORT_LEVEL_LABELS: Record<string, { label: string; color: string }> = {
  direct: { label: "直接支持", color: "success" },
  directional: { label: "方向性支持", color: "processing" },
  mechanism_only: { label: "仅机制支持", color: "cyan" },
  background_only: { label: "仅背景支持", color: "default" },
  not_transferable: { label: "不可迁移", color: "error" },
  insufficient: { label: "证据不足", color: "warning" },
};
const ALLOWED_USES_LABELS: Record<string, string> = {
  mechanism: "机制解释",
  trend_direction: "趋势方向",
  magnitude_reference: "量级参考",
  direct_numeric: "直接数值",
};
const ANALYSIS_STATUS_LABELS: Record<string, string> = {
  complete: "分析完整",
  partial: "分析受限",
  insufficient: "证据不足",
  not_needed: "无需分析",
};
const COMPATIBILITY_LABELS: Record<string, string> = {
  compatible: "可比",
  partially_compatible: "部分可比",
  incompatible: "不可比",
  unknown: "可比性未知",
};

function asObject(value: unknown): JsonObject | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}
function asObjectArray(value: unknown): JsonObject[] {
  return Array.isArray(value)
    ? value.map(asObject).filter((row): row is JsonObject => Boolean(row))
    : [];
}
function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}
function stringList(value: unknown): string[] {
  return (Array.isArray(value) ? value : [])
    .filter((s): s is string => typeof s === "string")
    .map((s) => s.trim())
    .filter(Boolean);
}

function SupportConclusionCard({ row }: { row: JsonObject }) {
  const target = text(row.judgment_target);
  const levelKey = text(row.support_level);
  const level = SUPPORT_LEVEL_LABELS[levelKey];
  const allowedUses = stringList(row.allowed_uses).map(
    (u) => ALLOWED_USES_LABELS[u] ?? u,
  );
  const conditions = stringList(row.conditions);
  const transferLimits = stringList(row.transfer_limits);
  const conflicts = stringList(row.conflicts);
  const unknowns = stringList(row.important_unknowns);
  const rationale = text(row.rationale);
  const recordRefs = stringList(row.evidence_record_refs);

  return (
    <article
      className="grid gap-2 rounded-lg border border-border bg-surface px-4 py-3"
      data-testid="pvd-support-conclusion-card"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Typography.Text strong>{target}</Typography.Text>
        {level ? (
          <Tag className="m-0" color={level.color}>
            {level.label}
          </Tag>
        ) : levelKey ? (
          <Tag className="m-0">{levelKey}</Tag>
        ) : null}
        {allowedUses.length > 0 ? (
          <Tag className="m-0" color="blue">
            授权用途：{allowedUses.join("、")}
          </Tag>
        ) : (
          <Tag className="m-0">无正向授权</Tag>
        )}
      </div>
      {rationale ? (
        <Typography.Paragraph className="!mb-0 text-sm leading-relaxed text-text-secondary">
          {rationale}
        </Typography.Paragraph>
      ) : null}
      {recordRefs.length > 0 ? (
        <div className="text-xs text-text-muted">
          依据记录：{recordRefs.join("、")}
        </div>
      ) : null}
      {conditions.length > 0 ? (
        <div className="text-sm">
          <span className="text-text-muted">使用条件：</span>
          {conditions.join("；")}
        </div>
      ) : null}
      {transferLimits.length > 0 ? (
        <div className="text-sm">
          <span className="text-text-muted">迁移限制：</span>
          {transferLimits.join("；")}
        </div>
      ) : null}
      {conflicts.length > 0 ? (
        <div className="text-sm">
          <span className="text-text-muted">冲突：</span>
          {conflicts.join("；")}
        </div>
      ) : null}
      {unknowns.length > 0 ? (
        <div className="text-sm">
          <span className="text-text-muted">重要未知：</span>
          {unknowns.join("；")}
        </div>
      ) : null}
    </article>
  );
}

function EvidenceAssessmentCard({ row }: { row: JsonObject }) {
  const recordId = text(row.record_id);
  const compatibility =
    COMPATIBILITY_LABELS[text(row.measurement_compatibility)] ??
    text(row.measurement_compatibility);
  const keyMatches = stringList(row.key_matches);
  const differences = stringList(row.important_differences);
  const transferLimits = stringList(row.transfer_limits);
  const uncertainty = stringList(row.uncertainty);
  return (
    <article className="grid gap-1 rounded-md border border-border bg-surface-soft px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <Typography.Text strong className="text-sm">
          {recordId || "未命名记录"}
        </Typography.Text>
        {compatibility ? (
          <Tag className="m-0">
            可比性：{compatibility}
          </Tag>
        ) : null}
      </div>
      {keyMatches.length > 0 ? (
        <div className="text-xs text-text-muted">关键匹配：{keyMatches.join("；")}</div>
      ) : null}
      {differences.length > 0 ? (
        <div className="text-xs text-text-muted">重要差异：{differences.join("；")}</div>
      ) : null}
      {transferLimits.length > 0 ? (
        <div className="text-xs text-text-muted">迁移限制：{transferLimits.join("；")}</div>
      ) : null}
      {uncertainty.length > 0 ? (
        <div className="text-xs text-text-muted">不确定性：{uncertainty.join("；")}</div>
      ) : null}
    </article>
  );
}

export function PvdEvidenceApplicabilityPresentation({
  document,
}: {
  document: JsonObject;
}): ReactNode {
  const status =
    ANALYSIS_STATUS_LABELS[text(document.analysis_status)] ??
    text(document.analysis_status);
  const conclusions = asObjectArray(document.support_conclusions);
  const assessments = asObjectArray(document.evidence_assessments);
  const conflicts = stringList(document.conflicts);
  const unknowns = stringList(document.important_unknowns);
  const summary = text(document.summary);

  if (conclusions.length === 0 && assessments.length === 0 && !summary) return null;

  return (
    <section data-testid="pvd-evidence-applicability-view" className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Typography.Title level={4} className="!mb-0">
          证据适用性与可比性分析
        </Typography.Title>
        {status ? <Tag className="m-0">{status}</Tag> : null}
      </div>

      {summary ? (
        <div className="rounded-lg border-l-4 border-accent-strong bg-surface-soft p-4">
          <Typography.Paragraph className="!mb-0 text-sm leading-relaxed">
            {summary}
          </Typography.Paragraph>
        </div>
      ) : null}

      {conclusions.length > 0 ? (
        <section className="grid gap-2">
          <Typography.Text strong>逐目标授权 · {conclusions.length} 项</Typography.Text>
          <div className="grid gap-3">
            {conclusions.map((row, i) => (
              <SupportConclusionCard key={i} row={row} />
            ))}
          </div>
        </section>
      ) : null}

      {assessments.length > 0 ? (
        <section className="grid gap-2">
          <Typography.Text strong>逐记录可比性评估 · {assessments.length} 条</Typography.Text>
          <div className="grid gap-2">
            {assessments.map((row, i) => (
              <EvidenceAssessmentCard key={i} row={row} />
            ))}
          </div>
        </section>
      ) : null}

      {conflicts.length > 0 ? (
        <section className="grid gap-1">
          <Typography.Text strong>证据冲突</Typography.Text>
          <ul className="m-0 grid gap-1 pl-5 text-sm text-text-secondary">
            {conflicts.map((c, i) => (
              <li key={`conflict-${i}`}>{c}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {unknowns.length > 0 ? (
        <section className="grid gap-1">
          <Typography.Text strong>重要未知</Typography.Text>
          <ul className="m-0 grid gap-1 pl-5 text-sm text-text-secondary">
            {unknowns.map((u, i) => (
              <li key={`unknown-${i}`}>{u}</li>
            ))}
          </ul>
        </section>
      ) : null}

      <ScientificResultSourceDetails document={document} />
    </section>
  );
}
