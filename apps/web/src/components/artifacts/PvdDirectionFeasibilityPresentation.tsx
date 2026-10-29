import { Collapse, Tag, Typography } from "antd";
import type { ReactNode } from "react";
import { researchPresentationParameterName } from "@openair/shared";

// S6 — pvd_candidate_direction (PvdCandidateDirectionPresentation)
// S7 — pvd_feasibility_validation (PvdFeasibilityValidationPresentation)
//
// Rewritten to match the current output schemas exactly:
//   S6: factor_assessments (controlledAssessment / contextualAssessment),
//       prioritized_factors, factors_to_hold, recommended_next_experiment_focus,
//       analysis_basis, unresolved_hypotheses, assumptions, limitations
//   S7: direction_assessments, cross_parameter_assessment, shared_conditions,
//       shared_risks, important_unknowns, limitations, recommendation_gate,
//       validation_status, availability
//
// Design goals:
//   • 4-parameter summary row at the top of S6 (power/n2/ar/time direction + role)
//   • Per-factor cards with expected_effect, confidence, risk, constraint_status
//   • "下一轮优先关注" banner from recommended_next_experiment_focus
//   • S7: per-direction cards with feasibility colour + decision + conditions/conflicts
//   • Everything in Chinese; no raw English keys exposed in primary view

type JsonObject = Record<string, unknown>;

// ─── Shared label maps ────────────────────────────────────────────────────────

const DIRECTION_LABELS: Record<string, string> = {
  increase: "增加 ↑",
  decrease: "降低 ↓",
  hold: "保持 →",
  change: "调整",
  uncertain: "方向未确定",
};

const DIRECTION_COLOR: Record<string, string> = {
  increase: "blue",
  decrease: "orange",
  hold: "default",
  change: "processing",
  uncertain: "warning",
};

const ROLE_LABELS: Record<string, string> = {
  primary_factor: "主要调整",
  secondary_factor: "次要调整",
  hold_constant_factor: "保持不变",
  uncertain_factor: "待定",
  contextual_factor: "背景因素",
  confounder: "混杂因素",
  mediator: "中介因素",
  risk_factor: "风险因素",
  applicability_factor: "适用性因素",
  // Feedback-round (history-mode) vocabulary.
  primary: "主要调整",
  secondary: "次要调整",
  hold_constant: "保持不变",
  uncertain: "待定",
};

const CERTAINTY_LABELS: Record<string, { label: string; color: string }> = {
  high: { label: "高确定性", color: "success" },
  medium: { label: "中等确定性", color: "processing" },
  low: { label: "低确定性", color: "warning" },
};

const CONFIDENCE_LABELS: Record<string, { label: string; color: string }> = {
  high: { label: "证据充分", color: "success" },
  medium: { label: "证据中等", color: "processing" },
  low: { label: "证据有限", color: "warning" },
};

const RISK_LEVEL_LABELS: Record<string, { label: string; color: string }> = {
  high: { label: "风险高", color: "error" },
  medium: { label: "风险中等", color: "warning" },
  low: { label: "风险低", color: "success" },
};

// History-mode (feedback-round) factor_assessments carry a flat `uncertainty`
// level instead of the structured confidence/risk objects.
const UNCERTAINTY_LABELS: Record<string, { label: string; color: string }> = {
  high: { label: "不确定性高", color: "warning" },
  medium: { label: "不确定性中等", color: "processing" },
  low: { label: "不确定性低", color: "success" },
};

const CONSTRAINT_STATUS_LABELS: Record<string, string> = {
  not_applicable: "",
  satisfied: "硬约束满足",
  overrode_candidate: "约束覆盖候选方向",
  conflict: "⚠ 与约束冲突",
};

const CAUSAL_LABELS: Record<string, string> = {
  not_identified: "因果未确认",
  confounded: "存在混杂",
  provisional_association: "暂定关联",
  replicated_association: "重复关联",
  causal_support_limited: "因果支持有限",
};

