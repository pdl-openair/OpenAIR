import { Tag, Typography } from "antd";

type JsonObject = Record<string, unknown>;

function asObject(value: unknown): JsonObject | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : null;
}

function asObjectArray(value: unknown): JsonObject[] {
  return Array.isArray(value) ? value.map(asObject).filter((item): item is JsonObject => Boolean(item)) : [];
}

function scalar(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function numeric(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(scalar).filter(Boolean) : [];
}

function confidenceLabel(value: unknown): string {
  const score = numeric(value);
  return score === null ? "" : `置信度 ${(score * 100).toFixed(0)}%`;
}

function epistemicLabel(value: unknown): string {
  const labels: Record<string, string> = {
    supported: "证据支持",
    uncertain: "仍有不确定性",
    conflicting: "存在冲突",
    boundary_limited: "受条件限制",
  };
  const raw = scalar(value);
  return labels[raw] ?? raw.replace(/_/g, " ");
}

function ConclusionSection({ rows }: { rows: JsonObject[] }) {
  if (!rows.length) return null;
  return (
    <section>
      <Typography.Title level={4} className="!mb-3">证据支持的关键结论</Typography.Title>
      <div className="grid gap-3">
        {rows.map((row, index) => {
          const rowUncertainties = stringArray(row.uncertainties);
          const recordCount = stringArray(row.supporting_record_ids).length;
          return (
            <article key={`${scalar(row.statement)}-${index}`} className="rounded-lg border border-slate-200 bg-white p-4">
              <div className="mb-2 flex flex-wrap gap-1.5">
                {epistemicLabel(row.epistemic_status) ? <Tag>{epistemicLabel(row.epistemic_status)}</Tag> : null}
                {confidenceLabel(row.confidence) ? <Tag>{confidenceLabel(row.confidence)}</Tag> : null}
                {recordCount > 0 ? <Tag>{recordCount} 条研究记录</Tag> : null}
              </div>
              <Typography.Text strong className="block text-[15px] leading-6">{scalar(row.statement)}</Typography.Text>
              {scalar(row.evidence_basis) ? <Typography.Paragraph className="!mb-1 !mt-2"><strong>依据：</strong>{scalar(row.evidence_basis)}</Typography.Paragraph> : null}
              {scalar(row.scope_boundary) ? <Typography.Paragraph className="!mb-1"><strong>适用边界：</strong>{scalar(row.scope_boundary)}</Typography.Paragraph> : null}
              {rowUncertainties.length ? <Typography.Paragraph className="!mb-0 text-slate-600"><strong>不确定性：</strong>{rowUncertainties.join("；")}</Typography.Paragraph> : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function ConflictAndGapSections({ document }: { document: JsonObject }) {
  const conflicts = asObjectArray(document.conflicts);
  const uncertainties = stringArray(document.uncertainties);
  const gaps = asObjectArray(document.evidence_gaps);
  return (
    <>
      {conflicts.length ? (
        <section data-testid="ot-final-conflicts">
          <Typography.Title level={4} className="!mb-3">证据冲突</Typography.Title>
          <div className="grid gap-2">
            {conflicts.map((row, index) => (
              <article key={`${scalar(row.statement)}-${index}`} className="rounded-lg border border-slate-200 bg-white p-4">
                <Typography.Text strong>{scalar(row.statement)}</Typography.Text>
                {scalar(row.implication) ? <Typography.Paragraph className="!mb-0 !mt-1 text-slate-600"><strong>意味着：</strong>{scalar(row.implication)}</Typography.Paragraph> : null}
              </article>
            ))}
          </div>
        </section>
      ) : null}
      {(uncertainties.length || gaps.length) ? (
        <section data-testid="ot-final-uncertainties-gaps">
          <Typography.Title level={4} className="!mb-3">当前未知与证据缺口</Typography.Title>
          {uncertainties.length ? (
            <ul className="mb-3 list-disc space-y-1 pl-5">
              {uncertainties.map((item) => <li key={item}>{item}</li>)}
            </ul>
          ) : null}
          <div className="grid gap-2">
            {gaps.map((row, index) => (
              <article key={`${scalar(row.gap)}-${index}`} className="rounded-lg border border-slate-200 bg-white p-3">
                <Typography.Text strong>{scalar(row.gap)}</Typography.Text>
                {scalar(row.impact) ? <Typography.Paragraph className="!mb-0 !mt-1 text-slate-600"><strong>影响：</strong>{scalar(row.impact)}</Typography.Paragraph> : null}
              </article>
            ))}
          </div>
        </section>
      ) : null}
    </>
  );
}

function NextResearchSection({ document }: { document: JsonObject }) {
  const leads = asObjectArray(document.research_leads);
  const hypotheses = asObjectArray(document.hypotheses);
  return (
    <>
      {leads.length ? (
        <section data-testid="ot-final-research-leads">
          <Typography.Title level={4} className="!mb-3">下一步研究方向</Typography.Title>
          <div className="grid gap-2">
            {leads.map((row, index) => (
              <article key={`${scalar(row.title)}-${index}`} className="rounded-lg border border-slate-200 bg-white p-4">
                <Typography.Text strong>{scalar(row.title) || `研究方向 ${index + 1}`}</Typography.Text>
                {scalar(row.research_question) ? <Typography.Paragraph className="!mb-1 !mt-1">{scalar(row.research_question)}</Typography.Paragraph> : null}
                {scalar(row.rationale) ? <Typography.Paragraph className="!mb-0 text-slate-600"><strong>为什么值得继续：</strong>{scalar(row.rationale)}</Typography.Paragraph> : null}
              </article>
            ))}
          </div>
        </section>
      ) : null}
      {hypotheses.length ? (
        <section data-testid="ot-final-hypotheses">
          <Typography.Title level={4} className="!mb-1">待验证假设</Typography.Title>
          <Typography.Paragraph type="secondary" className="!mb-3">以下内容由现有证据推导，尚不是已经建立的事实。</Typography.Paragraph>
          <div className="grid gap-2">
            {hypotheses.map((row, index) => {
              const falsification = stringArray(row.falsification_conditions);
              return (
                <article key={`${scalar(row.title)}-${index}`} className="rounded-lg border border-slate-200 bg-white p-4">
                  <Typography.Text strong>{scalar(row.title) || `假设 ${index + 1}`}</Typography.Text>
                  {scalar(row.statement) ? <Typography.Paragraph className="!mb-1 !mt-1">{scalar(row.statement)}</Typography.Paragraph> : null}
                  {scalar(row.falsifiable_prediction) ? <Typography.Paragraph className="!mb-1 text-slate-600"><strong>可检验预测：</strong>{scalar(row.falsifiable_prediction)}</Typography.Paragraph> : null}
                  {falsification.length ? <Typography.Paragraph className="!mb-0 text-slate-600"><strong>什么结果会推翻它：</strong>{falsification.join("；")}</Typography.Paragraph> : null}
                </article>
              );
            })}
          </div>
        </section>
      ) : null}
    </>
  );
}

export function OpenTrajectoryFinalSynthesisPresentation({ document }: { document: JsonObject }) {
  const directAnswer = asObject(document.direct_answer);
  const answer = scalar(directAnswer?.answer);
  const caveats = stringArray(directAnswer?.caveats);
  const supportingRecordCount = stringArray(directAnswer?.supporting_record_ids).length;

  return (
    <section data-testid="ot-final-synthesis-view" className="min-w-0 space-y-5">
      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
        <Typography.Text type="secondary" className="block text-xs">研究问题的当前直接回答</Typography.Text>
        <Typography.Title level={3} className="!mb-2 !mt-1">{answer || "当前尚不能形成直接回答"}</Typography.Title>
        <div className="flex flex-wrap gap-1.5">
          {confidenceLabel(directAnswer?.confidence) ? <Tag>{confidenceLabel(directAnswer?.confidence)}</Tag> : null}
          {supportingRecordCount > 0 ? <Tag>依据 {supportingRecordCount} 条来源研究记录</Tag> : null}
        </div>
        {caveats.length ? (
          <div className="mt-3">
            <Typography.Text strong>适用提醒</Typography.Text>
            <ul className="mb-0 mt-1 list-disc space-y-1 pl-5 text-slate-600">
              {caveats.map((item) => <li key={item}>{item}</li>)}
            </ul>
          </div>
        ) : null}
      </section>

      <ConclusionSection rows={asObjectArray(document.evidence_supported_conclusions)} />
      <ConflictAndGapSections document={document} />
      <NextResearchSection document={document} />
    </section>
  );
}
