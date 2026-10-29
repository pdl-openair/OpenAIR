import {
  OT_REASONING_LABELS_ZH,
  OT_REASONING_DETAILS_ZH,
  OT_ACTION_LABELS_ZH,
  OT_ACTION_DETAILS_ZH,
} from "@openair/shared";
import { Tag, Typography } from "antd";
import type { ReactNode } from "react";
import type { WorkbenchArtifact } from "../../model/workbenchTask";
import {
  CsActualMetricRecordsPresentation,
  CsReproductionComparisonJudgmentPresentation,
} from "./CsReproductionComparisonPresentation";
import {
  PvdCandidateDirectionPresentation,
  PvdFeasibilityValidationPresentation,
} from "./PvdDirectionFeasibilityPresentation";
import { PvdFeedbackAnalysisPresentation } from "./PvdFeedbackAnalysisPresentation";

import {
  hasScientificResultContent,
  ScientificResultDisplayValue,
  ScientificResultSourceDetails,
} from "./scientificResultDisplayValue";

type JsonObject = Record<string, unknown>;

export type WorkflowNodeResultPresentationKind =
  | "cs_actual_metric_records"
  | "cs_reproduction_comparison_judgment"
  | "cs_reproduction_conclusion"
  | "ot_current_research_conclusion"
  | "ot_key_unknowns"
  | "ot_next_research_recommendation"
  | "pvd_candidate_direction"
  | "pvd_feasibility_validation"
  | "pvd_feedback_analysis"
  | "pvd_round_recommendation";

// Keys match BOTH the durable artifact types (candidate_analysis, … — what the
// runtime actually mints) and the substage definition names (…experiment_analysis,
// …feasibility_validation — used by shape fixtures). Production artifacts do not
// carry a `substage` field, so the artifact-type entries are the ones that fire.
const ARTIFACT_KIND: Record<string, WorkflowNodeResultPresentationKind> = {
  actual_metric_records: "cs_actual_metric_records",
  reproduction_comparison_judgment: "cs_reproduction_comparison_judgment",
  reproduction_conclusion: "cs_reproduction_conclusion",
  current_research_conclusion: "ot_current_research_conclusion",
  key_unknowns_and_candidate_explanations: "ot_key_unknowns",
  next_research_recommendation: "ot_next_research_recommendation",
  candidate_analysis: "pvd_candidate_direction",
  process_factor_candidate_experiment_analysis: "pvd_candidate_direction",
  feasibility_validation: "pvd_feasibility_validation",
  parameter_experiment_plan_feasibility_validation:
    "pvd_feasibility_validation",
  feedback_analysis: "pvd_feedback_analysis",
  experimental_feedback_round_analysis: "pvd_feedback_analysis",
  round_experiment_plan_parameter_recommendation: "pvd_round_recommendation",
};

const SUBSTAGE_KIND: Record<string, WorkflowNodeResultPresentationKind> = {
  reproduction_conclusion_recommendation: "cs_reproduction_conclusion",
  current_research_conclusion: "ot_current_research_conclusion",
  key_unknowns_candidate_explanations: "ot_key_unknowns",
  next_research_recommendation: "ot_next_research_recommendation",
  process_factor_candidate_experiment_analysis: "pvd_candidate_direction",
  parameter_experiment_plan_feasibility_validation:
    "pvd_feasibility_validation",
  round_experiment_plan_parameter_recommendation: "pvd_round_recommendation",
};

export const WORKFLOW_NODE_RESULT_PRESENTATION_ORDER: readonly WorkflowNodeResultPresentationKind[] =
  [
    "cs_reproduction_conclusion",
    "ot_current_research_conclusion",
    "ot_key_unknowns",
    "ot_next_research_recommendation",
    "pvd_round_recommendation",
  ];

function exactIdentity(value?: string | null): string {
  return (value ?? "").trim().toLowerCase();
}

export function workflowNodeResultPresentationKind(
  artifact: WorkbenchArtifact,
): WorkflowNodeResultPresentationKind | null {
  const artifactType = exactIdentity(artifact.artifactType ?? artifact.type);
  const artifactKind = ARTIFACT_KIND[artifactType] ?? null;
  const substageKind = SUBSTAGE_KIND[exactIdentity(artifact.substage)] ?? null;
  if (artifactKind && substageKind && artifactKind !== substageKind)
    return null;
  return artifactKind ?? substageKind;
}

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

