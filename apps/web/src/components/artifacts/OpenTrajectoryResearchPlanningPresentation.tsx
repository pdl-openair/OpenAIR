import { Collapse, Descriptions, Tag, Typography } from "antd";
import type { CollapseProps } from "antd";
import type { ReactNode } from "react";

type JsonObject = Record<string, unknown>;

export type OpenTrajectoryResearchPlanningKind =
  "problem_framing" | "retrieval_strategy_design";

function asObject(value: unknown): JsonObject | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}

function asObjectArray(value: unknown): JsonObject[] {
  return Array.isArray(value)
    ? value.map(asObject).filter((item): item is JsonObject => Boolean(item))
    : [];
}

function scalar(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return "";
}

function textList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map(scalar).filter((item) => Boolean(item))
    : [];
}

function uniqueText(values: string[]): string[] {
  const seen = new Set<string>();
  return values.filter((value) => {
    const key = value.trim().toLocaleLowerCase();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function normalizedFact(value: string): string {
  return value
    .toLocaleLowerCase()
    .replace(/未提供|未知|待确认|仍需确认|具体/g, "")
    .replace(/[\s，。；、：:（）()\-_]/g, "");
}

function describesSameFact(left: string, right: string): boolean {
  const leftKey = normalizedFact(left);
  const rightKey = normalizedFact(right);
  return Boolean(
    leftKey &&
    rightKey &&
    (leftKey === rightKey ||
      leftKey.includes(rightKey) ||
      rightKey.includes(leftKey)),
  );
}

function uniqueFacts(values: string[]): string[] {
  const result: string[] = [];
  for (const value of values) {
    if (!value || result.some((item) => describesSameFact(item, value))) {
      continue;
    }
    result.push(value);
  }
  return result;
}

function displayValue(value: unknown): string {
  if (value === null) return "未提供";
  return scalar(value);
}

function booleanLabel(value: unknown): string {
  if (value === true) return "是";
  if (value === false) return "否";
  return scalar(value);
}

function TextTags({ values }: { values: string[] }) {
  if (values.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {values.map((value, index) => (
        <Tag key={`${value}-${index}`} className="!m-0 whitespace-normal">
          {value}
        </Tag>
      ))}
    </div>
  );
}

function CompactTerms({
  values,
  limit = 3,
}: {
  values: string[];
  limit?: number;
}) {
  const terms = uniqueText(values);
  if (terms.length === 0) return null;
  const visible = terms.slice(0, limit);
  const remaining = terms.length - visible.length;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {visible.map((value) => (
        <Tag key={value} className="!m-0 whitespace-normal">
          {value}
        </Tag>
      ))}
      {remaining > 0 ? (
        <Typography.Text type="secondary" className="text-xs">
          更多 {remaining} 项
        </Typography.Text>
      ) : null}
    </div>
  );
}

function InlineValues({ values }: { values: string[] }) {
  const items = uniqueText(values);
  return items.length > 0 ? (
    <span className="break-words leading-6">{items.join("、")}</span>
  ) : null;
}

function BulletList({ values }: { values: string[] }) {
  if (values.length === 0) return null;
  return (
    <ul className="m-0 space-y-1.5 pl-5">
      {values.map((value, index) => (
        <li key={`${value}-${index}`} className="leading-6 text-text-secondary">
          {value}
        </li>
      ))}
    </ul>
  );
}

function constraintStateLabel(value: unknown): string {
  const labels: Record<string, string> = {
    confirmed: "已确认",
    unknown: "信息未知",
    unrestricted: "未设限制",
    ambiguous: "存在歧义",
    conflicting: "条件冲突",
  };
  const raw = scalar(value);
  return labels[raw] ?? "";
}

function outputIntentLabel(value: unknown): string {
  const labels: Record<string, string> = {
    comparison: "比较研究",
    summary: "研究梳理",
    decision_support: "决策支持",
    literature_review: "文献综述",
    hypothesis_generation: "假设生成",
  };
  const raw = scalar(value);
  return labels[raw] ?? "";
}

function constraintTypeLabel(value: unknown): string {
  const labels: Record<string, string> = {
    condition_threshold: "条件阈值",
    experimental_condition: "实验条件",
  };
  return labels[scalar(value)] ?? "";
}

function DetailLine({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="grid gap-1 sm:grid-cols-[9rem_minmax(0,1fr)] sm:gap-3">
      <Typography.Text type="secondary">{label}</Typography.Text>
      <div className="min-w-0 break-words text-text">{children}</div>
    </div>
  );
}

function ConstraintDetail({
  constraint,
  index,
}: {
  constraint: JsonObject;
  index: number;
}) {
  const candidateValues = textList(constraint.candidate_values);
  const searchTerms = textList(constraint.downstream_search_terms);
  const originalPhrase = scalar(constraint.original_phrase);
  const interpretedValue = scalar(constraint.interpreted_value);
  const evidenceBasis = scalar(constraint.evidence_basis);
  const title = originalPhrase || interpretedValue || `约束 ${index + 1}`;
  const state = constraintStateLabel(constraint.semantic_state);
  const type = constraintTypeLabel(constraint.constraint_type);

  return (
    <article className="rounded-lg border border-border bg-surface-soft p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Typography.Text strong>{title}</Typography.Text>
        {state ? <Tag className="!m-0">{state}</Tag> : null}
      </div>
      <div className="space-y-2">
        {originalPhrase && originalPhrase !== title ? (
          <DetailLine label="原始表述">{originalPhrase}</DetailLine>
        ) : null}
        {type ? <DetailLine label="条件类型">{type}</DetailLine> : null}
        {interpretedValue && !describesSameFact(interpretedValue, title) ? (
          <DetailLine label="解释后的值">{interpretedValue}</DetailLine>
        ) : null}
        {candidateValues.length > 0 ? (
          <DetailLine label="候选值">
            <TextTags values={candidateValues} />
          </DetailLine>
        ) : null}
        {scalar(constraint.domain_rationale) ? (
          <DetailLine label="专业依据">
            {scalar(constraint.domain_rationale)}
          </DetailLine>
        ) : null}
        {searchTerms.length > 0 ? (
          <DetailLine label="下游检索词">
            <TextTags values={searchTerms} />
          </DetailLine>
        ) : null}
        {evidenceBasis && !describesSameFact(evidenceBasis, title) ? (
          <DetailLine label="信息依据">{evidenceBasis}</DetailLine>
        ) : null}
      </div>
    </article>
  );
}

interface OpenDecisionItem {
  title: string;
  impact: string;
  kind: "missing" | "risk" | "open";
}

function openDecisionItems(
  intent: JsonObject,
  criteria: JsonObject,
): OpenDecisionItem[] {
  const items: OpenDecisionItem[] = [];
  const add = (item: OpenDecisionItem) => {
    if (
      !item.title ||
      items.some((current) => describesSameFact(current.title, item.title))
    ) {
      return;
    }
    items.push(item);
  };

  for (const item of asObjectArray(criteria.missing_information)) {
    add({
      title: scalar(item.information),
      impact: scalar(item.why_needed),
      kind: "missing",
    });
  }
  for (const item of asObjectArray(criteria.research_risks)) {
    add({
      title: scalar(item.risk),
      impact: scalar(item.downstream_impact),
      kind: "risk",
    });
  }
  for (const ambiguity of textList(intent.ambiguities)) {
    add({ title: ambiguity, impact: "", kind: "open" });
  }
  for (const constraint of asObjectArray(criteria.interpreted_constraints)) {
    if (
      ["confirmed", "unrestricted"].includes(scalar(constraint.semantic_state))
    ) {
      continue;
    }
    add({
      title:
        scalar(constraint.original_phrase) ||
        scalar(constraint.interpreted_value),
      impact: scalar(constraint.domain_rationale),
      kind: "open",
    });
  }
  return items;
}

function OpenDecisionList({ items }: { items: OpenDecisionItem[] }) {
  if (items.length === 0) return null;
  return (
    <div className="divide-y divide-border">
      {items.map((item) => (
        <div className="py-2 first:pt-0 last:pb-0" key={item.title}>
          <div className="flex items-start gap-2">
            <span
              aria-hidden="true"
              className={`mt-2 h-1.5 w-1.5 shrink-0 rounded-full ${
                item.kind === "risk" ? "bg-danger" : "bg-warning"
              }`}
            />
            <div className="min-w-0">
              <Typography.Text strong>{item.title}</Typography.Text>
              {item.impact ? (
                <Typography.Paragraph className="!mb-0 !mt-0.5 text-sm text-text-secondary">
                  {item.impact}
                </Typography.Paragraph>
              ) : null}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function ProblemFramingDetails({
  document,
  remainingOpenItems,
}: {
  document: JsonObject;
  remainingOpenItems: OpenDecisionItem[];
}) {
  const intent = asObject(document.intent_understanding) ?? {};
  const criteria = asObject(document.constraints_criteria) ?? {};
  const constraints = asObjectArray(criteria.interpreted_constraints);
  const outputIntent = outputIntentLabel(intent.output_intent);
  const intentItems = [
    scalar(intent.original_user_query)
      ? {
          key: "query",
          label: "原始研究请求",
          children: scalar(intent.original_user_query),
        }
      : null,
    outputIntent
      ? {
          key: "intent",
          label: "研究输出目标",
          children: outputIntent,
        }
      : null,
    scalar(intent.requested_source_scope)
      ? {
          key: "scope",
          label: "来源范围",
          children: scalar(intent.requested_source_scope),
        }
      : null,
  ].filter((item): item is { key: string; label: string; children: string } =>
    Boolean(item),
  );

  return (
    <div className="space-y-5" data-testid="ot-problem-framing-details">
      {intentItems.length > 0 ? (
        <section>
          <Typography.Title level={5}>问题来源与研究目标</Typography.Title>
          <Descriptions bordered size="small" column={1} items={intentItems} />
        </section>
      ) : null}
      {constraints.length > 0 ? (
        <section>
          <Typography.Title level={5}>约束核查信息</Typography.Title>
          <div className="space-y-2">
            {constraints.map((constraint, index) => (
              <ConstraintDetail
                constraint={constraint}
                index={index}
                key={`constraint-${index}`}
              />
            ))}
          </div>
        </section>
      ) : null}
      {remainingOpenItems.length > 0 ? (
        <section>
          <Typography.Title level={5}>其他待确认事项</Typography.Title>
          <div className="rounded-lg border border-warning-border bg-surface-soft p-3">
            <OpenDecisionList items={remainingOpenItems} />
          </div>
        </section>
      ) : null}
    </div>
  );
}

function ProblemFramingPresentation({ document }: { document: JsonObject }) {
  const intent = asObject(document.intent_understanding) ?? {};
  const criteria = asObject(document.constraints_criteria) ?? {};
  const researchObject = scalar(intent.research_object);
  const targetMetrics = textList(intent.target_metrics);
  const boundaryConditions = textList(intent.boundary_conditions);
  const comparisonBaseline = scalar(intent.comparison_baseline);
  const sourceScope = scalar(intent.requested_source_scope);
  const constraints = asObjectArray(criteria.interpreted_constraints);
  const confirmedConstraints = constraints.filter((item) =>
    ["confirmed", "unrestricted"].includes(scalar(item.semantic_state)),
  );
  const confirmedConstraintValues = confirmedConstraints.map(
    (item) => scalar(item.interpreted_value) || scalar(item.original_phrase),
  );
  const confirmedBoundaries = uniqueFacts([
    ...boundaryConditions,
    ...confirmedConstraintValues,
  ]);
  const openItems = openDecisionItems(intent, criteria);
  const visibleOpenItems = openItems.slice(0, 3);
  const remainingOpenItems = openItems.slice(visibleOpenItems.length);
  const hasCore = Boolean(researchObject || targetMetrics.length);
  const hasConstraints = Boolean(
    confirmedBoundaries.length || comparisonBaseline || sourceScope,
  );

  return (
    <section className="space-y-5" data-testid="ot-problem-framing-view">
      <header>
        <Typography.Title level={3} className="!mb-1">
          研究问题与边界
        </Typography.Title>
        <Typography.Text type="secondary">
          当前正式问题建模中已经确认的研究重点、边界与未决信息。
        </Typography.Text>
      </header>

      <div className="grid items-start gap-3 lg:grid-cols-3">
        {hasCore ? (
          <section
            aria-labelledby="ot-problem-core-title"
            className="rounded-xl border border-border border-l-4 border-l-brand bg-surface-soft p-4"
          >
            <Typography.Title
              id="ot-problem-core-title"
              level={4}
              className="!mb-3 !text-text"
            >
              研究什么
            </Typography.Title>
            {researchObject ? (
              <div>
                <Typography.Text type="secondary">
                  研究对象 / 当前重点
                </Typography.Text>
                <Typography.Paragraph className="!mb-0 !mt-1 font-medium !text-text">
                  {researchObject}
                </Typography.Paragraph>
              </div>
            ) : null}
            {targetMetrics.length > 0 ? (
              <div className="mt-3">
                <Typography.Text type="secondary">
                  关注指标 / 结果
                </Typography.Text>
                <div className="mt-1.5">
                  <CompactTerms values={targetMetrics} />
                </div>
              </div>
            ) : null}
          </section>
        ) : null}

        {hasConstraints ? (
          <section
            aria-labelledby="ot-problem-constraints-title"
            className="rounded-xl border border-border border-l-4 border-l-success bg-surface-soft p-4"
          >
            <Typography.Title
              id="ot-problem-constraints-title"
              level={4}
              className="!mb-3 !text-text"
            >
              已确认边界
            </Typography.Title>
            {confirmedBoundaries.length > 0 ? (
              <BulletList values={confirmedBoundaries} />
            ) : null}
            {comparisonBaseline ? (
              <div className="mt-3">
                <Typography.Text type="secondary">比较基准</Typography.Text>
                <Typography.Paragraph className="!mb-0 !mt-0.5 font-medium !text-text">
                  {comparisonBaseline}
                </Typography.Paragraph>
              </div>
            ) : null}
            {sourceScope ? (
              <div className="mt-3">
                <Typography.Text type="secondary">证据来源范围</Typography.Text>
                <Typography.Paragraph className="!mb-0 !mt-0.5 font-medium !text-text">
                  {sourceScope}
                </Typography.Paragraph>
              </div>
            ) : null}
          </section>
        ) : null}

        {visibleOpenItems.length > 0 ? (
          <section
            aria-labelledby="ot-problem-unknowns-title"
            className="rounded-xl border border-border border-l-4 border-l-warning bg-surface-soft p-4"
          >
            <Typography.Title
              id="ot-problem-unknowns-title"
              level={4}
              className="!mb-3 !text-text"
            >
              仍需确认
            </Typography.Title>
            <OpenDecisionList items={visibleOpenItems} />
            {remainingOpenItems.length > 0 ? (
              <Typography.Text type="secondary" className="mt-2 block text-xs">
                另有 {remainingOpenItems.length} 项可在详情中查看
              </Typography.Text>
            ) : null}
          </section>
        ) : null}
      </div>

      <Collapse
        ghost
        items={[
          {
            key: "details",
            label: "查看详情",
            children: (
              <ProblemFramingDetails
                document={document}
                remainingOpenItems={remainingOpenItems}
              />
            ),
          },
        ]}
      />
    </section>
  );
}

const SEARCH_INTENT_LABELS: Record<string, string> = {
  core_topic: "核心主题",
  constraint_focused: "约束导向",
  mechanism_focused: "机制导向",
  method_or_strategy: "方法与策略",
  metric_or_performance: "指标与性能",
  adjacent_but_relevant: "相邻方向",
  noise_exclusion: "噪声排除",
};

function searchIntentLabel(value: unknown): string {
  const raw = scalar(value);
  return SEARCH_INTENT_LABELS[raw] ?? "";
}

function priorityLabel(value: unknown): string {
  const raw = scalar(value);
  const labels: Record<string, string> = { "1": "高", "2": "中", "3": "低" };
  return labels[raw] ?? raw;
}

function variantTypeLabel(value: unknown): string {
  const raw = scalar(value);
  const labels: Record<string, string> = {
    strict: "严格检索",
    broad: "扩展检索",
    fallback: "后备检索",
    exclusion: "排除检索",
  };
  return labels[raw] ?? "";
}

function protocolStatusLabel(value: unknown): string {
  const raw = scalar(value);
  const labels: Record<string, string> = {
    ok: "正常",
    degraded: "部分降级",
    fatal: "不可用",
    no_usable_result: "无可用结果",
  };
  return labels[raw] ?? "";
}

const SOURCE_CONSTRAINT_LABELS: Record<string, string> = {
  user_query: "原始研究问题",
  capacity_retention_metric: "容量保持率指标",
  fast_charging_condition: "快充条件",
  voltage_window: "电压窗口",
  "research_context.target_metrics": "研究目标指标",
};

const PAPER_TYPE_LABELS: Record<string, string> = {
  experimental: "实验研究",
  "mechanistic study": "机理研究",
  "materials design": "材料设计研究",
  "performance benchmark": "性能基准研究",
};

function mappedLabels(
  values: string[],
  labels: Record<string, string>,
): string[] {
  return uniqueText(values.map((value) => labels[value] ?? "").filter(Boolean));
}

function searchFocus(row: JsonObject): string[] {
  return uniqueText(textList(row.query_terms)).slice(0, 3);
}

function SearchDirectionCard({
  row,
  index,
}: {
  row: JsonObject;
  index: number;
}) {
  const direction =
    searchIntentLabel(row.search_intent) || `检索方向 ${index + 1}`;
  const reason = scalar(row.rationale);
  const focus = searchFocus(row);
  return (
    <article className="rounded-lg border border-border bg-surface-soft p-3">
      <div className="flex items-start justify-between gap-3">
        <Typography.Text strong>{direction}</Typography.Text>
        {scalar(row.priority) ? (
          <Tag className="!m-0 shrink-0">
            优先级：{priorityLabel(row.priority)}
          </Tag>
        ) : null}
      </div>
      {reason ? (
        <Typography.Paragraph className="!mb-0 !mt-1.5 text-sm leading-5 text-text-secondary">
          {reason}
        </Typography.Paragraph>
      ) : null}
      {focus.length > 0 ? (
        <div className="mt-2 text-sm">
          <Typography.Text type="secondary">检索重点：</Typography.Text>
          <InlineValues values={focus} />
        </div>
      ) : null}
    </article>
  );
}

function FullSearchTerms({ rows }: { rows: JsonObject[] }) {
  return (
    <div className="space-y-3">
      {rows.map((row, index) => {
        const mustInclude = uniqueText(textList(row.must_include_terms));
        const excludes = uniqueText(textList(row.exclude_terms));
        const alreadyCategorized = [...mustInclude, ...excludes];
        const queryTerms = uniqueText(textList(row.query_terms)).filter(
          (term) =>
            !alreadyCategorized.some((value) => describesSameFact(value, term)),
        );
        const optional = uniqueText(textList(row.optional_terms)).filter(
          (term) =>
            ![...alreadyCategorized, ...queryTerms].some((value) =>
              describesSameFact(value, term),
            ),
        );
        return (
          <article
            className="rounded-lg border border-border bg-surface-soft p-3"
            key={`matrix-terms-${index}`}
          >
            <Typography.Text strong>
              {searchIntentLabel(row.search_intent) || `检索方向 ${index + 1}`}
            </Typography.Text>
            <div className="mt-2 space-y-1.5">
              {mustInclude.length > 0 ? (
                <DetailLine label="必须包含">
                  <InlineValues values={mustInclude} />
                </DetailLine>
              ) : null}
              {excludes.length > 0 ? (
                <DetailLine label="排除条件">
                  <InlineValues values={excludes} />
                </DetailLine>
              ) : null}
              {queryTerms.length > 0 ? (
                <DetailLine label="其他检索词">
                  <InlineValues values={queryTerms} />
                </DetailLine>
              ) : null}
              {optional.length > 0 ? (
                <DetailLine label="可选扩展">
                  <InlineValues values={optional} />
                </DetailLine>
              ) : null}
            </div>
          </article>
        );
      })}
    </div>
  );
}

function MatrixTechnicalDetails({ rows }: { rows: JsonObject[] }) {
  return (
    <div className="space-y-2">
      {rows.map((row, index) => {
        const sourceConstraints = mappedLabels(
          textList(row.source_constraints),
          SOURCE_CONSTRAINT_LABELS,
        );
        const paperType =
          PAPER_TYPE_LABELS[scalar(row.expected_paper_type)] ?? "";
        const database = scalar(row.target_database);
        if (!paperType && !database && sourceConstraints.length === 0)
          return null;
        return (
          <article
            className="rounded-lg border border-border bg-surface-soft p-3"
            key={`matrix-detail-${index}`}
          >
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <Typography.Text strong>
                {searchIntentLabel(row.search_intent) ||
                  `检索方向 ${index + 1}`}
              </Typography.Text>
            </div>
            <div className="space-y-2">
              {paperType ? (
                <DetailLine label="预期文献类型">{paperType}</DetailLine>
              ) : null}
              {database ? (
                <DetailLine label="目标数据库">{database}</DetailLine>
              ) : null}
              {sourceConstraints.length > 0 ? (
                <DetailLine label="依据的研究边界">
                  <InlineValues values={sourceConstraints} />
                </DetailLine>
              ) : null}
            </div>
          </article>
        );
      })}
    </div>
  );
}

function ExpandedQueryDetails({ rows }: { rows: JsonObject[] }) {
  return (
    <div className="space-y-2">
      {rows.map((row, index) => {
        const query = scalar(row.query);
        const variant = variantTypeLabel(row.variant_type);
        const direction = searchIntentLabel(row.search_intent);
        return (
          <article
            className="rounded-lg border border-border bg-surface-soft p-3"
            key={`query-${index}`}
          >
            <div className="mb-2 flex flex-wrap gap-1.5">
              {variant ? <Tag className="!m-0">{variant}</Tag> : null}
              {direction ? <Tag className="!m-0">{direction}</Tag> : null}
              {scalar(row.priority) ? (
                <Tag className="!m-0">
                  优先级：{priorityLabel(row.priority)}
                </Tag>
              ) : null}
            </div>
            {query ? (
              <Typography.Text
                code
                className="block whitespace-normal break-all leading-6"
              >
                {query}
              </Typography.Text>
            ) : null}
            {scalar(row.rationale) ? (
              <Typography.Paragraph className="!mb-0 !mt-2 text-text-secondary">
                {scalar(row.rationale)}
              </Typography.Paragraph>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}

const FACET_LABELS: Record<string, string> = {
  core_entities: "核心对象",
  research_object_en: "研究对象检索表达",
  mechanisms: "机制",
  target_metrics_en: "目标指标",
  task_context: "研究情境",
  must_have_terms: "必须包含",
  nice_to_have_terms: "可选扩展",
  negative_contexts: "排除条件",
};

const QUERY_FACET_LABELS: Record<string, string> = {
  object: "对象",
  mechanism: "机制",
  metric: "指标",
  context: "情境",
  evaluation: "评价",
};

const VALIDATION_FLAG_LABELS: Record<string, string> = {
  risk_flags_not_used_as_exclude_terms: "风险标记未直接作为排除条件",
  generic_boolean_only: "仅使用通用布尔检索式",
};

const PROTOCOL_PATH_LABELS: Record<string, string> = {
  search_matrix: "检索方向",
  expanded_query_variants: "扩展检索式",
};

const PROTOCOL_COVERAGE_LABELS: Record<string, string> = {
  search_intent_count: "已覆盖方向",
  search_intent_total: "计划方向",
  accepted_query_count: "已接受检索式",
  quarantined_item_count: "已隔离项目",
};

function FacetDetails({
  facets,
  matrix,
}: {
  facets: JsonObject;
  matrix: JsonObject[];
}) {
  const queryFacets = asObject(facets.query_facets);
  const seen = new Set<string>();
  for (const row of matrix) {
    for (const key of [
      "query_terms",
      "must_include_terms",
      "optional_terms",
      "exclude_terms",
    ]) {
      for (const value of textList(row[key])) {
        seen.add(value.toLocaleLowerCase());
      }
    }
  }
  const takeUnseen = (values: string[]) =>
    uniqueText(values).filter((value) => {
      const key = value.toLocaleLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  const groups = Object.entries(FACET_LABELS)
    .map(([key, label]) => ({
      key,
      label,
      values: takeUnseen(textList(facets[key])),
    }))
    .filter((group) => group.values.length > 0);
  const queryGroups = queryFacets
    ? Object.entries(QUERY_FACET_LABELS)
        .map(([key, label]) => ({
          key: `query-${key}`,
          label,
          values: takeUnseen(textList(queryFacets[key])),
        }))
        .filter((group) => group.values.length > 0)
    : [];
  if (groups.length === 0 && queryGroups.length === 0) return null;

  return (
    <div className="space-y-3">
      {groups.map((group) => (
        <DetailLine label={group.label} key={group.key}>
          <InlineValues values={group.values} />
        </DetailLine>
      ))}
      {queryGroups.length > 0 ? (
        <div className="rounded-lg border border-border bg-surface-soft p-3">
          <Typography.Text strong>补充检索维度</Typography.Text>
          <div className="mt-3 space-y-2">
            {queryGroups.map((group) => (
              <DetailLine label={group.label} key={group.key}>
                <InlineValues values={group.values} />
              </DetailLine>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function QualityReportDetails({ report }: { report: JsonObject }) {
  const items = [
    "search_matrix_count" in report
      ? {
          key: "matrix-count",
          label: "检索方向数",
          children: displayValue(report.search_matrix_count),
        }
      : null,
    "expanded_query_variants_count" in report
      ? {
          key: "query-count",
          label: "扩展检索式数",
          children: displayValue(report.expanded_query_variants_count),
        }
      : null,
    "fallback_used" in report
      ? {
          key: "fallback",
          label: "是否使用后备策略",
          children: booleanLabel(report.fallback_used),
        }
      : null,
  ].filter((item): item is { key: string; label: string; children: string } =>
    Boolean(item),
  );
  const coverage = mappedLabels(
    textList(report.search_intent_coverage),
    SEARCH_INTENT_LABELS,
  );
  const constraints = mappedLabels(
    textList(report.constraints_utilized),
    SOURCE_CONSTRAINT_LABELS,
  );
  const flags = mappedLabels(
    textList(report.validation_flags),
    VALIDATION_FLAG_LABELS,
  );

  return (
    <div className="space-y-3">
      {items.length > 0 ? (
        <Descriptions bordered size="small" column={1} items={items} />
      ) : null}
      {coverage.length > 0 ? (
        <DetailLine label="已覆盖方向">
          <InlineValues values={coverage} />
        </DetailLine>
      ) : null}
      {constraints.length > 0 ? (
        <DetailLine label="已使用约束">
          <InlineValues values={constraints} />
        </DetailLine>
      ) : null}
      {flags.length > 0 ? (
        <DetailLine label="校验标记">
          <InlineValues values={flags} />
        </DetailLine>
      ) : null}
    </div>
  );
}

function ProtocolItems({
  values,
  mode,
}: {
  values: JsonObject[];
  mode: "accepted" | "quarantined" | "repaired";
}) {
  const items = values
    .map((item) => ({
      item,
      label: PROTOCOL_PATH_LABELS[scalar(item.path)] ?? "",
    }))
    .filter((entry) => entry.label);
  if (items.length === 0) return null;
  const detailKey =
    mode === "accepted"
      ? "count"
      : mode === "quarantined"
        ? "reason"
        : "action";
  return (
    <ul className="m-0 space-y-1 pl-5">
      {items.map(({ item, label }, index) => (
        <li key={`${mode}-${index}`} className="break-words">
          {label}：{displayValue(item[detailKey])}
        </li>
      ))}
    </ul>
  );
}

function ResultProtocolDetails({ protocol }: { protocol: JsonObject }) {
  const accepted = asObjectArray(protocol.accepted_items);
  const quarantined = asObjectArray(protocol.quarantined_items);
  const repaired = asObjectArray(protocol.repaired_items);
  const warnings = textList(protocol.warnings);
  const coverage = asObject(protocol.coverage);
  const status = protocolStatusLabel(protocol.status);
  const coverageItems = coverage
    ? Object.entries(coverage)
        .map(([key, value]) => ({
          key,
          label: PROTOCOL_COVERAGE_LABELS[key] ?? "",
          value,
        }))
        .filter((item) => item.label)
    : [];

  return (
    <div className="space-y-3">
      {status ? <DetailLine label="结果状态">{status}</DetailLine> : null}
      {accepted.length > 0 ? (
        <DetailLine label="已接受内容">
          <ProtocolItems values={accepted} mode="accepted" />
        </DetailLine>
      ) : null}
      {quarantined.length > 0 ? (
        <DetailLine label="已隔离内容">
          <ProtocolItems values={quarantined} mode="quarantined" />
        </DetailLine>
      ) : null}
      {repaired.length > 0 ? (
        <DetailLine label="已修复内容">
          <ProtocolItems values={repaired} mode="repaired" />
        </DetailLine>
      ) : null}
      {warnings.length > 0 ? (
        <DetailLine label="警告">
          <BulletList values={warnings} />
        </DetailLine>
      ) : null}
      {coverageItems.length > 0 ? (
        <div className="rounded-lg border border-border bg-surface-soft p-3">
          <Typography.Text strong>覆盖记录</Typography.Text>
          <div className="mt-2 space-y-1.5">
            {coverageItems.map((item) => (
              <DetailLine label={item.label} key={item.key}>
                {displayValue(item.value)}
              </DetailLine>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function RetrievalStrategyDetails({
  document,
  matrix,
}: {
  document: JsonObject;
  matrix: JsonObject[];
}) {
  const expanded = asObjectArray(document.expanded_query_variants);
  const facets = asObject(document.assembled_facets);
  const quality = asObject(document.quality_report);
  const protocol = asObject(document.result_protocol);
  const hasSourceDetails = matrix.some(
    (row) =>
      Boolean(PAPER_TYPE_LABELS[scalar(row.expected_paper_type)]) ||
      Boolean(scalar(row.target_database)) ||
      mappedLabels(textList(row.source_constraints), SOURCE_CONSTRAINT_LABELS)
        .length > 0,
  );
  const items: NonNullable<CollapseProps["items"]> = [];
  if (matrix.length > 0) {
    items.push({
      key: "terms",
      label: `完整检索词（${matrix.length} 个方向）`,
      children: (
        <div className="space-y-4">
          <FullSearchTerms rows={matrix} />
          {facets ? <FacetDetails facets={facets} matrix={matrix} /> : null}
        </div>
      ),
    });
  }
  if (expanded.length > 0) {
    items.push({
      key: "queries",
      label: `扩展检索式（${expanded.length} 条）`,
      children: <ExpandedQueryDetails rows={expanded} />,
    });
  }
  if (hasSourceDetails) {
    items.push({
      key: "sources",
      label: "来源与数据库约束",
      children: <MatrixTechnicalDetails rows={matrix} />,
    });
  }
  if (quality || protocol) {
    items.push({
      key: "quality",
      label: "质量与结果协议",
      children: (
        <div className="space-y-4">
          {quality ? <QualityReportDetails report={quality} /> : null}
          {protocol ? <ResultProtocolDetails protocol={protocol} /> : null}
        </div>
      ),
    });
  }

  return (
    <div data-testid="ot-retrieval-strategy-details">
      <Typography.Paragraph className="!mb-3 text-sm text-text-secondary">
        按主题展开核查完整检索词、检索式与执行约束。
      </Typography.Paragraph>
      <Collapse size="small" items={items} />
    </div>
  );
}

function RetrievalStrategyPresentation({ document }: { document: JsonObject }) {
  const matrix = asObjectArray(document.search_matrix);
  const facets = asObject(document.assembled_facets) ?? {};
  const mustInclude = textList(facets.must_have_terms);
  const excludes = textList(facets.negative_contexts);

  return (
    <section className="space-y-5" data-testid="ot-retrieval-strategy-view">
      <header>
        <Typography.Title level={3} className="!mb-1">
          证据检索计划
        </Typography.Title>
        <Typography.Text type="secondary">
          为回答当前研究问题，按方向、理由和优先级组织证据检索。
        </Typography.Text>
      </header>

      {mustInclude.length > 0 || excludes.length > 0 ? (
        <section
          className="grid gap-3 lg:grid-cols-2"
          aria-label="关键检索边界"
        >
          {mustInclude.length > 0 ? (
            <div className="rounded-lg border border-border border-l-4 border-l-success bg-surface-soft p-3">
              <Typography.Text strong>必须包含</Typography.Text>
              <div className="mt-2">
                <CompactTerms values={mustInclude} />
              </div>
            </div>
          ) : null}
          {excludes.length > 0 ? (
            <div className="rounded-lg border border-border border-l-4 border-l-text-muted bg-surface-soft p-3">
              <Typography.Text strong>排除条件</Typography.Text>
              <div className="mt-2">
                <CompactTerms values={excludes} />
              </div>
            </div>
          ) : null}
        </section>
      ) : null}

      {matrix.length > 0 ? (
        <section aria-labelledby="ot-retrieval-matrix-title">
          <Typography.Title id="ot-retrieval-matrix-title" level={4}>
            主要检索方向
          </Typography.Title>
          <div className="grid gap-2 lg:grid-cols-2">
            {matrix.map((row, index) => (
              <SearchDirectionCard
                row={row}
                index={index}
                key={`search-direction-${index}`}
              />
            ))}
          </div>
        </section>
      ) : null}

      <Collapse
        ghost
        items={[
          {
            key: "details",
            label: "查看详情",
            children: (
              <RetrievalStrategyDetails document={document} matrix={matrix} />
            ),
          },
        ]}
      />
    </section>
  );
}

export function OpenTrajectoryResearchPlanningPresentation({
  kind,
  document,
}: {
  kind: OpenTrajectoryResearchPlanningKind;
  document: JsonObject;
}): ReactNode {
  return kind === "problem_framing" ? (
    <ProblemFramingPresentation document={document} />
  ) : (
    <RetrievalStrategyPresentation document={document} />
  );
}
