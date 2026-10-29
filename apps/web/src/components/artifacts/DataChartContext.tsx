import { projectResearchRecordContext } from "./researchRecordContextProjection";

type JsonObject = Record<string, unknown>;

function reportedCondition(value: string, unit: string): string {
  return `${value}${unit ? ` ${unit}` : ""}`;
}

export function DataChartContext({ records }: { records: JsonObject[] }) {
  const context = projectResearchRecordContext(records);
  if (!context) return null;

  return (
    <section
      className="mb-5 rounded-xl border border-warning/40 bg-surface-soft p-4 shadow-sm"
      data-testid="data-chart-context"
      aria-label={`${context.metric} 条件差异下的已报告数值`}
    >
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="m-0 text-[16px] font-semibold text-text">
          已报告 {context.metric}
        </h4>
        <span className="rounded-md bg-surface px-2 py-0.5 text-xs font-medium text-text-secondary">
          {context.unit}
        </span>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {context.rows.map((row) => (
          <div
            key={row.recordId}
            className="rounded-lg border border-border bg-surface p-3"
            data-testid="context-value-row"
          >
            <div className="text-sm font-medium text-text">
              {row.objectLabel}
            </div>
            <div className="mt-1 font-mono text-xl font-semibold text-text">
              {row.reportedValue}{" "}
              <span className="font-sans text-sm font-normal text-text-secondary">
                {context.unit}
              </span>
            </div>
            {row.uncertaintyLabel ? (
              <p className="mb-0 mt-2 text-xs leading-5 text-text-secondary">
                不确定性：{row.uncertaintyLabel}
              </p>
            ) : null}
            <a
              className="mt-2 block truncate text-xs"
              href={`#${row.anchorId}`}
              title={row.sourceLabel}
            >
              查看来源：{row.sourceLabel}
            </a>
          </div>
        ))}
      </div>

      {context.commonConditions.length ? (
        <p className="mb-3 mt-4 text-xs leading-5 text-text-secondary">
          共同条件：
          {context.commonConditions
            .map(
              ({ name, value, unit }) =>
                `${name} ${reportedCondition(value, unit)}`,
            )
            .join(" · ")}
        </p>
      ) : null}

      <div className="mt-4 overflow-x-auto">
        <div className="mb-2 text-sm font-semibold text-text">条件差异</div>
        <table className="w-full min-w-[420px] border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-border text-xs text-text-secondary">
              <th className="px-2 py-2 font-medium">条件</th>
              {context.rows.map((row) => (
                <th key={row.recordId} className="px-2 py-2 font-medium">
                  {row.objectLabel}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {context.differentConditionNames.map((conditionName) => (
              <tr key={conditionName} className="border-b border-border/70">
                <th className="px-2 py-2 font-medium text-text">
                  {conditionName}
                </th>
                {context.rows.map((row) => {
                  const condition = row.conditions.find(
                    ({ name }) => name === conditionName,
                  );
                  return (
                    <td key={row.recordId} className="px-2 py-2 text-text">
                      {condition
                        ? reportedCondition(condition.value, condition.unit)
                        : ""}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div
        className="mt-4 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-xs leading-5 text-text"
        role="note"
      >
        ⚠
        条件不同。这里只展示来源中已报告的数值，不能据此认为各组严格可比，也不能把数值差异归因于某个条件。
      </div>
      <div className="mt-3 text-xs text-text-secondary">
        {context.rows.length} 条 ResearchRecord · 每条可查看来源
      </div>
    </section>
  );
}
