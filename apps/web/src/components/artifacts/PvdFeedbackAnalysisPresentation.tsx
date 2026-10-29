import { Collapse, Tag, Typography } from "antd";
import type { ReactNode } from "react";

// S5 — pvd_feedback_analysis (PvdFeedbackAnalysisPresentation)
//
// Matches the pvd-experimental-feedback-round-analysis output schema exactly:
//   analysis_status, experiment_ref, summary,
//   execution_analysis {status, deviations[]},
//   measurement_analysis {status, limitations[]},
//   result_change {state, basis_refs, comparison_refs},
//   findings [{statement, epistemic_state, uncertainty, missing_evidence}],
//   anomalies/confounders [{statement, basis_refs}],
//   causal_boundary {causality_established, statement, basis_refs},
//   unresolved_questions [string],
//   downstream_readiness {status, reasons[]},
//   upstream_context {…refs}
//
// Design goals (mirrors S6/S7 presentations):
//   • 状态总览行：执行偏差 / 测量质量 / 结果变化 三枚要点徽章在最上方
//   • 下游就绪横幅：本轮结论是否可供下一轮候选分析使用
//   • 逐条发现卡片，认识状态（已观察/推断/假设）显式标注
//   • 异常、混杂、因果边界、未决问题分区呈现
//   • 全中文主视图，不暴露原始英文键名

type JsonObject = Record<string, unknown>;

// ─── Label maps ───────────────────────────────────────────────────────────────

const ANALYSIS_STATUS_LABELS: Record<string, { label: string; color: string }> = {
  complete: { label: "分析完整", color: "success" },
  partial: { label: "部分完成", color: "warning" },
  insufficient: { label: "信息不足", color: "error" },
};

const EXECUTION_STATUS_LABELS: Record<string, { label: string; color: string }> = {
  aligned: { label: "按计划执行", color: "success" },
  deviated: { label: "存在偏差", color: "warning" },
  partial: { label: "部分执行", color: "processing" },
  not_executed: { label: "未执行", color: "default" },
  unknown: { label: "执行情况未知", color: "default" },
};

const MEASUREMENT_STATUS_LABELS: Record<
  string,
  { label: string; color: string }
> = {
  usable: { label: "测量可用", color: "success" },
  usable_with_caution: { label: "测量谨慎使用", color: "warning" },
  insufficient: { label: "测量不足", color: "error" },
  conflicted: { label: "测量互相冲突", color: "error" },
  not_supplied: { label: "未提供测量", color: "default" },
};

const RESULT_CHANGE_LABELS: Record<string, { label: string; color: string }> = {
  improved: { label: "结果改善 ↑", color: "success" },
  degraded: { label: "结果恶化 ↓", color: "error" },
  unchanged: { label: "结果持平 →", color: "default" },
  mixed: { label: "结果混合", color: "warning" },
  uncertain: { label: "变化不确定", color: "warning" },
  not_assessable: { label: "无法评估变化", color: "default" },
};

const READINESS_LABELS: Record<string, { label: string; color: string }> = {
  usable: { label: "可供下一轮使用", color: "success" },
  usable_with_caution: { label: "谨慎供下一轮使用", color: "processing" },
  not_ready: { label: "暂不进入下一轮", color: "error" },
};