function scalar(value: unknown): string | null {
  if (typeof value === "string") return value.trim() || null;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  return null;
}

function directTextList(value: unknown): unknown[] {
  return Array.isArray(value)
    ? value.filter(hasScientificResultContent)
    : hasScientificResultContent(value)
      ? [value]
      : [];
}

function TextList({ values }: { values: unknown[] }) {
  if (values.length === 0) return null;
  return (
    <ul className="m-0 grid gap-1.5 pl-5 text-sm leading-relaxed text-text-secondary">
      {values.map((value, index) => (
        <li key={index}>
          <ScientificResultDisplayValue value={value} />
        </li>
      ))}
    </ul>
  );
}

function ResultSection({
  title,
  values,
}: {
  title: string;
  values: unknown[];
}) {
  if (values.length === 0) return null;
  return (
    <section className="rounded-lg border border-border bg-surface-soft p-4">
      <h3 className="m-0 text-base font-semibold text-text">{title}</h3>
      <div className="mt-2">
        <TextList values={values} />
      </div>
    </section>
  );
}

function StatusRow({
  values,
}: {
  values: Array<{ label: string; value: string | null }>;
}) {
  const visible = values.filter((row) => row.value);
  if (visible.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {visible.map((row) => (
        <Tag key={row.label} className="m-0">
          {row.label}：{row.value}
        </Tag>
      ))}
    </div>
  );
}

function knownStatus(
  value: unknown,
  labels: Record<string, string>,
): string | null {
  const key = text(value);
  return key ? (labels[key] ?? null) : null;
}

const CS_EXECUTION_LABELS: Record<string, string> = {
  succeeded: "已执行",
  partial: "部分执行",
  failed: "执行未成功",
};

const CS_EVIDENCE_LABELS: Record<string, string> = {
  sufficient: "证据充分",
  insufficient: "证据不足",
  partial: "证据仅覆盖部分范围",
};

const CS_VERDICT_LABELS: Record<string, string> = {
  reproduced: "复现达标",
  not_reproduced: "复现未达标",
  indeterminate: "当前无法判定",
};

function CsReproductionConclusion({ document }: { document: JsonObject }) {
  const answer = directTextList(document.answer);
  const scope = directTextList(document.scope);
  const nextSteps = directTextList(document.next_steps);

  return (
    <section data-testid="workflow-node-cs-conclusion" className="grid gap-4">
      <div>
        <Typography.Title level={4} className="!mb-2">
          复现结论与下一步建议
        </Typography.Title>
        <TextList values={answer} />
      </div>
      <StatusRow
        values={[
          {
            label: "实际执行",
            value: knownStatus(document.execution_status, CS_EXECUTION_LABELS),
          },
          {
            label: "证据",
            value: knownStatus(
              document.evidence_sufficiency,
              CS_EVIDENCE_LABELS,
            ),
          },
          {
            label: "复现结论",
            value: knownStatus(document.verdict, CS_VERDICT_LABELS),
          },
        ]}
      />
      <ResultSection title="复现范围" values={scope} />
      <ResultSection
        title="限制"
        values={directTextList(document.limitations)}
      />
      <ResultSection title="未知" values={directTextList(document.unknowns)} />
      <ResultSection title="下一步" values={nextSteps} />
    </section>
  );
}

function ScientificDetail({ title, value }: { title: string; value: unknown }) {
  if (!hasScientificResultContent(value)) return null;
  return (
    <details className="rounded-lg border border-border p-4">
      <summary className="cursor-pointer text-base font-medium text-text">
        {title}
      </summary>
      <div className="mt-3 text-sm leading-relaxed text-text-secondary">
        <ScientificResultDisplayValue value={value} />
      </div>
    </details>
  );
}

