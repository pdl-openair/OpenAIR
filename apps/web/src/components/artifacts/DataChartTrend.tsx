import {
  projectResearchRecordTrend,
  type TrendViewModel,
} from "./researchRecordTrendProjection";

type JsonObject = Record<string, unknown>;

const WIDTH = 680;
const HEIGHT = 280;
const LEFT = 70;
const RIGHT = 28;
const TOP = 30;
const BOTTOM = 58;

function conditionLabel(condition: {
  name: string;
  value: string;
  unit: string;
}): string {
  return `${condition.name} ${condition.value}${condition.unit ? ` ${condition.unit}` : ""}`;
}

export function DataChartTrend({ records }: { records: JsonObject[] }) {
  const trend = projectResearchRecordTrend(records);
  if (!trend) return null;
  return <ResearchRecordTrendChart trend={trend} />;
}

/** Render an admitted series; schema-specific projections own admission. */
export function ResearchRecordTrendChart({
  trend,
  recordLabel = "ResearchRecord",
}: {
  trend: TrendViewModel;
  recordLabel?: string;
}) {
  const xValues = trend.points.map(({ x }) => x);
  const yValues = trend.points.map(({ y }) => y);
  // Scale first so finite extreme values cannot overflow a range subtraction.
  const xScale = Math.max(...xValues.map(Math.abs)) || 1;
  const yScale = Math.max(...yValues.map(Math.abs)) || 1;
  const xMinimum = Math.min(...xValues.map((value) => value / xScale));
  const xMaximum = Math.max(...xValues.map((value) => value / xScale));
  const yMinimum = Math.min(...yValues.map((value) => value / yScale));
  const yMaximum = Math.max(...yValues.map((value) => value / yScale));
  const yPadding =
    yMinimum === yMaximum
      ? Math.max(Math.abs(yMinimum) * 0.1, 1)
      : (yMaximum - yMinimum) * 0.14;
  const plotWidth = WIDTH - LEFT - RIGHT;
  const plotHeight = HEIGHT - TOP - BOTTOM;
  const xPosition = (value: number) =>
    LEFT + ((value / xScale - xMinimum) / (xMaximum - xMinimum)) * plotWidth;
  const yPosition = (value: number) =>
    TOP +
    ((yMaximum + yPadding - value / yScale) /
      (yMaximum - yMinimum + 2 * yPadding)) *
      plotHeight;
  const polyline = trend.points
    .map(({ x, y }) => `${xPosition(x)},${yPosition(y)}`)
    .join(" ");
  const fixedContext = trend.fixedConditions.map(conditionLabel).join(" · ");

  return (
    <section
      className="mb-5 rounded-xl border border-border bg-surface-soft p-4 shadow-sm"
      data-testid="data-chart-trend"
      aria-label={`${trend.metric} 随 ${trend.conditionName} 的变化图`}
    >
      <div className="mb-1 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h4 className="m-0 text-[16px] font-semibold text-text">
            {trend.metric} 随 {trend.conditionName} 的变化
          </h4>
          <p className="mb-0 mt-1 text-xs text-text-secondary">
            研究对象：{trend.objectLabel}
          </p>
        </div>
        <span className="rounded-md bg-surface px-2 py-0.5 text-xs font-medium text-text-secondary">
          {trend.resultUnit}
        </span>
      </div>

      <div className="mt-3 overflow-x-auto rounded-lg bg-surface px-2 py-1">
        <svg
          className="block h-auto min-w-[560px] text-text-secondary"
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role="img"
          aria-label={`${trend.points.length} 个数据点，按 ${trend.conditionName} 数值升序排列`}
        >
          <line
            x1={LEFT}
            y1={TOP}
            x2={LEFT}
            y2={HEIGHT - BOTTOM}
            stroke="currentColor"
            opacity="0.45"
          />
          <line
            x1={LEFT}
            y1={HEIGHT - BOTTOM}
            x2={WIDTH - RIGHT}
            y2={HEIGHT - BOTTOM}
            stroke="currentColor"
            opacity="0.45"
          />
          <polyline
            points={polyline}
            fill="none"
            stroke="var(--color-brand, #1677ff)"
            strokeWidth="3"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
          {trend.points.map((point) => {
            const x = xPosition(point.x);
            const y = yPosition(point.y);
            const observation = point.observations[0];
            return (
              <g key={point.x} data-testid="trend-point">
                <line
                  x1={x}
                  y1={HEIGHT - BOTTOM}
                  x2={x}
                  y2={HEIGHT - BOTTOM + 5}
                  stroke="currentColor"
                  opacity="0.55"
                />
                <a href={`#${observation?.anchorId}`}>
                  <title>{observation?.sourceLabel} · 查看原始记录</title>
                  <circle
                    cx={x}
                    cy={y}
                    r="6"
                    fill="var(--color-brand, #1677ff)"
                    stroke="white"
                    strokeWidth="2"
                  />
                </a>
                <text
                  x={x}
                  y={y - 13}
                  textAnchor="middle"
                  fontSize="12"
                  fill="currentColor"
                >
                  {observation?.displayY ?? observation?.reportedY}{" "}
                  {trend.resultUnit}
                </text>
                <text
                  x={x}
                  y={HEIGHT - BOTTOM + 22}
                  textAnchor="middle"
                  fontSize="12"
                  fill="currentColor"
                >
                  {observation?.displayX ?? observation?.reportedX}
                </text>
              </g>
            );
          })}
          <text
            x={WIDTH - RIGHT}
            y={HEIGHT - 12}
            textAnchor="end"
            fontSize="12"
            fill="currentColor"
          >
            {trend.conditionName} ({trend.conditionUnit})
          </text>
          <text x={12} y={TOP - 10} fontSize="12" fill="currentColor">
            {trend.metric} ({trend.resultUnit})
          </text>
        </svg>
      </div>

      <div className="mt-3 grid gap-1.5 text-xs text-text-secondary">
        {trend.points.flatMap((point) =>
          point.observations.map((observation) => (
            <div
              key={observation.recordId}
              className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1"
              data-testid="trend-observation"
            >
              <span className="font-mono text-text">
                {observation.displayX ?? observation.reportedX}{" "}
                {trend.conditionUnit} →{" "}
                {observation.displayY ?? observation.reportedY}{" "}
                {trend.resultUnit}
                {observation.displayX !== undefined ||
                observation.displayY !== undefined ? (
                  <span className="ml-2 text-text-secondary">
                    （原文：{observation.reportedX}{" "}
                    {observation.reportedXUnit ?? trend.conditionUnit} →{" "}
                    {observation.reportedY}{" "}
                    {observation.reportedYUnit ?? trend.resultUnit}）
                  </span>
                ) : null}
              </span>
              <a
                href={`#${observation.anchorId}`}
                title={observation.sourceLabel}
              >
                查看来源：{observation.sourceLabel}
              </a>
            </div>
          )),
        )}
      </div>

      <div className="mt-4 border-t border-border pt-3 text-xs text-text-secondary">
        {fixedContext ? `其他条件：${fixedContext} · ` : ""}
        {trend.recordCount} 条 {recordLabel} · 每条可查看来源
      </div>
    </section>
  );
}
