import { Collapse, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { ReactNode } from "react";

type JsonObject = Record<string, unknown>;

const CONDITION_KEYS = [
  "task_scope",
  "implementation",
  "data_split",
  "preprocessing",
  "evaluation_protocol",
  "metric_definition",
  "randomness",
  "hardware_environment",
] as const;

const CONDITION_LABELS: Record<(typeof CONDITION_KEYS)[number], string> = {
  task_scope: "任务范围",
  implementation: "实现方式",
  data_split: "数据划分",
  preprocessing: "预处理",
  evaluation_protocol: "评测协议",
  metric_definition: "指标定义",
  randomness: "随机性设置",
  hardware_environment: "硬件环境",
};

const CONDITION_STATUS_LABELS: Record<string, string> = {
  compatible: "兼容",
  mismatch: "不一致",
  unknown: "未知",
};

const COMPARABILITY_STATUS_LABELS: Record<string, string> = {
  comparable: "可以比较",
  not_comparable: "当前不可直接比较",
  indeterminate: "当前无法确认可比性",
};

const OVERALL_VERDICT_LABELS: Record<string, string> = {
  meets_standard: "达到判据",
  does_not_meet_standard: "未达到判据",
  indeterminate: "无法判定",
};

const RECORD_STATUS_LABELS: Record<string, string> = {
  complete: "记录完整",
  incomplete: "记录不完整",
  failed: "记录失败",
};

const EXTRACTION_STATUS_LABELS: Record<string, string> = {
  succeeded: "提取成功",
  failed: "提取失败",
};

const RULE_KIND_LABELS: Record<string, string> = {
  exact: "精确一致",
  absolute_tolerance: "绝对容差",
  relative_tolerance: "相对容差",
  actual_gte_reference: "本次结果不低于参考值",
  actual_lte_reference: "本次结果不高于参考值",
};

const EXTRACTION_FAILURE_LABELS: Record<string, string> = {
  metric_not_found: "未找到该指标",
  metric_ambiguous: "指标匹配不唯一",
  value_type_mismatch: "值类型不匹配",
  table_not_found: "未找到结果表",
  table_ambiguous: "结果表不唯一",
  table_row_ambiguous: "结果行不唯一",
  metric_extraction_incomplete: "指标提取不完整",
  execution_not_succeeded: "执行未成功",
  result_source_incomplete: "结果来源不完整",
  result_source_identity_mismatch: "结果来源身份不一致",
  skill_input_invalid: "输入合同无效",
  execution_record_invalid: "执行记录无效",
  result_source_unavailable: "结果来源不可用",
};

const COMPARISON_FAILURE_LABELS: Record<string, string> = {
  actual_metric_missing: "缺少对应实测指标",
  actual_metric_unavailable: "实测指标提取未成功",
  actual_metric_ambiguous: "实测指标身份不唯一",
  reference_metric_ambiguous: "参考指标身份不唯一",
  value_type_mismatch: "值类型不一致",
  unit_mismatch: "单位不一致",
  actual_value_type_invalid: "实测值类型无效",
  reference_value_type_invalid: "参考值类型无效",
  actual_source_trace_invalid: "实测来源轨迹无效",
  comparison_rule_invalid: "比较规则无效",
  comparison_rule_type_incompatible: "比较规则与值类型不兼容",
  comparison_tolerance_invalid: "容差值无效",
  comparison_incomplete: "比较未完整完成",
  comparability_not_established: "可比性尚未建立",
  skill_input_invalid: "输入合同无效",
};

const RULE_APPLICATION_STATE_LABELS: Record<string, string> = {
  succeeded: "确定性比较成功",
  failed: "确定性比较失败",
};

const RULE_APPLICATION_COMPARISON_STATUS_LABELS: Record<string, string> = {
  complete: "比较完整",
  incomplete: "比较不完整",
};

const UNKNOWN_LABELS: Record<string, string> = {
  comparability_not_established: "可比性尚未建立",
  comparison_incomplete: "比较未完整完成",
  skill_input_invalid: "输入合同无效",
};

function displayUnknown(value: string): string | null {
  if (UNKNOWN_LABELS[value]) return UNKNOWN_LABELS[value];
  // Keep natural-language unknowns; park snake_case machine codes in details only.
  if (/^[a-z][a-z0-9_]*$/.test(value)) return null;
  return value;
}

function asObject(value: unknown): JsonObject | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function knownLabel(value: unknown, labels: Record<string, string>): string | null {
  const key = text(value);
  return key ? (labels[key] ?? null) : null;
}

function formatScalar(value: unknown): string | null {
  if (value === null) return null;
  if (typeof value === "string") return value.trim() || null;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  return null;
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is string => typeof item === "string")
    .map((item) => item.trim())
    .filter(Boolean);
}