function OtStructuredText({
  value,
  primaryKeys,
  labels,
  detailLabels,
}: {
  value: unknown;
  primaryKeys: string[];
  labels: Record<string, string>;
  detailLabels: Record<string, string>;
}) {
  const object = asObject(value);
  const primaryKey =
    object &&
    primaryKeys.find((key) => hasScientificResultContent(object[key]));
  if (!object || !primaryKey)
    return <TextList values={directTextList(value)} />;
  const additional = Object.fromEntries(
    Object.entries(object).filter(
      ([key]) => key !== primaryKey && !labels[key] && !detailLabels[key],
    ),
  );
  return (
    <div className="grid gap-4" data-testid="ot-reasoning-hierarchy">
      <div className="text-lg font-medium leading-relaxed text-text">
        <ScientificResultDisplayValue value={object[primaryKey]} />
      </div>
      {Object.entries(labels).map(([key, title]) => (
        <ResultSection
          key={key}
          title={title}
          values={directTextList(object[key])}
        />
      ))}
      {hasScientificResultContent(additional) ? (
        <ScientificResultDisplayValue value={additional} />
      ) : null}
      {Object.entries(detailLabels).map(([key, title]) => (
        <ScientificDetail key={key} title={title} value={object[key]} />
      ))}
    </div>
  );
}

function OtDecisionResult({
  document,
  title,
  testId,
  uncertaintyTitle = "限制与不确定性",
}: {
  document: JsonObject;
  title: string;
  testId: string;
  uncertaintyTitle?: string;
}) {
  return (
    <section data-testid={testId} className="grid gap-4">
      <div className="rounded-lg border-l-4 border-accent-strong bg-surface-soft p-4">
        <Typography.Title level={4} className="!mb-3 !text-xl">
          {title}
        </Typography.Title>
        <div className="text-base font-medium leading-relaxed text-text">
          <OtStructuredText
            value={document.reasoning_summary}
            primaryKeys={[
              "direct_answer",
              "current_judgment",
              "current_conclusion",
            ]}
            labels={OT_REASONING_LABELS_ZH}
            detailLabels={OT_REASONING_DETAILS_ZH}
          />
        </div>
      </div>
      <ResultSection
        title={uncertaintyTitle}
        values={directTextList(document.uncertainty)}
      />
      {hasScientificResultContent(document.next_action) ? (
        <section className="rounded-lg border border-border bg-surface-soft p-4">
          <h3 className="m-0 mb-3 text-base font-semibold text-text">下一步</h3>
          <OtStructuredText
            value={document.next_action}
            primaryKeys={["priority_study"]}
            labels={OT_ACTION_LABELS_ZH}
            detailLabels={OT_ACTION_DETAILS_ZH}
          />
        </section>
      ) : null}
      <div className="grid gap-3 md:grid-cols-2">
        <ResultSection
          title="适用范围"
          values={directTextList(document.scope)}
        />
        <ResultSection
          title="成立条件"
          values={directTextList(document.assumptions)}
        />
      </div>
    </section>
  );
}

function publicParameter(
  row: JsonObject,
): { label: string; value: string; unit: string } | null {
  const parameterKey = text(row.parameter_key);
  const displayName = text(row.display_name);
  if (!displayName || displayName === parameterKey) return null;
  const value = scalar(row.recommended_value);
  if (value === null) return null;
  return { label: displayName, value, unit: text(row.unit) };
}