const EPISTEMIC_LABELS: Record<string, { label: string; color: string }> = {
  observed: { label: "已观察", color: "success" },
  inferred: { label: "推断", color: "processing" },
  hypothesized: { label: "假设", color: "warning" },
  unknown: { label: "认识状态未知", color: "default" },
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

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

/** 一枚要点徽章：执行偏差 / 测量质量 / 结果变化共用 */
function StatusBadge({
  title,
  entry,
  fallback,
}: {
  title: string;
  entry: { label: string; color: string } | undefined;
  fallback: string;
}) {
  return (
    <div
      className="rounded-lg border border-border bg-surface px-3 py-2"
      data-testid="pvd-feedback-status-badge"
    >
      <div className="text-xs text-text-muted">{title}</div>
      <div className="mt-1">
        {entry ? (
          <Tag color={entry.color} className="m-0 font-medium">
            {entry.label}
          </Tag>
        ) : (
          <Tag className="m-0">{fallback}</Tag>
        )}
      </div>
    </div>
  );
}

function BasedStatementList({
  rows,
  emptyText,
}: {
  rows: JsonObject[];
  emptyText: string;
}) {
  if (rows.length === 0) {
    return (
      <Typography.Paragraph className="!mb-0 text-sm text-text-muted">
        {emptyText}
      </Typography.Paragraph>
    );
  }
  return (
    <ul className="m-0 grid gap-1.5 pl-5 text-sm leading-relaxed text-text-secondary">
      {rows.map((row, i) => (
        <li key={i}>{text(row.statement)}</li>
      ))}
    </ul>
  );
}

function FindingCard({ row }: { row: JsonObject }) {
  const statement = text(row.statement);
  const epistemicKey = text(row.epistemic_state);
  const epistemic = EPISTEMIC_LABELS[epistemicKey];
  const uncertainty = text(row.uncertainty);
  const missingEvidence = stringList(row.missing_evidence);

  return (
    <article
      className="grid gap-1.5 rounded-lg border border-border bg-surface px-4 py-3"
      data-testid="pvd-feedback-finding-card"
    >
      <div className="flex flex-wrap items-center gap-2">
        {epistemic ? (
          <Tag color={epistemic.color} className="m-0">
            {epistemic.label}
          </Tag>
        ) : null}
        <span className="text-sm leading-relaxed text-text">{statement}</span>
      </div>
      {uncertainty ? (
        <div className="text-xs text-text-muted">不确定性：{uncertainty}</div>
      ) : null}
      {missingEvidence.length > 0 ? (
        <div className="text-xs text-text-muted">
          缺失证据：{missingEvidence.join("；")}
        </div>
      ) : null}
    </article>
  );
}

// ─── Main presentation ────────────────────────────────────────────────────────

export function PvdFeedbackAnalysisPresentation({
  document,
}: {
  document: JsonObject;
}): ReactNode {
  const statusKey = text(document.analysis_status);
  const statusEntry = ANALYSIS_STATUS_LABELS[statusKey];
  const experimentRef = text(document.experiment_ref);
  const summary = text(document.summary);

  const execution = asObject(document.execution_analysis);
  const measurement = asObject(document.measurement_analysis);
  const resultChange = asObject(document.result_change);
  const readiness = asObject(document.downstream_readiness);
  const causalBoundary = asObject(document.causal_boundary);

  const executionEntry = execution
    ? EXECUTION_STATUS_LABELS[text(execution.status)]
    : undefined;
  const measurementEntry = measurement
    ? MEASUREMENT_STATUS_LABELS[text(measurement.status)]
    : undefined;
  const changeEntry = resultChange
    ? RESULT_CHANGE_LABELS[text(resultChange.state)]
    : undefined;
  const readinessEntry = readiness
    ? READINESS_LABELS[text(readiness.status)]
    : undefined;

  const findings = asObjectArray(document.findings);
  const anomalies = asObjectArray(document.anomalies);
  const confounders = asObjectArray(document.confounders);
  const unresolvedQuestions = stringList(document.unresolved_questions);
  const readinessReasons = readiness ? stringList(readiness.reasons) : [];
  const deviations = execution ? asObjectArray(execution.deviations) : [];
  const measurementLimitations = measurement
    ? stringList(measurement.limitations)
    : [];
  const upstream = asObject(document.upstream_context);

  return (
    <section data-testid="workflow-node-pvd-feedback-analysis" className="grid gap-4">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-2">
        <Typography.Title level={4} className="!mb-0">
          实验反馈与轮次分析
        </Typography.Title>
        {statusEntry ? (
          <Tag color={statusEntry.color} className="m-0">
            {statusEntry.label}
          </Tag>
        ) : null}
        {experimentRef ? (
          <Tag className="m-0" title="本轮反馈对应的实验记录">
            {experimentRef}
          </Tag>
        ) : null}
      </div>

      {/* Summary */}
      {summary ? (
        <Typography.Paragraph className="!mb-0 text-sm leading-relaxed text-text-secondary">
          {summary}
        </Typography.Paragraph>
      ) : null}

      {/* Key status row */}
      <div className="grid gap-2 sm:grid-cols-3">
        <StatusBadge
          title="执行偏差"
          entry={executionEntry}
          fallback="执行情况未知"
        />
        <StatusBadge
          title="测量质量"
          entry={measurementEntry}
          fallback="测量状态未知"
        />
        <StatusBadge
          title="结果变化"
          entry={changeEntry}
          fallback="变化未评估"
        />
      </div>

      {/* Downstream readiness banner */}
      {readiness ? (
        <section className="rounded-lg border border-accent-strong bg-surface-soft px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <Typography.Text strong>下一轮就绪判断</Typography.Text>
            {readinessEntry ? (
              <Tag color={readinessEntry.color} className="m-0 font-medium">
                {readinessEntry.label}
              </Tag>
            ) : (
              <Tag className="m-0">未给出</Tag>
            )}
          </div>
          {readinessReasons.length > 0 ? (
            <ul className="m-0 mt-2 grid gap-1 pl-5 text-sm leading-relaxed text-text-secondary">
              {readinessReasons.map((reason, i) => (
                <li key={i}>{reason}</li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}

      {/* Findings */}
      {findings.length > 0 ? (
        <section className="grid gap-2">
          <Typography.Text strong>本轮发现（{findings.length} 条）</Typography.Text>
          <div className="grid gap-2">
            {findings.map((row, i) => (
              <FindingCard key={i} row={row} />
            ))}
          </div>
        </section>
      ) : null}

      {/* Anomalies */}
      {anomalies.length > 0 ? (
        <section className="grid gap-1">
          <Typography.Text strong>异常（{anomalies.length} 项）</Typography.Text>
          <BasedStatementList rows={anomalies} emptyText="无异常记录。" />
        </section>
      ) : null}

      {/* Confounders */}
      {confounders.length > 0 ? (
        <section className="grid gap-1">
          <Typography.Text strong>
            混杂因素（{confounders.length} 项）
          </Typography.Text>
          <BasedStatementList rows={confounders} emptyText="无混杂因素记录。" />
        </section>
      ) : null}

      {/* Causal boundary */}
      {causalBoundary && text(causalBoundary.statement) ? (
        <section className="grid gap-1">
          <Typography.Text strong>因果边界</Typography.Text>
          <Typography.Paragraph className="!mb-0 text-sm leading-relaxed text-text-secondary">
            {causalBoundary.causality_established === true ? (
              <Tag color="processing" className="m-0">
                因果已确立
              </Tag>
            ) : (
              <Tag className="m-0">因果未确立</Tag>
            )}
            {text(causalBoundary.statement)}
          </Typography.Paragraph>
        </section>
      ) : null}

      {/* Unresolved questions */}
      {unresolvedQuestions.length > 0 ? (
        <section className="grid gap-1">
          <Typography.Text strong>
            未决问题（{unresolvedQuestions.length} 项）
          </Typography.Text>
          <ul className="m-0 grid gap-1 pl-5 text-sm leading-relaxed text-text-secondary">
            {unresolvedQuestions.map((question, i) => (
              <li key={i}>{question}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {/* Technical details */}
      {deviations.length > 0 ||
      measurementLimitations.length > 0 ||
      upstream !== null ? (
        <Collapse
          ghost
          items={[
            {
              key: "details",
              label: "执行偏差、测量限制与依据来源",
              children: (
                <div className="grid gap-4">
                  {deviations.length > 0 ? (
                    <div>
                      <Typography.Text strong>执行偏差明细</Typography.Text>
                      <div className="mt-2">
                        <BasedStatementList
                          rows={deviations}
                          emptyText="无偏差记录。"
                        />
                      </div>
                    </div>
                  ) : null}
                  {measurementLimitations.length > 0 ? (
                    <div>
                      <Typography.Text strong>测量限制</Typography.Text>
                      <ul className="m-0 mt-2 grid gap-1 pl-5 text-sm text-text-secondary">
                        {measurementLimitations.map((item, i) => (
                          <li key={i}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  {upstream !== null ? (
                    <div>
                      <Typography.Text strong>依据来源</Typography.Text>
                      <div className="mt-2 grid gap-1 text-xs text-text-muted">
                        {(
                          [
                            ["推荐依据", upstream.recommendation_basis_refs],
                            ["因素依据", upstream.factor_basis_refs],
                            ["授权证据", upstream.authorized_evidence_refs],
                          ] as const
                        ).map(([label, refs]) => {
                          const rows = stringList(refs as unknown);
                          if (rows.length === 0) return null;
                          return (
                            <div key={label}>
                              {label}：{rows.join("、")}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ) : null}
                </div>
              ),
            },
          ]}
        />
      ) : null}
    </section>
  );
}
