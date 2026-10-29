import { Table, Tag, Typography } from "antd";
import type { ReactNode } from "react";
import { researchPresentationParameterName } from "@openair/shared";
import { ScientificResultSourceDetails } from "./scientificResultDisplayValue";

// S2 — pvd_evidence_retrieval_design / pvd_evidence_schema_design
// Two sibling skills under definition pvd:research_retrieval_experiment_design.
// evidence_schema carries the evidence_targets table (what to look for);
// retrieval_design carries the query_variants (how to look). Both share a
// coverage_target. Projected directly from canonical artifact, no LLM.

type JsonObject = Record<string, unknown>;

const SEARCH_INTENT_LABELS: Record<string, string> = {
  core_topic: "核心主题",
  constraint_focused: "约束聚焦",
  mechanism_focused: "机制聚焦",
  method_or_strategy: "方法/策略",
  metric_or_performance: "指标/性能",
  adjacent_but_relevant: "相邻相关",
  noise_exclusion: "噪声排除",
};
const RETRIEVAL_MODE_LABELS: Record<string, string> = {
  core: "核心检索",
  core_plus_supplemental: "核心 + 补充",
};
const REQUIREDNESS_LABELS: Record<string, string> = {
  required: "必需",
  preferred: "优先",
  optional: "可选",
};
const GAP_TAXONOMY_LABELS: Record<string, string> = {
  missing_mechanism: "缺失机制",
  missing_condition: "缺失条件",
  missing_metric: "缺失指标",
  missing_baseline: "缺失基线",
  conflict: "冲突",
  not_comparable: "不可比",
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
function paramName(key: string): string {
  return researchPresentationParameterName(key, key);
}
function paramNames(keys: unknown): string {
  return (Array.isArray(keys) ? keys : [])
    .filter((k): k is string => typeof k === "string")
    .map(paramName)
    .join("、");
}

function CoverageTarget({ coverage }: { coverage: unknown }) {
  const c = asObject(coverage);
  if (!c) return null;
  const formal = paramNames(c.formal_parameter_keys);
  const pending = paramNames(c.pending_parameter_keys);
  const all = paramNames(c.all_parameter_keys);
  if (!formal && !pending && !all) return null;
  return (
    <div className="rounded-lg border border-border bg-surface-soft px-4 py-3">
      <Typography.Text strong>参数覆盖</Typography.Text>
      <div className="mt-2 grid gap-1 text-sm">
        {formal ? <div><span className="text-text-muted">已纳入形式化参数：</span>{formal}</div> : null}
        {pending ? <div><span className="text-text-muted">待定参数：</span>{pending}</div> : null}
        {all ? <div><span className="text-text-muted">全部参数：</span>{all}</div> : null}
      </div>
    </div>
  );
}

function EvidenceTargetsTable({ targets }: { targets: unknown }) {
  const rows = asObjectArray(targets);
  if (rows.length === 0) return null;
  return (
    <section data-testid="pvd-evidence-schema-targets">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <Typography.Text strong>证据目标</Typography.Text>
        <Typography.Text type="secondary" className="text-sm">
          {rows.length} 个目标
        </Typography.Text>
      </div>
      <Table<JsonObject>
        size="small"
        rowKey={(row) => text(row.target_id) || String(rows.indexOf(row))}
        dataSource={rows}
        pagination={rows.length > 8 ? { pageSize: 8, showSizeChanger: false } : false}
        columns={[
          {
            title: "编号",
            key: "target_id",
            width: 90,
            render: (_, row) => text(row.target_id),
          },
          {
            title: "目标类型",
            key: "target_type",
            width: 130,
            render: (_, row) => text(row.target_type) || "—",
          },
          {
            title: "科研角色",
            key: "scientific_role",
            width: 130,
            render: (_, row) => text(row.scientific_role) || "—",
          },
          {
            title: "说明",
            key: "description",
            render: (_, row) => (
              <Typography.Text className="text-sm">
                {text(row.description) || "—"}
              </Typography.Text>
            ),
          },
          {
            title: "必要性",
            key: "requiredness",
            width: 80,
            render: (_, row) => {
              const label =
                (REQUIREDNESS_LABELS as Record<string, string>)[text(row.requiredness)] ?? null;
              return label ? <Tag className="m-0">{label}</Tag> : "—";
            },
          },
          {
            title: "覆盖参数",
            key: "covered_parameter_keys",
            render: (_, row) => paramNames(row.covered_parameter_keys) || "—",
          },
        ]}
      />
    </section>
  );
}

function GapTaxonomy({ gaps }: { gaps: unknown }) {
  const rows = (Array.isArray(gaps) ? gaps : [])
    .filter((g): g is string => typeof g === "string")
    .map((g) => GAP_TAXONOMY_LABELS[g] ?? g)
    .filter(Boolean);
  if (rows.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2">
      <Typography.Text type="secondary" className="text-sm">
        空缺分类：
      </Typography.Text>
      {rows.map((g, i) => (
        <Tag key={`${g}-${i}`} className="m-0">{g}</Tag>
      ))}
    </div>
  );
}

export function PvdEvidenceSchemaPresentation({
  document,
}: {
  document: JsonObject;
}): ReactNode {
  const schema = asObject(document.evidence_schema);
  if (!schema) return null;
  const targets = schema.evidence_targets;
  const gapTaxonomy = schema.gap_taxonomy;
  const uncertaintyRules = (schema.uncertainty_rules as unknown[] | undefined) ?? [];
  const designWarnings = (document.design_warnings as unknown[] | undefined) ?? [];
  const rules = uncertaintyRules
    .filter((r): r is string => typeof r === "string")
    .filter(Boolean);
  const warnings = designWarnings
    .filter((w): w is string => typeof w === "string")
    .filter(Boolean);

  return (
    <section data-testid="pvd-evidence-schema-view" className="grid gap-4">
      <Typography.Title level={4} className="!mb-0">
        工艺证据需求与覆盖设计
      </Typography.Title>
      <EvidenceTargetsTable targets={targets} />
      <CoverageTarget coverage={document.coverage_target} />
      <GapTaxonomy gaps={gapTaxonomy} />
      {rules.length > 0 ? (
        <section className="grid gap-2">
          <Typography.Text strong>不确定性规则</Typography.Text>
          <ul className="m-0 grid gap-1.5 pl-5 text-sm text-text-secondary">
            {rules.map((r, i) => (
              <li key={`rule-${i}`}>{r}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {warnings.length > 0 ? (
        <section className="grid gap-2">
          <Typography.Text type="warning" strong>
            设计告警
          </Typography.Text>
          <ul className="m-0 grid gap-1.5 pl-5 text-sm">
            {warnings.map((w, i) => (
              <li key={`warn-${i}`}>{w}</li>
            ))}
          </ul>
        </section>
      ) : null}
      <ScientificResultSourceDetails document={document} />
    </section>
  );
}

function QueryVariantRow({ row }: { row: JsonObject }) {
  const intent =
    (SEARCH_INTENT_LABELS as Record<string, string>)[text(row.search_intent)] ?? text(row.search_intent);
  const query = text(row.query);
  const rationale = text(row.rationale);
  const covered = paramNames(row.covered_parameter_keys);
  const priority = row.priority;
  return (
    <article className="grid gap-1 rounded-md border border-border bg-surface px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <Typography.Text strong className="text-sm">
          {text(row.query_id)}
        </Typography.Text>
        {intent ? <Tag className="m-0">{intent}</Tag> : null}
        {typeof priority === "number" ? (
          <Tag className="m-0">优先级 {priority}</Tag>
        ) : null}
      </div>
      <div className="text-sm">{query}</div>
      {covered ? (
        <div className="text-xs text-text-muted">覆盖参数：{covered}</div>
      ) : null}
      {rationale ? (
        <div className="text-xs text-text-muted">{rationale}</div>
      ) : null}
    </article>
  );
}

export function PvdRetrievalDesignPresentation({
  document,
}: {
  document: JsonObject;
}): ReactNode {
  const design = asObject(document.retrieval_design);
  if (!design) return null;
  const variants = asObjectArray(design.query_variants);
  const mode =
    (RETRIEVAL_MODE_LABELS as Record<string, string>)[text(design.mode)] ?? text(design.mode);
  const rationale = text(design.design_rationale);
  const pendingIdentities = (document.pending_parameter_evidence_identities as unknown[] | undefined) ?? [];
  const pending = pendingIdentities
    .filter((p): p is string => typeof p === "string")
    .map(paramName)
    .filter(Boolean);

  return (
    <section data-testid="pvd-retrieval-design-view" className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Typography.Title level={4} className="!mb-0">
          工艺证据检索计划
        </Typography.Title>
        {mode ? <Tag className="m-0">{mode}</Tag> : null}
      </div>
      {rationale ? (
        <Typography.Paragraph className="!mb-0 text-sm text-text-secondary">
          {rationale}
        </Typography.Paragraph>
      ) : null}
      {variants.length > 0 ? (
        <section className="grid gap-2">
          <Typography.Text strong>查询变体 · {variants.length} 条</Typography.Text>
          <div className="grid gap-2">
            {variants.map((variant, i) => (
              <QueryVariantRow key={i} row={variant} />
            ))}
          </div>
        </section>
      ) : null}
      <CoverageTarget coverage={document.coverage_target} />
      {pending.length > 0 ? (
        <div className="text-sm">
          <span className="text-text-muted">待补证据参数：</span>
          {pending.join("、")}
        </div>
      ) : null}
      <ScientificResultSourceDetails document={document} />
    </section>
  );
}