function PvdParameters({ experiment }: { experiment: JsonObject }) {
  const parameters = asObjectArray(experiment.parameters)
    .map(publicParameter)
    .filter((row): row is { label: string; value: string; unit: string } =>
      Boolean(row),
    );
  if (parameters.length === 0) return null;
  return (
    <dl
      className="m-0 grid gap-2 sm:grid-cols-2"
      data-testid="workflow-node-pvd-parameters"
    >
      {parameters.map((parameter) => (
        <div
          key={`${parameter.label}-${parameter.value}`}
          className="rounded-md bg-surface-soft px-3 py-2"
        >
          <dt className="text-xs text-text-muted">{parameter.label}</dt>
          <dd className="m-0 mt-1 text-sm font-medium text-text">
            {parameter.value}
            {parameter.unit ? ` ${parameter.unit}` : ""}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function PvdExperiment({ experiment }: { experiment: JsonObject }) {
  const priority = scalar(experiment.priority);
  return (
    <article className="grid gap-3 rounded-lg border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Typography.Text strong>实验方案</Typography.Text>
        {priority ? <Tag className="m-0">优先级 {priority}</Tag> : null}
      </div>
      <PvdParameters experiment={experiment} />
      <ResultSection
        title="设计依据"
        values={directTextList(experiment.design_rationale)}
      />
      <ResultSection
        title="验证目标"
        values={directTextList(experiment.validation_goal)}
      />
      <ResultSection
        title="需保持的条件"
        values={directTextList(experiment.feasibility_conditions)}
      />
      <ResultSection
        title="风险"
        values={directTextList(experiment.recommendation_risks)}
      />
      <ResultSection
        title="改变当前方向的观察"
        values={directTextList(experiment.failure_signal)}
      />
    </article>
  );
}

const PVD_STATUS_LABELS: Record<string, string> = {
  recommendation: "当前正式推荐",
  no_recommendation: "当前没有正式推荐",
};

function PvdRoundRecommendation({ document }: { document: JsonObject }) {
  const experiments = asObjectArray(document.experiments);
  const status = knownStatus(document.decision_status, PVD_STATUS_LABELS);
  const portfolioNote = text(document.portfolio_note);

  return (
    <section
      data-testid="workflow-node-pvd-recommendation"
      className="grid gap-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Typography.Title level={4} className="!mb-0">
          本轮实验方案与参数推荐
        </Typography.Title>
        {status ? <Tag className="m-0">{status}</Tag> : null}
      </div>
      {portfolioNote ? (
        <Typography.Paragraph className="!mb-0">
          {portfolioNote}
        </Typography.Paragraph>
      ) : null}
      {experiments.length > 0 ? (
        <div className="grid gap-3">
          {experiments.map((experiment, index) => (
            <PvdExperiment key={index} experiment={experiment} />
          ))}
        </div>
      ) : null}
      <ResultSection
        title="共同条件"
        values={directTextList(document.shared_conditions)}
      />
      <ResultSection
        title="风险"
        values={directTextList(document.shared_risks)}
      />
      <ResultSection
        title="重要未知"
        values={directTextList(document.important_unknowns)}
      />
      <ResultSection
        title="改变方向的信号"
        values={directTextList(document.direction_change_signals)}
      />
      <ResultSection
        title="下一步"
        values={directTextList(document.next_step)}
      />
    </section>
  );
}

function WorkflowNodeResultBody({
  kind,
  document,
}: {
  kind: WorkflowNodeResultPresentationKind;
  document: JsonObject;
}): ReactNode {
  if (kind === "cs_actual_metric_records")
    return <CsActualMetricRecordsPresentation document={document} />;
  if (kind === "cs_reproduction_comparison_judgment")
    return <CsReproductionComparisonJudgmentPresentation document={document} />;
  if (kind === "cs_reproduction_conclusion")
    return <CsReproductionConclusion document={document} />;
  if (kind === "ot_current_research_conclusion")
    return (
      <OtDecisionResult
        document={document}
        title="当前研究结论"
        testId="workflow-node-ot-current-conclusion"
      />
    );
  if (kind === "ot_key_unknowns")
    return (
      <OtDecisionResult
        document={document}
        title="关键未知与候选解释"
        testId="workflow-node-ot-key-unknowns"
        uncertaintyTitle="仍需区分的未知"
      />
    );
  if (kind === "ot_next_research_recommendation")
    return (
      <OtDecisionResult
        document={document}
        title="下一步研究建议"
        testId="workflow-node-ot-next-recommendation"
      />
    );
  if (kind === "pvd_candidate_direction")
    return <PvdCandidateDirectionPresentation document={document} />;
  if (kind === "pvd_feasibility_validation")
    return <PvdFeasibilityValidationPresentation document={document} />;
  if (kind === "pvd_feedback_analysis")
    return <PvdFeedbackAnalysisPresentation document={document} />;
  return <PvdRoundRecommendation document={document} />;
}

export function WorkflowNodeResultPresentation(props: {
  kind: WorkflowNodeResultPresentationKind;
  document: JsonObject;
}): ReactNode {
  return (
    <>
      <WorkflowNodeResultBody {...props} />
      <ScientificResultSourceDetails document={props.document} />
    </>
  );
}
