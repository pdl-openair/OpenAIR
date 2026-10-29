import { projectResearchRecordComparison } from "./researchRecordComparisonProjection";

type JsonObject = Record<string, unknown>;

function comparisonSummary(
  input: ReturnType<typeof projectResearchRecordComparison>,
): string {
  if (!input) return "";
  if (input.difference === 0) return `各对象数值相同 · 差值 0 ${input.unit}`;
  return [
    `最高：${input.highestObjects.join("、")} ${input.maximum} ${input.unit}`,
    `最低：${input.lowestObjects.join("、")} ${input.minimum} ${input.unit}`,
    `相差 ${input.differenceLabel} ${input.unit}`,
  ].join(" · ");
}

export function DataChartComparison({ records }: { records: JsonObject[] }) {
  const comparison = projectResearchRecordComparison(records);
  if (!comparison) return null;

  const domainMinimum = Math.min(0, comparison.minimum);
  const domainMaximum = Math.max(0, comparison.maximum);
  const domainSpan = domainMaximum - domainMinimum;
  const position = (value: number) =>
    domainSpan === 0 ? 0 : ((value - domainMinimum) / domainSpan) * 100;
  const zeroPosition = position(0);

  return (
    <section
      className="mb-5 rounded-xl border border-border bg-surface-soft p-4 shadow-sm"
      data-testid="data-chart-comparison"
      aria-label={`${comparison.metric} 对比图`}
    >
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="m-0 text-[16px] font-semibold text-text">
          {comparison.metric} 对比
        </h4>
        <span className="rounded-md bg-surface px-2 py-0.5 text-xs font-medium text-text-secondary">
          {comparison.unit}
        </span>
      </div>
      <p
        className="mb-4 mt-0 text-xs leading-5 text-text-secondary"
        data-testid="comparison-summary"
      >
        {comparisonSummary(comparison)}
      </p>

      <div className="grid gap-3">
        {comparison.rows.map((row) => {
          const valuePosition = position(row.value);
          const barLeft = Math.min(zeroPosition, valuePosition);
          const barWidth = Math.abs(valuePosition - zeroPosition);
          return (
            <div
              key={row.recordId}
              className="grid items-center gap-x-3 gap-y-1 sm:grid-cols-[minmax(120px,0.9fr)_minmax(160px,2fr)_auto]"
              data-testid="comparison-row"
            >
              <div className="min-w-0">
                <div
                  className="truncate text-sm font-medium text-text"
                  title={row.objectLabel}
                >
                  {row.objectLabel}
                </div>
                <a
                  className="block truncate text-xs"
                  href={`#${row.anchorId}`}
                  title={row.sourceLabel}
                >
                  查看来源：{row.sourceLabel}
                </a>
              </div>
              <div
                className="relative h-7 overflow-hidden rounded-md bg-surface"
                aria-label={`${row.objectLabel}：${row.reportedValue} ${comparison.unit}`}
              >
                <span
                  aria-hidden="true"
                  className="absolute inset-y-0 w-px bg-border-strong"
                  style={{ left: `${zeroPosition}%` }}
                />
                <span
                  aria-hidden="true"
                  className={`absolute inset-y-1 rounded-sm ${row.value < 0 ? "bg-warning" : "bg-brand"}`}
                  style={{
                    left: `${barLeft}%`,
                    width: `${barWidth}%`,
                    minWidth: row.value === 0 ? 2 : undefined,
                  }}
                />
              </div>
              <div className="whitespace-nowrap text-right font-mono text-sm font-semibold text-text">
                {row.reportedValue}{" "}
                <span className="font-sans text-xs font-normal text-text-secondary">
                  {comparison.unit}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-4 border-t border-border pt-3 text-xs text-text-secondary">
        {comparison.conditionLabel} · {comparison.rows.length} 条 ResearchRecord
        · 每行可查看来源
      </div>
    </section>
  );
}
