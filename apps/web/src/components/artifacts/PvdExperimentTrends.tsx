import { ResearchRecordTrendChart } from "./DataChartTrend";
import { projectPvdExperimentSeries } from "./pvdExperimentSeriesProjection";
import { pvdFieldLabel } from "./pvdRecordFieldLabels";
import type { JsonObject } from "./researchRecordDataChartProjection";

export function PvdExperimentTrends({ document }: { document: JsonObject }) {
  const series = projectPvdExperimentSeries(document);
  if (!series.length) return null;
  return (
    <section data-testid="pvd-experiment-trends" aria-label="参数—性能趋势">
      <h4 className="mb-3 mt-0 text-base font-semibold">参数—性能趋势</h4>
      {series.map((item, index) => (
        <div key={item.key} data-testid="pvd-experiment-series">
          <p className="mb-2 text-xs text-text-secondary">
            观察组 {index + 1} · {item.sourceLabel}
          </p>
          <ResearchRecordTrendChart
            recordLabel="研究记录"
            trend={{
              ...item,
              conditionName: pvdFieldLabel(item.conditionName),
              fixedConditions: item.fixedConditions.map((condition) => ({
                ...condition,
                name: pvdFieldLabel(condition.name),
              })),
            }}
          />
        </div>
      ))}
      <p className="mb-4 text-xs text-text-secondary">
        同一来源、材料和已报告的其余条件一致；连线展示报告值随参数的变化，不表示拟合或因果关系。可点击数据点查看原始记录。
      </p>
    </section>
  );
}