const FACTOR_CLASS_LABELS: Record<string, string> = {
  controlled_parameter: "可控参数",
  context_factor: "背景因素",
  response_mediator: "响应中介",
  confounder: "混杂因素",
  risk_factor: "风险因素",
  applicability_factor: "适用性因素",
};

const FOCUS_TYPE_LABELS: Record<string, string> = {
  single_factor_probe: "单因素探查",
  interaction_disambiguation: "交互消歧",
  degraded_observation: "降级观察",
};

const ANALYSIS_STATUS_LABELS: Record<string, { label: string; color: string }> = {
  complete: { label: "分析完整", color: "success" },
  degraded: { label: "分析受限", color: "warning" },
  insufficient_for_direction: { label: "信息不足", color: "error" },
};

const HISTORY_MODE_LABELS: Record<string, string> = {
  R0: "初轮（无历史）",
  R1_plus: "迭代轮（有历史）",
  feedback: "反馈轮（基于实验诊断）",
};

const EXECUTION_BASIS_LABELS: Record<string, string> = {
  no_history: "无执行历史",
  actual_execution: "有实际执行",
  mixed_history_with_mismatch: "历史不匹配",
  recommendation_only_history: "仅推荐历史",
};

const PRIORITY_LABELS: Record<string, string> = {
  primary: "主要",
  secondary: "次要",
  hold: "保持",
  uncertain: "待定",
  context: "背景",
};

// S7 labels
const RECOMMENDATION_GATE_LABELS: Record<string, { label: string; color: string }> = {
  release: { label: "可进入推荐", color: "success" },
  conditional_release: { label: "条件放行", color: "processing" },
  defer: { label: "暂缓", color: "warning" },
  block: { label: "阻止", color: "error" },
};

const VALIDATION_STATUS_LABELS: Record<string, string> = {
  complete: "核验完整",
  degraded: "核验受限",
  partial: "部分完成",
};

const AVAILABILITY_LABELS: Record<string, string> = {
  available: "当前可用",
  unavailable: "当前不可用",
};

const FEASIBILITY_LABELS: Record<string, { label: string; color: string }> = {
  feasible: { label: "可执行", color: "success" },
  feasible_with_conditions: { label: "条件可执行", color: "processing" },
  insufficient_information: { label: "信息不足", color: "warning" },
  infeasible: { label: "不可执行", color: "error" },
};

