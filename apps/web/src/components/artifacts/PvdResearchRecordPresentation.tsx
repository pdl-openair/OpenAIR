import {
  asObject,
  text,
  type JsonObject,
} from "./researchRecordDataChartProjection";
import {
  projectPvdReportedValues,
  pvdObjects,
  pvdRecordAnchor,
  pvdRecordLabel,
  pvdRecords,
  pvdSourceFor,
  pvdSourceLabel,
  type PvdReportedValueGroup,
} from "./pvdReportedValueProjection";

import { PVD_FIELD_LABELS as FIELD_LABELS } from "./pvdRecordFieldLabels";
import { PvdExperimentTrends } from "./PvdExperimentTrends";
import { Table } from "antd";
import { researchPresentationFieldLabel } from "@openair/shared";
import { ScientificResultDisplayValue } from "./scientificResultDisplayValue";
const FACT_LABELS: Record<string, string> = {
  not_reported: "未报告",
  unknown: "未知",
  unavailable: "不可获得",
  ambiguous: "不明确",
  not_applicable: "不适用",
};

function displayedValue(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function factLabel(value: unknown): string {
  const fact = asObject(value);
  if (!fact) return displayedValue(value);
  const status = text(fact.fact_status);
  const core =
    status === "known"
      ? [displayedValue(fact.value), text(fact.unit)].filter(Boolean).join(" ")
      : FACT_LABELS[status] || status;
  return [
    core,
    text(fact.definition),
    text(fact.qualifier),
    fact.uncertainty != null
      ? `不确定性：${displayedValue(fact.uncertainty)}`
      : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

function isKnownFact(fact: unknown): boolean {
  return text(asObject(fact)?.fact_status) === "known";
}

function factKeyLabel(key: string): string {
  // Co-located PVD label map first, then the shared dictionary; an unmapped
  // key is shown verbatim rather than masked as a misleading "补充条件".
  return FIELD_LABELS[key] || researchPresentationFieldLabel(key) || key;
}

function FactSection({ title, value }: { title: string; value: unknown }) {
  const section = asObject(value);
  if (!section) return null;
  const entries = Object.entries(section).filter(
    ([key]) => key !== "other_conditions",
  );
  const extra = pvdObjects(section.other_conditions);
  // Split canonical facts: known values render as rows; fail-closed unknowns
  // (not_reported / unknown / unavailable / ambiguous / not_applicable) fold
  // into a single expandable count instead of a wall of "未知" rows.
  const known = entries.filter(([, fact]) => isKnownFact(fact));
  const unsettled = entries.filter(([, fact]) => !isKnownFact(fact));
  if (!known.length && !unsettled.length && !extra.length) return null;
  return (
    <div className="mt-3">
      <h5 className="mb-1 mt-0 font-medium">{title}</h5>
      <dl className="m-0 grid gap-1 text-sm text-text-secondary">
        {known.map(([key, fact]) => (
          <div key={key}>
            <dt className="inline">{factKeyLabel(key)}：</dt>
            <dd className="m-0 inline">{factLabel(fact)}</dd>
          </div>
        ))}
        {extra.map((entry, index) => (
          <div key={index}>
            <dt className="inline">{text(entry.key)}：</dt>
            <dd className="m-0 inline">{factLabel(entry.fact)}</dd>
          </div>
        ))}
      </dl>
      {unsettled.length > 0 ? (
        <details className="mt-1 text-sm text-text-muted">
          <summary className="cursor-pointer">
            未报道或未确定字段：{unsettled.length} 项
          </summary>
          <dl className="m-0 mt-1 grid gap-1">
            {unsettled.map(([key, fact]) => (
              <div key={key}>
                <dt className="inline">{factKeyLabel(key)}：</dt>
                <dd className="m-0 inline">{factLabel(fact)}</dd>
              </div>
            ))}
          </dl>
        </details>
      ) : null}
    </div>
  );
}

function OutcomeDetails({ outcome }: { outcome: JsonObject }) {
  const fields = Object.keys(FIELD_LABELS).filter((key) => key in outcome);
  return (
    <div className="text-xs leading-5 text-text-secondary">
      {text(outcome.definition) ? <div>{text(outcome.definition)}</div> : null}
      {fields.map((key) => (
        <div key={key}>
          {FIELD_LABELS[key]}：{factLabel(outcome[key])}
        </div>
      ))}
      {outcome.uncertainty != null ? (
        <div>不确定性：{displayedValue(outcome.uncertainty)}</div>
      ) : null}
    </div>
  );
}

function ReportedValueChart({ group }: { group: PvdReportedValueGroup }) {
  const scale = Math.max(...group.rows.map((row) => Math.abs(row.value))) || 1;
  const minimum = Math.min(0, ...group.rows.map((row) => row.value / scale));
  const maximum = Math.max(0, ...group.rows.map((row) => row.value / scale));
  const span = maximum - minimum;
  const position = (value: number) =>
    span === 0 ? 0 : ((value / scale - minimum) / span) * 100;
  const zero = position(0);
  return (
    <section
      className="mb-5 rounded-xl border border-border bg-surface-soft p-4"
      data-testid="pvd-reported-values-chart"
      aria-label={`${group.metric} 原文报告值（${group.unit}）`}
    >
      <h4 className="mb-3 mt-0 text-base font-semibold">
        已报告 {group.metric}{" "}
        <span className="font-normal">({group.unit})</span>
      </h4>
      <div className="grid gap-3">
        {group.rows.map((row) => (
          <div key={row.key} className="rounded-lg bg-surface p-3">
            <div className="mb-2 flex flex-wrap justify-between gap-2 text-sm">
              <a href={`#${row.anchorId}`} title={row.sourceLabel}>
                {row.label} · 查看条件与来源
              </a>
              <span className="font-mono">
                {row.convertedLabel ?? row.reportedValue} {group.unit}
              </span>
            </div>
            <div
              className="relative mb-2 h-3 rounded bg-surface-soft"
              aria-hidden="true"
            >
              <div
                className="absolute h-full rounded bg-brand"
                style={{
                  left: `${Math.min(zero, position(row.value))}%`,
                  width: `${Math.abs(position(row.value) - zero)}%`,
                }}
              />
              <div
                className="absolute top-0 h-full border-l border-text-secondary"
                style={{ left: `${zero}%` }}
              />
            </div>
            <OutcomeDetails outcome={row.outcome} />
            {row.convertedLabel !== null ? (
              <div className="text-xs text-text-secondary">
                原文：{row.reportedValue} {row.reportedUnit}；换算：
                {row.convertedLabel} {group.unit}
              </div>
            ) : null}
            <div className="mt-1 text-xs text-text-secondary">
              {row.sourceLabel}
            </div>
            <details className="mt-2 text-xs text-text-secondary">
              <summary className="cursor-pointer">材料与工艺条件</summary>
              <FactSection title="材料与样品" value={row.record.material} />
              <FactSection title="工艺条件" value={row.record.process} />
            </details>
          </div>
        ))}
      </div>
      <p className="mb-0 mt-3 text-xs leading-5 text-text-secondary">
        数值来自各自来源记录；材料、工艺或测量条件可能不同。此图不表示各组严格可比，也不据此判断工艺优劣或因果关系。
      </p>
    </section>
  );
}

function SourceDetails({ source }: { source: JsonObject | null }) {
  const label = pvdSourceLabel(source);
  const url = text(source?.url);
  const doi = text(source?.doi);
  const href = /^https?:\/\//i.test(url)
    ? url
    : doi
      ? `https://doi.org/${doi.replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "")}`
      : "";
  return (
    <div className="mt-3 text-sm text-text-secondary">
      来源：
      {href ? (
        <a href={href} target="_blank" rel="noreferrer">
          {label}
        </a>
      ) : (
        label || "来源未确认"
      )}
      {doi ? <div>DOI：{doi}</div> : null}
      {text(source?.source_location) ? (
        <div>{text(source?.source_location)}</div>
      ) : null}
      {text(source?.notes) ? <div>{text(source?.notes)}</div> : null}
      {source?.access_state === "partial" ? <div>来源仅部分可读</div> : null}
      {source?.access_state === "unverified" ? <div>来源尚未核验</div> : null}
      {source?.access_state === "unavailable" ? (
        <div>来源当前不可访问</div>
      ) : null}
    </div>
  );
}

export function PvdResearchRecordPresentation({
  document,
}: {
  document: JsonObject;
}) {
  const records = pvdRecords(document);
  const groups = projectPvdReportedValues(document);
  const coverage = asObject(document.coverage);
  const gaps = pvdObjects(document.unresolved_gaps);
  const packet = asObject(document.evidence_packet);
  const evidence = pvdObjects(packet?.evidence_items).filter(
    (item) => text(item.evidence_id) && text(item.source_anchor_id),
  );
  return (
    <section data-testid="pvd-research-record-view" className="min-w-0">
      <h4 className="mb-3 mt-0 text-base font-semibold">文献研究记录</h4>
      <PvdExperimentTrends document={document} />
      {groups.map((group) => (
        <ReportedValueChart key={group.key} group={group} />
      ))}
      {records.length === 0 && evidence.length > 0 ? (
        <section
          className="mb-5 rounded-lg border border-border p-4"
          data-testid="pvd-verified-evidence"
        >
          <h3 className="m-0 mb-2 text-base font-semibold">
            已核验文献证据 · {evidence.length} 条
          </h3>
          <p className="text-sm text-text-secondary">
            已有证据可阅读；尚未形成正式研究记录，以下内容不作为实验记录或可比工艺窗口。
          </p>
          <Table<JsonObject>
            size="small"
            rowKey={(item) => text(item.evidence_id)}
            dataSource={evidence}
            pagination={{
              pageSize: 6,
              showSizeChanger: false,
              showTotal: (total) => `共 ${total} 条证据`,
            }}
            columns={[
              {
                title: "研究发现",
                key: "claim",
                render: (_, item) => text(item.claim),
              },
              {
                title: "报告条件与结果",
                key: "conditions",
                render: (_, item) => (
                  <div className="grid gap-1 text-sm">
                    {pvdObjects(item.conditions).map((condition, index) => (
                      <div key={index}>
                        {FIELD_LABELS[text(condition.name)] ||
                          text(condition.name)}
                        ：{text(condition.value_text)} {text(condition.unit)}
                      </div>
                    ))}
                    {asObject(item.metric) ? (
                      <div>
                        {text(asObject(item.metric)?.name)}：
                        {text(asObject(item.metric)?.value_text)}{" "}
                        {text(asObject(item.metric)?.unit)}
                      </div>
                    ) : null}
                  </div>
                ),
              },
              {
                title: "原始来源",
                key: "source",
                render: (_, item) => (
                  <div>
                    {text(item.arxiv_id) ? (
                      <a
                        href={`https://arxiv.org/abs/${encodeURIComponent(text(item.arxiv_id))}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        arXiv {text(item.arxiv_id)}
                      </a>
                    ) : null}
                    <div>{text(item.section_name)}</div>
                  </div>
                ),
              },
            ]}
            expandable={{
              expandedRowRender: (item) => (
                <div className="grid gap-3 text-sm">
                  <div>
                    <strong>原文依据</strong>
                    <blockquote className="m-0 mt-2 whitespace-pre-wrap border-l-2 border-border pl-3">
                      {text(item.evidence_quote)}
                    </blockquote>
                  </div>
                  {item.qualifiers ? (
                    <ScientificResultDisplayValue
                      value={{ 限定条件: item.qualifiers }}
                    />
                  ) : null}
                </div>
              ),
              expandRowByClick: true,
            }}
          />
        </section>
      ) : null}
      <div className="grid gap-3">
        {records.map((record, index) => (
          <article
            id={pvdRecordAnchor(record, index)}
            key={index}
            className="rounded-xl border border-border bg-surface p-4"
          >
            <h4 className="m-0 font-semibold">
              {pvdRecordLabel(record, index)}
            </h4>
            <FactSection title="材料与样品" value={record.material} />
            <FactSection title="工艺条件" value={record.process} />
            {pvdObjects(record.outcomes).map((outcome, outcomeIndex) => (
              <div key={outcomeIndex} className="mt-3">
                <div className="text-sm font-medium">
                  {text(outcome.metric)}：{displayedValue(outcome.value)}{" "}
                  {text(outcome.unit)}
                </div>
                <OutcomeDetails outcome={outcome} />
              </div>
            ))}
            <FactSection
              title="其他条件"
              value={{ other_conditions: record.other_context }}
            />
            <SourceDetails source={pvdSourceFor(record, document)} />
          </article>
        ))}
      </div>
      {coverage ? (
        <div className="mt-4 text-sm">
          证据覆盖：
          {coverage.decision_coverage_sufficient === true
            ? "当前决策所需证据已覆盖"
            : coverage.decision_coverage_sufficient === false
              ? "当前决策所需证据尚未充分覆盖"
              : "未知"}
        </div>
      ) : null}
      {gaps.length ? (
        <ul className="mt-3 text-sm">
          {gaps.map((gap, index) => (
            <li key={index}>{text(gap.description)}</li>
          ))}
        </ul>
      ) : null}
      <details className="mt-4 text-sm text-text-secondary">
        <summary className="cursor-pointer">查看研究记录与依据详情</summary>
        <ScientificResultDisplayValue
          value={{
            research_records: records,
            source_provenance: document.source_provenance,
            coverage,
          }}
        />
      </details>
    </section>
  );
}
