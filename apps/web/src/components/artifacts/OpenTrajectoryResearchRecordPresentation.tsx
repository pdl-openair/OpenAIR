import { Tag, Typography } from "antd";
import { DataChartComparison } from "./DataChartComparison";
import { DataChartContext } from "./DataChartContext";
import { DataChartTrend } from "./DataChartTrend";
import { researchRecordAnchorId } from "./researchRecordDataChartProjection";

type JsonObject = Record<string, unknown>;

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
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  return "";
}

function readableEnum(value: unknown): string {
  const raw = scalar(value);
  const labels: Record<string, string> = {
    material_composition: "材料组成",
    experimental_condition: "实验条件",
    quantitative_result: "定量结果",
    characterization_observation: "表征观察",
    computation_result: "计算结果",
    comparison: "对比证据",
    mechanism_statement: "机制陈述",
    limitation: "局限性",
    qualitative_result: "定性结果",
    direct_source_fact: "原文事实",
    author_interpretation: "作者解释",
    other_source_information: "其他来源信息",
    direct_observation: "直接观察",
    association: "相关关系",
    causal_claim: "因果主张",
    source_statement: "来源陈述",
  };
  return labels[raw] ?? raw.replace(/_/g, " ");
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(scalar).filter(Boolean) : [];
}

function identifierFor(source: JsonObject | null): string {
  if (!source) return "";
  const doi = scalar(source.doi);
  if (doi) return `DOI ${doi.replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "")}`;
  const arxiv = scalar(source.arxiv_id);
  return arxiv ? `arXiv ${arxiv}` : "";
}

function conditionSummary(value: unknown): string {
  return asObjectArray(value)
    .map((item) => {
      const name = scalar(item.name);
      const status = scalar(item.status);
      if (status === "unknown") {
        return name ? `${name}：未报告` : "未报告";
      }
      const measurement = [scalar(item.value_text), scalar(item.unit)]
        .filter(Boolean)
        .join(" ");
      return [name, measurement].filter(Boolean).join("：");
    })
    .filter(Boolean)
    .join("；");
}

function conditionStatusSummary(value: unknown): string {
  const status = scalar(value);
  if (status === "partially_unknown") return "条件信息：部分未报告";
  if (status === "not_applicable") return "条件信息：不适用";
  return "";
}

function resultValueSummary(result: JsonObject | null): string {
  if (!result) return "";
  return asObjectArray(result.values)
    .map((item) => {
      const name = scalar(item.name);
      const measurement = [scalar(item.value_text), scalar(item.unit)]
        .filter(Boolean)
        .join(" ");
      return [name, measurement].filter(Boolean).join("：");
    })
    .filter(Boolean)
    .join("；");
}

function sourceSummary(source: JsonObject | null): string {
  if (!source) return "";
  return [
    scalar(source.title),
    identifierFor(source),
    scalar(source.section_name),
  ]
    .filter(Boolean)
    .join(" · ");
}

export function OpenTrajectoryResearchRecordPresentation({
  document,
}: {
  document: JsonObject;
}) {
  const rows = asObjectArray(document.research_records);
  if (rows.length === 0) {
    return (
      <Typography.Text type="secondary" data-testid="ot-scientific-empty">
        当前研究记录产物暂无可呈现的来源记录。
      </Typography.Text>
    );
  }

  return (
    <section data-testid="ot-research-record-view" className="min-w-0">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <Typography.Title level={4} className="!mb-0">
          来源研究记录
        </Typography.Title>
        <Typography.Text type="secondary">{rows.length} 条</Typography.Text>
      </div>
      <DataChartTrend records={rows} />
      <DataChartComparison records={rows} />
      <DataChartContext records={rows} />
      <div className="grid gap-3">
        {rows.map((row, index) => {
          const result = asObject(row.result);
          const source = asObject(row.source);
          const object = scalar(row.research_object);
          const statement = scalar(result?.statement);
          const sourceText = sourceSummary(source);
          const sourceLocator = scalar(source?.source_locator);
          const conditions = conditionSummary(row.conditions);
          const conditionStatus = conditionStatusSummary(row.condition_status);
          const values = resultValueSummary(result);
          const uncertainty = scalar(row.uncertainty);
          const unknownFields = stringArray(row.unknown_fields);

          return (
            <article
              id={researchRecordAnchorId(scalar(row.record_id), index)}
              key={`${object}-${index}`}
              className="rounded-lg border border-border bg-surface p-4 shadow-sm"
            >
              <Typography.Text type="secondary" className="block text-xs">
                {object || "未命名研究对象"}
              </Typography.Text>
              <Typography.Text
                strong
                className="mt-1 block text-[15px] leading-6"
              >
                {statement || "未提供结果描述"}
              </Typography.Text>
              {values ? (
                <Typography.Paragraph className="!mb-1 !mt-2">
                  <strong>报告结果：</strong>
                  {values}
                </Typography.Paragraph>
              ) : null}
              {conditions ? (
                <Typography.Paragraph className="!mb-1">
                  <strong>研究条件：</strong>
                  {conditions}
                </Typography.Paragraph>
              ) : null}
              {scalar(row.source_quote) ? (
                <Typography.Paragraph className="!mb-2 !mt-2 border-l-2 border-border pl-3 text-text-secondary">
                  {scalar(row.source_quote)}
                </Typography.Paragraph>
              ) : null}
              {sourceText ? (
                <Typography.Paragraph className="!mb-2 text-text-secondary">
                  <strong>来源：</strong>
                  {sourceLocator.startsWith("http") ? (
                    <Typography.Link
                      href={sourceLocator}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {sourceText}
                    </Typography.Link>
                  ) : (
                    sourceText
                  )}
                </Typography.Paragraph>
              ) : null}
              <div className="flex flex-wrap gap-1.5">
                {readableEnum(row.record_type) ? (
                  <Tag>{readableEnum(row.record_type)}</Tag>
                ) : null}
                {readableEnum(row.information_identity) ? (
                  <Tag>{readableEnum(row.information_identity)}</Tag>
                ) : null}
                {readableEnum(row.assertion_mode) ? (
                  <Tag>{readableEnum(row.assertion_mode)}</Tag>
                ) : null}
                {conditionStatus ? <Tag>{conditionStatus}</Tag> : null}
                {uncertainty ? <Tag>不确定性：{uncertainty}</Tag> : null}
                {unknownFields.length ? (
                  <Tag>未报告 / 未确认：{unknownFields.join("、")}</Tag>
                ) : null}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