const DECISION_LABELS: Record<string, string> = {
  release: "放行",
  conditional_release: "条件放行",
  defer: "暂缓",
  block: "阻止",
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
function paramName(key: string): string {
  return researchPresentationParameterName(key, key);
}

// ─── S6 sub-components ───────────────────────────────────────────────────────

/** One-line summary row for each of the 4 controlled parameters */
function ControlledParamSummary({ assessments }: { assessments: JsonObject[] }) {
  const CORE = ["power", "n2_flow", "ar_flow", "deposition_time"];
  const byFactor = new Map(assessments.map((a) => [text(a.factor), a]));
  const rows = CORE.map((key) => {
    const a = byFactor.get(key);
    const dir = a ? text(a.candidate_direction) : "";
    const role = a ? text(a.role) || text(a.priority_role) : "";
    const confidence = asObject(a?.confidence);
    const confLevel = confidence ? text(confidence.level) : "";
    const risk = asObject(a?.risk);
    const riskLevel = risk ? text(risk.level) : "";
    const uncertaintyLevel = a ? text(a.uncertainty) : "";
    return { key, dir, role, confLevel, riskLevel, uncertaintyLevel };
  });
  return (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
      {rows.map(({ key, dir, role, confLevel, riskLevel, uncertaintyLevel }) => {
        const dirEntry = DIRECTION_LABELS[dir];
        const color = DIRECTION_COLOR[dir] ?? "default";
        const conf = CONFIDENCE_LABELS[confLevel];
        const riskEntry = RISK_LEVEL_LABELS[riskLevel];
        const uncertaintyEntry = UNCERTAINTY_LABELS[uncertaintyLevel];
        return (
          <div
            key={key}
            className="rounded-lg border border-border bg-surface px-3 py-2"
            data-testid="pvd-controlled-param-card"
          >
            <div className="text-xs text-text-muted">{paramName(key)}</div>
            <div className="mt-1 flex flex-wrap items-center gap-1">
              {dirEntry ? (
                <Tag color={color} className="m-0 font-medium">
                  {dirEntry}
                </Tag>
              ) : (
                <Tag className="m-0">未确定</Tag>
              )}
              {role && ROLE_LABELS[role] ? (
                <span className="text-xs text-text-secondary">{ROLE_LABELS[role]}</span>
              ) : null}
            </div>
            <div className="mt-1 flex flex-wrap gap-1">
              {conf ? (
                <Tag color={conf.color} className="m-0 text-xs">
                  {conf.label}
                </Tag>
              ) : null}
              {riskEntry ? (
                <Tag color={riskEntry.color} className="m-0 text-xs">
                  {riskEntry.label}
                </Tag>
              ) : null}
              {uncertaintyEntry ? (
                <Tag color={uncertaintyEntry.color} className="m-0 text-xs">
                  {uncertaintyEntry.label}
                </Tag>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ExpectedEffectList({ effects }: { effects: unknown }) {
  const rows = asObjectArray(effects);
  if (rows.length === 0) return null;
  return (
    <ul className="m-0 grid gap-1 pl-4 text-sm text-text-secondary">
      {rows.map((effect, i) => {
        const response = text(effect.response);
        const certaintyKey = text(effect.certainty);
        const entry = CERTAINTY_LABELS[certaintyKey];
        const note = text(effect.note);
        return (
          <li key={i}>
            {response}
            {entry ? (
              <Tag color={entry.color} className="ml-1 m-0 text-xs">
                {entry.label}
              </Tag>
            ) : null}
            {note ? <span className="ml-1 text-text-muted">（{note}）</span> : null}
          </li>
        );
      })}
    </ul>
  );
}

function FactorAssessmentCard({ row }: { row: JsonObject }) {
  const factor = text(row.factor);
  const displayName = text(row.display_name);
  const label = displayName || paramName(factor);
  const dir = text(row.candidate_direction);
  const role = text(row.role) || text(row.priority_role);
  const factorClass = text(row.factor_class);
  const rationale = text(row.rationale);
  const isControlled =
    row.is_controlled_four_parameter === true ||
    row.is_recommendation_parameter === true;

  const confidence = asObject(row.confidence);
  const risk = asObject(row.risk);
  const uncertaintyLevel = text(row.uncertainty);
  const constraintStatus = text(row.constraint_status);
  const causal = text(row.causal_interpretation);
  const contradictions = stringList(row.contradictions);
  const confounders = stringList(row.confounders);
  const applicability = stringList(row.applicability_conditions);

  const dirEntry = dir ? DIRECTION_LABELS[dir] : null;
  const dirColor = dir ? DIRECTION_COLOR[dir] : "default";
  const confEntry = confidence ? CONFIDENCE_LABELS[text(confidence.level)] : null;
  const riskEntry = risk ? RISK_LEVEL_LABELS[text(risk.level)] : null;
  const uncertaintyEntry = UNCERTAINTY_LABELS[uncertaintyLevel];
  const constraintLabel =
    constraintStatus && constraintStatus !== "not_applicable"
      ? CONSTRAINT_STATUS_LABELS[constraintStatus]
      : "";

  return (
    <article
      className="grid gap-2 rounded-lg border border-border bg-surface px-4 py-3"
      data-testid="pvd-factor-assessment-card"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Typography.Text strong>{label}</Typography.Text>
        {!isControlled && factorClass ? (
          <Tag className="m-0 text-xs">{FACTOR_CLASS_LABELS[factorClass] ?? factorClass}</Tag>
        ) : null}
        {dirEntry ? (
          <Tag color={dirColor} className="m-0">
            {dirEntry}
          </Tag>
        ) : null}
        {role && ROLE_LABELS[role] ? (
          <Tag className="m-0">{ROLE_LABELS[role]}</Tag>
        ) : null}
        {confEntry ? (
          <Tag color={confEntry.color} className="m-0">
            {confEntry.label}
          </Tag>
        ) : null}
        {riskEntry ? (
          <Tag color={riskEntry.color} className="m-0">
            {riskEntry.label}
          </Tag>
        ) : null}
        {uncertaintyEntry ? (
          <Tag color={uncertaintyEntry.color} className="m-0">
            {uncertaintyEntry.label}
          </Tag>
        ) : null}
        {constraintLabel ? (
          <Tag color="error" className="m-0">
            {constraintLabel}
          </Tag>
        ) : null}
        {causal && causal !== "not_identified" ? (
          <span className="text-xs text-text-muted">{CAUSAL_LABELS[causal] ?? causal}</span>
        ) : null}
      </div>

      {rationale ? (
        <Typography.Paragraph className="!mb-0 text-sm leading-relaxed text-text-secondary">
          {rationale}
        </Typography.Paragraph>
      ) : null}

      <ExpectedEffectList effects={row.expected_effect} />

      {applicability.length > 0 ? (
        <div className="text-sm">
          <span className="text-text-muted">适用条件：</span>
          {applicability.join("；")}
        </div>
      ) : null}

      {contradictions.length > 0 ? (
        <div className="text-sm">
          <span className="text-text-muted">矛盾证据：</span>
          {contradictions.join("；")}
        </div>
      ) : null}

      {confounders.length > 0 ? (
        <div className="text-sm">
          <span className="text-text-muted">混杂因素：</span>
          {confounders.join("；")}
        </div>
      ) : null}

      {confidence?.basis ? (
        <div className="text-xs text-text-muted">
          证据基础：{text(confidence.basis)}
        </div>
      ) : null}
    </article>
  );
}

function NextFocusBanner({ focus }: { focus: JsonObject }) {
  const focusType = text(focus.focus_type);
  const primaryFactor = text(focus.primary_factor);
  const holdFactors = (focus.hold_factors as unknown[] | undefined) ?? [];
  const rationale = text(focus.rationale);
  const focusRefList = stringList(focus.factor_refs);
  const holdNames = holdFactors
    .filter((f): f is string => typeof f === "string")
    .map(paramName)
    .join("、");

  return (
    <section className="rounded-lg border border-accent-strong bg-surface-soft px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <Typography.Text strong>下一轮优先关注</Typography.Text>
        {focusType ? (
          <Tag color="blue" className="m-0">
            {FOCUS_TYPE_LABELS[focusType] ?? focusType}
          </Tag>
        ) : null}
        {primaryFactor ? (
          <Tag className="m-0">{paramName(primaryFactor)}</Tag>
        ) : null}
        {focusRefList.map((ref) => (
          <Tag key={ref} className="m-0">
            {paramName(ref)}
          </Tag>
        ))}
      </div>
      {rationale ? (
        <Typography.Paragraph className="!mb-0 mt-2 text-sm leading-relaxed text-text-secondary">
          {rationale}
        </Typography.Paragraph>
      ) : null}
      {holdNames ? (
        <div className="mt-1 text-xs text-text-muted">保持不变：{holdNames}</div>
      ) : null}
    </section>
  );
}

function PrioritizedList({ factors }: { factors: JsonObject[] }) {
  if (factors.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {factors.map((f, i) => {
        const key = text(f.factor);
        const priority = text(f.priority);
        const reason = text(f.reason);
        const label = PRIORITY_LABELS[priority] ?? priority;
        const color =
          priority === "primary"
            ? "blue"
            : priority === "secondary"
              ? "processing"
              : priority === "hold"
                ? "default"
                : "warning";
        return (
          <span
            key={i}
            className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-1 text-sm"
            title={reason}
          >
            <Tag color={color} className="m-0">
              {label}
            </Tag>
            {paramName(key)}
          </span>
        );
      })}
    </div>
  );
}

export function PvdCandidateDirectionPresentation({
  document,
}: {
  document: JsonObject;
}): ReactNode {
  const rawAssessments = document.factor_assessments;
  const allAssessments = asObjectArray(rawAssessments);
  // R0 schema marks the four controlled parameters with
  // is_controlled_four_parameter; feedback-round (history-mode) output marks
  // them with is_recommendation_parameter. Both mean "recommendation parameter".
  const controlled = allAssessments.filter(
    (a) =>
      a.is_controlled_four_parameter === true ||
      a.is_recommendation_parameter === true,
  );
  const contextual = allAssessments.filter(
    (a) =>
      a.is_controlled_four_parameter !== true &&
      a.is_recommendation_parameter !== true,
  );

  const hasMalformed =
    !Array.isArray(rawAssessments) || rawAssessments.length === 0;

  const statusKey = text(document.analysis_status);
  const statusEntry = ANALYSIS_STATUS_LABELS[statusKey];

  const analysisBasis = asObject(document.analysis_basis);
  const prioritizedFactors = asObjectArray(document.prioritized_factors);
  const factorsToHold = asObjectArray(document.factors_to_hold);
  const focus = asObject(document.recommended_next_experiment_focus);
  const hypotheses = asObjectArray(document.unresolved_hypotheses);
  const assumptions = stringList(document.assumptions);
  const limitations = stringList(document.limitations);

  return (
    <section data-testid="workflow-node-pvd-candidate-direction" className="grid gap-4">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-2">
        <Typography.Title level={4} className="!mb-0">
          工艺因素与候选方向
        </Typography.Title>
        {statusEntry ? (
          <Tag color={statusEntry.color} className="m-0">
            {statusEntry.label}
          </Tag>
        ) : null}
        {analysisBasis ? (
          <>
            {analysisBasis.history_mode ? (
              <Tag className="m-0">
                {HISTORY_MODE_LABELS[text(analysisBasis.history_mode)] ??
                  text(analysisBasis.history_mode)}
              </Tag>
            ) : null}
            {analysisBasis.execution_basis ? (
              <span className="text-xs text-text-muted">
                {EXECUTION_BASIS_LABELS[text(analysisBasis.execution_basis)] ??
                  text(analysisBasis.execution_basis)}
              </span>
            ) : null}
          </>
        ) : null}
      </div>

      {/* 4-parameter summary */}
      {controlled.length > 0 ? (
        <section className="grid gap-2">
          <Typography.Text strong>四参数候选方向一览</Typography.Text>
          <ControlledParamSummary assessments={allAssessments} />
        </section>
      ) : null}

      {/* Prioritized factor list */}
      {prioritizedFactors.length > 0 ? (
        <section className="grid gap-2">
          <Typography.Text strong>参数优先级</Typography.Text>
          <PrioritizedList factors={prioritizedFactors} />
        </section>
      ) : null}

      {/* Next experiment focus */}
      {focus ? <NextFocusBanner focus={focus} /> : null}

      {/* Controlled factor detail cards */}
      {hasMalformed && controlled.length === 0 ? (
        <Typography.Paragraph className="!mb-0 text-sm text-text-secondary">
          候选方向评估当前无法安全结构化展示，可查看技术详情。
        </Typography.Paragraph>
      ) : (
        controlled.length > 0 && (
          <section className="grid gap-2">
            <Typography.Text strong>可控参数详细评估</Typography.Text>
            <div className="grid gap-3">
              {controlled.map((row) => (
                <FactorAssessmentCard key={text(row.factor)} row={row} />
              ))}
            </div>
          </section>
        )
      )}

      {/* Contextual / background factors */}
      {contextual.length > 0 ? (
        <Collapse
          ghost
          items={[
            {
              key: "contextual",
              label: `背景与混杂因素（${contextual.length} 项）`,
              children: (
                <div className="grid gap-3">
                  {contextual.map((row, i) => (
                    <FactorAssessmentCard key={i} row={row} />
                  ))}
                </div>
              ),
            },
          ]}
        />
      ) : null}

      {/* Factors to hold */}
      {factorsToHold.length > 0 ? (
        <section className="grid gap-2">
          <Typography.Text strong>本轮保持不变的参数</Typography.Text>
          <div className="flex flex-wrap gap-2">
            {factorsToHold.map((f, i) => (
              <span
                key={i}
                className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-1 text-sm"
                title={text(f.reason)}
              >
                {paramName(text(f.factor))}
              </span>
            ))}
          </div>
        </section>
      ) : null}

      {/* Unresolved hypotheses */}
      {hypotheses.length > 0 ? (
        <section className="grid gap-1">
          <Typography.Text strong>未决假设</Typography.Text>
          <ul className="m-0 grid gap-1.5 pl-5 text-sm text-text-secondary">
            {hypotheses.map((h, i) => {
              const hypo = text(h.hypothesis);
              const discrim = text(h.discriminating_factor);
              return (
                <li key={i}>
                  {hypo}
                  {discrim ? (
                    <span className="ml-1 text-text-muted">
                      （判别因素：{paramName(discrim)}）
                    </span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {assumptions.length > 0 || limitations.length > 0 ? (
        <Collapse
          ghost
          items={[
            {
              key: "details",
              label: "假设与限制",
              children: (
                <div className="grid gap-4">
                  {assumptions.length > 0 ? (
                    <div>
                      <Typography.Text strong>假设</Typography.Text>
                      <ul className="m-0 mt-2 grid gap-1 pl-5 text-sm text-text-secondary">
                        {assumptions.map((a, i) => (
                          <li key={i}>{a}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  {limitations.length > 0 ? (
                    <div>
                      <Typography.Text strong>限制</Typography.Text>
                      <ul className="m-0 mt-2 grid gap-1 pl-5 text-sm text-text-secondary">
                        {limitations.map((l, i) => (
                          <li key={i}>{l}</li>
                        ))}
                      </ul>
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

// ─── S7 ──────────────────────────────────────────────────────────────────────

function DirectionAssessmentCard({ row }: { row: JsonObject }) {
  const factor = text(row.factor);
  const displayName = text(row.display_name);
  const label = displayName || paramName(factor);
  const dir = text(row.candidate_direction);
  const feasibility = text(row.feasibility);
  const decision = text(row.decision);
  const rationale = text(row.rationale);

  const feasEntry = FEASIBILITY_LABELS[feasibility];
  const dirEntry = dir ? DIRECTION_LABELS[dir] : null;
  const dirColor = dir ? DIRECTION_COLOR[dir] : "default";
  const decisionLabel = DECISION_LABELS[decision] ?? decision;

  const conditions = stringList(row.conditions);
  const conflicts = stringList(row.conflicts);
  const risks = stringList(row.risks);
  const missing = stringList(row.missing_information);

  return (
    <article
      className="grid gap-2 rounded-lg border border-border bg-surface px-4 py-3"
      data-testid="pvd-direction-assessment-card"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Typography.Text strong>{label}</Typography.Text>
        {dirEntry ? (
          <Tag color={dirColor} className="m-0">
            {dirEntry}
          </Tag>
        ) : null}
        {feasEntry ? (
          <Tag color={feasEntry.color} className="m-0">
            {feasEntry.label}
          </Tag>
        ) : null}
        {decisionLabel ? <Tag className="m-0">{decisionLabel}</Tag> : null}
      </div>
      {rationale ? (
        <Typography.Paragraph className="!mb-0 text-sm leading-relaxed text-text-secondary">
          {rationale}
        </Typography.Paragraph>
      ) : null}
      {conditions.length > 0 ? (
        <div className="text-sm">
          <span className="text-text-muted">执行条件：</span>
          {conditions.join("；")}
        </div>
      ) : null}
      {conflicts.length > 0 ? (
        <div className="text-sm">
          <span className="text-text-muted">冲突：</span>
          {conflicts.join("；")}
        </div>
      ) : null}
      {risks.length > 0 ? (
        <div className="text-sm">
          <span className="text-text-muted">风险：</span>
          {risks.join("；")}
        </div>
      ) : null}
      {missing.length > 0 ? (
        <div className="text-sm">
          <span className="text-text-muted">缺失信息：</span>
          {missing.join("；")}
        </div>
      ) : null}
    </article>
  );
}

export function PvdFeasibilityValidationPresentation({
  document,
}: {
  document: JsonObject;
}): ReactNode {
  const rawAssessments = document.direction_assessments;
  const assessments = asObjectArray(rawAssessments);

  const hasMalformed =
    !Array.isArray(rawAssessments) || rawAssessments.length === 0;

  const gateKey = text(document.recommendation_gate);
  const gateEntry = RECOMMENDATION_GATE_LABELS[gateKey];
  const validationStatus = text(document.validation_status);
  const availability = text(document.availability);

  const sharedConditions = stringList(document.shared_conditions);
  const sharedRisks = stringList(document.shared_risks);
  const importantUnknowns = stringList(document.important_unknowns);
  const limitations = stringList(document.limitations);

  const cross = asObject(document.cross_parameter_assessment);

  return (
    <section
      data-testid="workflow-node-pvd-feasibility-validation"
      className="grid gap-4"
    >
      {/* Header with gate decision as prominent badge */}
      <div className="grid gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Typography.Title level={4} className="!mb-0">
            参数与实验方案可行性核验
          </Typography.Title>
          {gateEntry ? (
            <Tag color={gateEntry.color} className="m-0 text-base">
              {gateEntry.label}
            </Tag>
          ) : (
            <Typography.Text type="secondary" className="text-sm">
              总体推荐准入状态暂无法确认
            </Typography.Text>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {validationStatus ? (
            <Tag className="m-0">{VALIDATION_STATUS_LABELS[validationStatus] ?? validationStatus}</Tag>
          ) : null}
          {availability ? (
            <Tag className="m-0">{AVAILABILITY_LABELS[availability] ?? availability}</Tag>
          ) : null}
        </div>
      </div>

      {/* Per-direction cards */}
      {hasMalformed && assessments.length === 0 ? (
        <Typography.Paragraph className="!mb-0 text-sm text-text-secondary">
          逐方向核验当前无法安全结构化展示，可查看技术详情。
        </Typography.Paragraph>
      ) : (
        <div className="grid gap-3">
          {assessments.map((row, i) => (
            <DirectionAssessmentCard key={i} row={row} />
          ))}
        </div>
      )}

      {/* Shared conditions / risks / unknowns */}
      {sharedConditions.length > 0 ? (
        <section className="grid gap-1">
          <Typography.Text strong>共用执行条件</Typography.Text>
          <ul className="m-0 grid gap-1 pl-5 text-sm text-text-secondary">
            {sharedConditions.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {sharedRisks.length > 0 ? (
        <section className="grid gap-1">
          <Typography.Text strong>共用风险</Typography.Text>
          <ul className="m-0 grid gap-1 pl-5 text-sm text-text-secondary">
            {sharedRisks.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {importantUnknowns.length > 0 ? (
        <section className="grid gap-1">
          <Typography.Text strong>重要未知</Typography.Text>
          <ul className="m-0 grid gap-1 pl-5 text-sm text-text-secondary">
            {importantUnknowns.map((u, i) => (
              <li key={i}>{u}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {/* Cross-parameter assessment and limitations in technical details */}
      <Collapse
        ghost
        items={[
          {
            key: "details",
            label: "跨参数核验与完整限制",
            children: (
              <div className="grid gap-4">
                {cross ? (
                  <div>
                    <Typography.Text strong>跨参数核验</Typography.Text>
                    <div className="mt-2 grid gap-1 text-sm text-text-secondary">
                      {Object.entries(cross)
                        .filter(([, v]) => v !== null && v !== undefined)
                        .map(([k, v]) => (
                          <div key={k}>
                            <span className="text-text-muted">{k}：</span>
                            {typeof v === "string"
                              ? v
                              : Array.isArray(v)
                                ? (v as string[]).join("；")
                                : JSON.stringify(v)}
                          </div>
                        ))}
                    </div>
                  </div>
                ) : null}
                {limitations.length > 0 ? (
                  <div>
                    <Typography.Text strong>完整限制</Typography.Text>
                    <ul className="m-0 mt-2 grid gap-1 pl-5 text-sm text-text-secondary">
                      {limitations.map((l, i) => (
                        <li key={i}>{l}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
            ),
          },
        ]}
      />
    </section>
  );
}