function FailClosedNotice({ part }: { part: string }) {
  return (
    <Typography.Paragraph className="!mb-0 text-sm text-text-secondary">
      {part}当前无法安全结构化展示，可查看原始技术详情。
    </Typography.Paragraph>
  );
}

function DetailBlock({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="grid gap-2">
      <Typography.Text strong>{title}</Typography.Text>
      <div className="grid gap-2 text-sm text-text-secondary">{children}</div>
    </section>
  );
}

function Kv({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null;
  return (
    <div>
      <span className="text-text-muted">{label}：</span>
      <span>{value}</span>
    </div>
  );
}

function formatMeasuredValue(value: unknown, unit: unknown): string {
  const formatted = formatScalar(value);
  if (formatted === null) return "—";
  const unitText = text(unit);
  return unitText ? `${formatted} ${unitText}` : formatted;
}

function formatRule(rule: JsonObject | null): string | null {
  if (!rule) return null;
  const kindLabel = knownLabel(rule.kind, RULE_KIND_LABELS);
  if (!kindLabel) return null;
  const tolerance = formatScalar(rule.tolerance);
  return tolerance === null ? kindLabel : `${kindLabel}（容差 ${tolerance}）`;
}

function meetsRuleLabel(meetsRule: unknown, comparisonStatus: string): string {
  if (comparisonStatus === "failed") return "无法判定";
  if (meetsRule === true) return "达到判据";
  if (meetsRule === false) return "未达到判据";
  return "无法判定";
}

function isStableMetricRow(row: JsonObject): boolean {
  return (
    typeof row.metric_name === "string" &&
    row.metric_name.trim().length > 0 &&
    typeof row.value_type === "string" &&
    (typeof row.unit === "string" || row.unit === null) &&
    typeof row.extraction_status === "string"
  );
}

function isStableJudgmentRow(row: JsonObject): boolean {
  if (typeof row.metric_name !== "string" || !row.metric_name.trim()) return false;
  if (typeof row.comparison_status !== "string") return false;
  if (!(row.meets_rule === true || row.meets_rule === false || row.meets_rule === null)) {
    return false;
  }
  if (!(row.actual === null || asObject(row.actual))) return false;
  if (!asObject(row.reference)) return false;
  if (!asObject(row.rule)) return false;
  return true;
}

function isStableConditionItem(value: unknown): JsonObject | null {
  const item = asObject(value);
  if (!item) return null;
  if (!knownLabel(item.status, CONDITION_STATUS_LABELS)) return null;
  if (!Array.isArray(item.evidence_refs)) return null;
  if (!item.evidence_refs.every((ref) => typeof ref === "string")) return null;
  if ("note" in item && typeof item.note !== "string") return null;
  return item;
}

type ActualMetricRow = {
  key: string;
  metricName: string;
  valueText: string;
  unitText: string;
  statusText: string;
  failureText: string | null;
  sourceTrace: JsonObject | null;
};

function projectActualMetrics(document: JsonObject): {
  statusLabel: string | null;
  executionRef: string | null;
  rows: ActualMetricRow[] | null;
  safeUnknowns: string[];
  partialFailureCount: number;
} {
  const statusLabel = knownLabel(document.status, RECORD_STATUS_LABELS);
  const executionRef =
    document.execution_record_ref === null
      ? null
      : text(document.execution_record_ref) || null;
  const safeUnknowns = stringList(document.unknowns);
  const partialFailures = Array.isArray(document.partial_failures)
    ? document.partial_failures
    : [];

  if (!Array.isArray(document.metrics)) {
    return {
      statusLabel,
      executionRef,
      rows: null,
      safeUnknowns,
      partialFailureCount: partialFailures.length,
    };
  }

  const rows: ActualMetricRow[] = [];
  for (const [index, raw] of document.metrics.entries()) {
    const row = asObject(raw);
    if (!row || !isStableMetricRow(row)) {
      return {
        statusLabel,
        executionRef,
        rows: null,
        safeUnknowns,
        partialFailureCount: partialFailures.length,
      };
    }
    const extractionStatus = text(row.extraction_status);
    const statusText =
      knownLabel(extractionStatus, EXTRACTION_STATUS_LABELS) ?? extractionStatus;
    const failureCode = text(row.failure_code);
    const failureText = failureCode
      ? (EXTRACTION_FAILURE_LABELS[failureCode] ?? failureCode)
      : null;
    rows.push({
      key: `${row.metric_name}-${index}`,
      metricName: text(row.metric_name),
      valueText: formatScalar(row.actual_value) ?? "—",
      unitText: row.unit === null ? "—" : text(row.unit) || "—",
      statusText: failureText && extractionStatus === "failed"
        ? `${statusText}（${failureText}）`
        : statusText,
      failureText,
      sourceTrace: asObject(row.source_trace),
    });
  }

  return {
    statusLabel,
    executionRef,
    rows,
    safeUnknowns,
    partialFailureCount: partialFailures.length,
  };
}

export function CsActualMetricRecordsPresentation({
  document,
}: {
  document: JsonObject;
}): ReactNode {
  const projected = projectActualMetrics(document);
  const visibleUnknowns = projected.safeUnknowns
    .map(displayUnknown)
    .filter((item): item is string => Boolean(item));
  const detailOnlyUnknowns = projected.safeUnknowns.filter(
    (item) => displayUnknown(item) === null,
  );
  const columns: ColumnsType<ActualMetricRow> = [
    { title: "指标", dataIndex: "metricName", key: "metricName" },
    { title: "本次结果", dataIndex: "valueText", key: "valueText" },
    { title: "单位", dataIndex: "unitText", key: "unitText", width: 100 },
    { title: "状态", dataIndex: "statusText", key: "statusText" },
  ];

  return (
    <section data-testid="cs-actual-metric-records" className="grid gap-4">
      <div className="grid gap-2">
        <Typography.Title level={4} className="!mb-0">
          本次实际结果
        </Typography.Title>
        <Typography.Paragraph className="!mb-0 text-sm text-text-secondary">
          查看这次真实执行实际测到了哪些指标。
        </Typography.Paragraph>
        {projected.statusLabel ? (
          <Tag className="m-0 w-fit">{projected.statusLabel}</Tag>
        ) : null}
      </div>

      {projected.rows ? (
        <Table<ActualMetricRow>
          size="small"
          pagination={false}
          rowKey="key"
          columns={columns}
          dataSource={projected.rows}
        />
      ) : (
        <FailClosedNotice part="实测指标列表" />
      )}

      {projected.partialFailureCount > 0 ? (
        <Typography.Paragraph className="!mb-0 text-sm text-text-secondary">
          存在 {projected.partialFailureCount} 条部分失败记录（元素细节见技术详情）。
        </Typography.Paragraph>
      ) : null}

      {visibleUnknowns.length > 0 ? (
        <section className="grid gap-2">
          <Typography.Text strong>未知</Typography.Text>
          <ul className="m-0 grid gap-1 pl-5 text-sm text-text-secondary">
            {visibleUnknowns.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      ) : null}

      <Collapse
        items={[
          {
            key: "details",
            label: "技术详情与核查信息",
            children: (
              <div className="grid gap-4">
                <DetailBlock title="执行与来源">
                  <Kv label="执行记录" value={projected.executionRef} />
                  {projected.rows?.map((row) => (
                    <div key={`trace-${row.key}`} className="rounded-md bg-surface-soft px-3 py-2">
                      <div className="font-medium text-text">{row.metricName}</div>
                      {row.failureText ? <Kv label="失败码说明" value={row.failureText} /> : null}
                      {row.sourceTrace ? (
                        <pre className="m-0 mt-2 overflow-auto whitespace-pre-wrap text-xs text-text-muted">
                          {JSON.stringify(row.sourceTrace, null, 2)}
                        </pre>
                      ) : (
                        <div className="mt-1 text-text-muted">无来源轨迹</div>
                      )}
                    </div>
                  ))}
                </DetailBlock>
                {detailOnlyUnknowns.length > 0 ? (
                  <DetailBlock title="原始未知码">
                    {detailOnlyUnknowns.map((item) => (
                      <div key={item}>{item}</div>
                    ))}
                  </DetailBlock>
                ) : null}
              </div>
            ),
          },
        ]}
      />
    </section>
  );
}

type JudgmentRow = {
  key: string;
  metricName: string;
  actualText: string;
  referenceText: string;
  ruleText: string;
  judgmentText: string;
  deltaText: string | null;
  failureText: string | null;
  calculationTrace: JsonObject | null;
};

function projectJudgmentRows(value: unknown): JudgmentRow[] | null {
  if (!Array.isArray(value)) return null;
  const rows: JudgmentRow[] = [];
  for (const [index, raw] of value.entries()) {
    const row = asObject(raw);
    if (!row || !isStableJudgmentRow(row)) return null;
    const actual = asObject(row.actual);
    const reference = asObject(row.reference);
    const rule = asObject(row.rule);
    const ruleText = formatRule(rule);
    if (!reference || !ruleText) return null;

    const comparisonStatus = text(row.comparison_status);
    const trace = asObject(row.calculation_trace);
    const signed = text(trace?.signed_delta_decimal);
    const absolute = text(trace?.absolute_delta_decimal);
    const deltaText =
      signed || absolute
        ? [
            signed ? `符号差 ${signed}` : null,
            absolute ? `绝对差 ${absolute}` : null,
          ]
            .filter(Boolean)
            .join("；")
        : null;

    const failureCode = text(row.failure_code);
    rows.push({
      key: `${row.metric_name}-${index}`,
      metricName: text(row.metric_name),
      actualText: actual
        ? formatMeasuredValue(actual.value, actual.unit)
        : "—",
      referenceText: formatMeasuredValue(reference.value, reference.unit),
      ruleText,
      judgmentText: meetsRuleLabel(row.meets_rule, comparisonStatus),
      deltaText,
      failureText: failureCode
        ? (COMPARISON_FAILURE_LABELS[failureCode] ?? failureCode)
        : null,
      calculationTrace: trace,
    });
  }
  return rows;
}

function projectComparability(value: unknown): {
  statusLabel: string | null;
  conditions: Array<{
    key: string;
    label: string;
    statusLabel: string;
    note: string | null;
    evidenceCount: number;
  }> | null;
  safe: boolean;
} {
  const comparability = asObject(value);
  if (!comparability) {
    return { statusLabel: null, conditions: null, safe: false };
  }
  const statusLabel = knownLabel(comparability.status, COMPARABILITY_STATUS_LABELS);
  if (!statusLabel) {
    return { statusLabel: null, conditions: null, safe: false };
  }

  const conditionsObject = asObject(comparability.conditions);
  if (!conditionsObject) {
    return { statusLabel, conditions: null, safe: false };
  }

  const conditions: Array<{
    key: string;
    label: string;
    statusLabel: string;
    note: string | null;
    evidenceCount: number;
  }> = [];

  for (const key of CONDITION_KEYS) {
    const item = isStableConditionItem(conditionsObject[key]);
    if (!item) {
      return { statusLabel, conditions: null, safe: false };
    }
    conditions.push({
      key,
      label: CONDITION_LABELS[key],
      statusLabel: knownLabel(item.status, CONDITION_STATUS_LABELS) ?? text(item.status),
      note: text(item.note) || null,
      evidenceCount: Array.isArray(item.evidence_refs) ? item.evidence_refs.length : 0,
    });
  }

  return { statusLabel, conditions, safe: true };
}

function projectRuleApplication(value: unknown): Array<{ label: string; value: string }> | null {
  const ruleApplication = asObject(value);
  if (!ruleApplication) return null;

  const allowedKeys = new Set([
    "deterministic_state",
    "comparison_status",
    "deterministic_overall_result",
    "requested_reference_count",
    "compared_metric_count",
    "failure_code",
  ]);
  if (Object.keys(ruleApplication).some((key) => !allowedKeys.has(key))) {
    // Extra keys are tolerated only if known fields remain readable; ignore unknowns in UI.
  }

  const rows: Array<{ label: string; value: string }> = [];
  const state = text(ruleApplication.deterministic_state);
  if (state) {
    rows.push({
      label: "确定性比较状态",
      value: RULE_APPLICATION_STATE_LABELS[state] ?? state,
    });
  }
  const comparisonStatus = text(ruleApplication.comparison_status);
  if (comparisonStatus) {
    rows.push({
      label: "比较完整度",
      value: RULE_APPLICATION_COMPARISON_STATUS_LABELS[comparisonStatus] ?? comparisonStatus,
    });
  }
  const overall = text(ruleApplication.deterministic_overall_result);
  if (overall) {
    rows.push({
      label: "确定性总体结果",
      value: OVERALL_VERDICT_LABELS[overall] ?? overall,
    });
  }
  const requested = formatScalar(ruleApplication.requested_reference_count);
  if (requested !== null) {
    rows.push({ label: "请求参考指标数", value: requested });
  }
  const compared = formatScalar(ruleApplication.compared_metric_count);
  if (compared !== null) {
    rows.push({ label: "已比较指标数", value: compared });
  }
  const failureCode = text(ruleApplication.failure_code);
  if (failureCode) {
    rows.push({
      label: "失败码",
      value: COMPARISON_FAILURE_LABELS[failureCode] ?? failureCode,
    });
  }
  return rows;
}

export function CsReproductionComparisonJudgmentPresentation({
  document,
}: {
  document: JsonObject;
}): ReactNode {
  const comparability = projectComparability(document.comparability);
  const judgmentRows = projectJudgmentRows(document.metric_judgments);
  const verdictLabel = knownLabel(document.overall_verdict, OVERALL_VERDICT_LABELS);
  const ruleApplicationRows = projectRuleApplication(document.rule_application);
  const safeUnknowns = stringList(document.unknowns);
  const visibleUnknowns = safeUnknowns
    .map(displayUnknown)
    .filter((item): item is string => Boolean(item));
  const detailOnlyUnknowns = safeUnknowns.filter(
    (item) => displayUnknown(item) === null,
  );
  const evidenceRefs = stringList(document.evidence_refs);
  const hasConditionMismatchRecords =
    Array.isArray(document.condition_mismatches) &&
    document.condition_mismatches.length > 0;

  const columns: ColumnsType<JudgmentRow> = [
    { title: "指标", dataIndex: "metricName", key: "metricName" },
    { title: "本次结果", dataIndex: "actualText", key: "actualText" },
    { title: "参考结果", dataIndex: "referenceText", key: "referenceText" },
    { title: "比较规则", dataIndex: "ruleText", key: "ruleText" },
    { title: "判断", dataIndex: "judgmentText", key: "judgmentText" },
  ];

  return (
    <section
      data-testid="cs-reproduction-comparison-judgment"
      className="grid gap-5"
    >
      <div className="grid gap-2">
        <Typography.Title level={4} className="!mb-0">
          结果比较与达标判断
        </Typography.Title>
        <Typography.Paragraph className="!mb-0 text-sm text-text-secondary">
          先确认比较条件，再查看各指标与既定规则的对照结果。
        </Typography.Paragraph>
      </div>

      <section data-testid="cs-comparison-comparability" className="grid gap-3">
        <Typography.Text strong>比较条件</Typography.Text>
        {comparability.statusLabel ? (
          <Tag className="m-0 w-fit">{comparability.statusLabel}</Tag>
        ) : (
          <FailClosedNotice part="比较条件状态" />
        )}
        {hasConditionMismatchRecords ? (
          <Typography.Paragraph className="!mb-0 text-sm text-text-secondary">
            存在条件差异记录；具体条件状态见下方比较条件列表。
          </Typography.Paragraph>
        ) : null}
        {comparability.conditions ? (
          <div className="grid gap-2 sm:grid-cols-2">
            {comparability.conditions.map((condition) => (
              <div
                key={condition.key}
                className="rounded-md border border-border bg-surface px-3 py-2"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-text">
                    {condition.label}
                  </span>
                  <Tag className="m-0">{condition.statusLabel}</Tag>
                </div>
                {condition.note ? (
                  <div className="mt-1 text-xs text-text-secondary">
                    {condition.note}
                  </div>
                ) : null}
                <div className="mt-1 text-xs text-text-muted">
                  依据 {condition.evidenceCount} 条
                </div>
              </div>
            ))}
          </div>
        ) : comparability.statusLabel ? (
          <FailClosedNotice part="比较条件明细" />
        ) : null}
      </section>

      <section data-testid="cs-comparison-metric-table" className="grid gap-3">
        <Typography.Text strong>逐指标比较</Typography.Text>
        {judgmentRows ? (
          <>
            <Table<JudgmentRow>
              size="small"
              pagination={false}
              rowKey="key"
              columns={columns}
              dataSource={judgmentRows}
            />
            {judgmentRows.some((row) => row.deltaText) ? (
              <ul className="m-0 grid gap-1 pl-5 text-sm text-text-secondary">
                {judgmentRows
                  .filter((row) => row.deltaText)
                  .map((row) => (
                    <li key={`delta-${row.key}`}>
                      {row.metricName}：差值（正式计算结果）{row.deltaText}
                    </li>
                  ))}
              </ul>
            ) : null}
          </>
        ) : (
          <FailClosedNotice part="逐指标比较" />
        )}
      </section>

      <section data-testid="cs-comparison-verdict" className="grid gap-2">
        <Typography.Text strong>达标状态</Typography.Text>
        {verdictLabel ? (
          <Tag className="m-0 w-fit" data-testid="cs-comparison-overall-verdict">
            {verdictLabel}
          </Tag>
        ) : (
          <FailClosedNotice part="达标状态" />
        )}
      </section>

      <section data-testid="cs-comparison-limits" className="grid gap-2">
        <Typography.Text strong>限制 / 未知</Typography.Text>
        {visibleUnknowns.length > 0 ? (
          <ul className="m-0 grid gap-1 pl-5 text-sm text-text-secondary">
            {visibleUnknowns.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        ) : (
          <Typography.Paragraph className="!mb-0 text-sm text-text-secondary">
            当前没有可直接展示的未知说明。
          </Typography.Paragraph>
        )}
      </section>

      <Collapse
        items={[
          {
            key: "details",
            label: "技术详情与核查信息",
            children: (
              <div className="grid gap-4">
                <DetailBlock title="比较条件依据">
                  {comparability.conditions?.map((condition) => (
                    <Kv
                      key={`ev-${condition.key}`}
                      label={condition.label}
                      value={`状态 ${condition.statusLabel}；依据 ${condition.evidenceCount} 条`}
                    />
                  ))}
                  {hasConditionMismatchRecords ? (
                    <div>
                      条件差异原始记录数：{document.condition_mismatches && Array.isArray(document.condition_mismatches) ? document.condition_mismatches.length : 0}
                      （不解释对象字段语义）
                    </div>
                  ) : null}
                  {evidenceRefs.length > 0 ? (
                    <div>证据引用数：{evidenceRefs.length}</div>
                  ) : null}
                </DetailBlock>

                <DetailBlock title="完整逐指标计算">
                  {judgmentRows?.map((row) => (
                    <div
                      key={`calc-${row.key}`}
                      className="rounded-md bg-surface-soft px-3 py-2"
                    >
                      <div className="font-medium text-text">{row.metricName}</div>
                      <Kv label="判断" value={row.judgmentText} />
                      <Kv label="规则" value={row.ruleText} />
                      <Kv label="差值（正式计算结果）" value={row.deltaText} />
                      <Kv label="失败说明" value={row.failureText} />
                      {row.calculationTrace ? (
                        <pre className="m-0 mt-2 overflow-auto whitespace-pre-wrap text-xs text-text-muted">
                          {JSON.stringify(row.calculationTrace, null, 2)}
                        </pre>
                      ) : null}
                    </div>
                  ))}
                </DetailBlock>

                <DetailBlock title="技术规则记录">
                  {ruleApplicationRows?.map((row) => (
                    <Kv key={row.label} label={row.label} value={row.value} />
                  ))}
                </DetailBlock>

                {detailOnlyUnknowns.length > 0 ? (
                  <DetailBlock title="原始未知码">
                    {detailOnlyUnknowns.map((item) => (
                      <div key={item}>{item}</div>
                    ))}
                  </DetailBlock>
                ) : null}
              </div>
            ),
          },
        ]}
      />
    </section>
  );
}
