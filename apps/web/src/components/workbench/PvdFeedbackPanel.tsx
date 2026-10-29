import { useState, useCallback, useEffect, useMemo } from "react";
import { Button, Form, Input, Typography, Collapse, Alert, Select, Skeleton } from "antd";
import { PlusOutlined, MinusCircleOutlined, ExperimentOutlined } from "@ant-design/icons";
import {
  fetchPvdFeedbackExperiments,
  submitPvdExperimentFeedback,
  PvdFeedbackError,
  type PvdFeedbackExperiment,
  type PvdFeedbackParameter,
  type PvdFeedbackMeasurement,
} from "../../api/pvdFeedback";

interface PvdFeedbackPanelProps {
  taskId: string;
  taskRevision: number;
  /** Called after a successful submission so the parent can refresh. */
  onSubmitted?: (newRevision: number) => void;
  onCancel?: () => void;
}

/**
 * One feedback parameter row. `parameterKey` and unit identity fields come from
 * the system recommendation (or user input for ad-hoc rows); only `value` is
 * expected to be edited by the user in the common flow.
 */
interface ParameterRow {
  parameter_key: string;
  value: string;
  unit: string;
  value_type: string;
  unit_identity: string;
  unit_dimension: string;
}

interface MeasurementRow {
  metric_key: string;
  value: string;
  unit: string;
}

/** Canonical unit identity/dimension table for the four PVD parameters. */
const UNIT_IDENTITY: Record<string, { identity: string; dimension: string; unit: string }> = {
  power: { identity: "power:W", dimension: "power", unit: "W" },
  n2_flow: { identity: "flow:sccm", dimension: "volumetric_flow_rate", unit: "sccm" },
  ar_flow: { identity: "flow:sccm", dimension: "volumetric_flow_rate", unit: "sccm" },
  total_gas_flow: { identity: "flow:sccm", dimension: "volumetric_flow_rate", unit: "sccm" },
  deposition_time: { identity: "time:s", dimension: "time", unit: "s" },
};

const KNOWN_METRIC_UNITS: Record<string, string> = {
  rocking_curve_fwhm_002: "deg",
  deposition_rate: "nm/s",
  film_thickness: "nm",
  surface_condition: "",
};

function recRow(parameterKey: string, recommendedValue: unknown, unit: string | null | undefined): ParameterRow {
  const meta = UNIT_IDENTITY[parameterKey];
  const normalizedValue =
    typeof recommendedValue === "number" || typeof recommendedValue === "boolean"
      ? String(recommendedValue)
      : typeof recommendedValue === "string"
        ? recommendedValue
        : "";
  return {
    parameter_key: parameterKey,
    value: normalizedValue,
    unit: unit ?? meta?.unit ?? "",
    value_type: normalizedValue !== "" && /^-?\d+$/.test(normalizedValue) ? "integer" : "continuous",
    unit_identity: meta?.identity ?? (unit ? `custom:${unit}` : ""),
    unit_dimension: meta?.dimension ?? "",
  };
}

function emptyParam(): ParameterRow {
  return { parameter_key: "", value: "", unit: "", value_type: "continuous", unit_identity: "", unit_dimension: "" };
}

function emptyMeasurement(): MeasurementRow {
  return { metric_key: "", value: "", unit: "" };
}

function toParams(rows: ParameterRow[]): PvdFeedbackParameter[] {
  return rows
    .filter(r => r.parameter_key.trim() && r.value.trim())
    .map(r => ({
      parameter_key: r.parameter_key.trim(),
      value: /^-?\d+$/.test(r.value.trim())
        ? Number.parseInt(r.value.trim(), 10)
        : /^-?\d*\.?\d+(e[+-]?\d+)?$/i.test(r.value.trim())
          ? Number.parseFloat(r.value.trim())
          : r.value.trim(),
      value_type: /^-?\d+$/.test(r.value.trim()) ? "integer" : "continuous",
      unit: r.unit.trim() || null,
      unit_identity: r.unit_identity.trim() || null,
      unit_dimension: r.unit_dimension.trim() || null,
    }));
}

function toMeasurements(rows: MeasurementRow[]): PvdFeedbackMeasurement[] {
  return rows
    .filter(r => r.metric_key.trim() && r.value.trim())
    .map((r, index) => ({
      measurement_ref: `user-measurement-${index + 1}`,
      metric_key: r.metric_key.trim(),
      value: /^-?\d*\.?\d+(e[+-]?\d+)?$/i.test(r.value.trim())
        ? Number.parseFloat(r.value.trim())
        : r.value.trim(),
      unit: r.unit.trim() || null,
      status: "final" as const,
      validity: "valid" as const,
      method: null,
    }));
}

function ParameterTable({
  rows,
  onChange,
  testPrefix,
}: {
  rows: ParameterRow[];
  onChange: (rows: ParameterRow[]) => void;
  testPrefix: string;
}) {
  const update = (index: number, field: keyof ParameterRow, val: string) => {
    const next = rows.map((r, i) => (i === index ? { ...r, [field]: val } : r));
    onChange(next);
  };
  const remove = (index: number) => onChange(rows.filter((_, i) => i !== index));
  const add = () => onChange([...rows, emptyParam()]);

  return (
    <div className="grid gap-1.5" data-testid={`${testPrefix}-params`}>
      {rows.map((row, index) => (
        <div key={index} className="flex gap-1.5 items-center">
          <Input
            size="small"
            placeholder="参数键（如 power）"
            value={row.parameter_key}
            onChange={e => update(index, "parameter_key", e.target.value)}
            className="flex-[2]"
          />
          <Input
            size="small"
            placeholder="数值"
            value={row.value}
            onChange={e => update(index, "value", e.target.value)}
            className="flex-[2]"
          />
          <Input
            size="small"
            placeholder="单位"
            value={row.unit}
            onChange={e => update(index, "unit", e.target.value)}
            className="flex-1"
          />
          <Button
            type="text"
            size="small"
            icon={<MinusCircleOutlined />}
            danger
            onClick={() => remove(index)}
            aria-label="删除此行"
          />
        </div>
      ))}
      <Button
        type="dashed"
        size="small"
        icon={<PlusOutlined />}
        onClick={add}
        className="w-fit"
      >
        添加参数
      </Button>
    </div>
  );
}

function MeasurementTable({
  rows,
  onChange,
}: {
  rows: MeasurementRow[];
  onChange: (rows: MeasurementRow[]) => void;
}) {
  const update = (index: number, field: keyof MeasurementRow, val: string) => {
    const next = rows.map((r, i) => (i === index ? { ...r, [field]: val } : r));
    onChange(next);
  };
  const remove = (index: number) => onChange(rows.filter((_, i) => i !== index));
  const add = () => onChange([...rows, emptyMeasurement()]);

  return (
    <div className="grid gap-1.5" data-testid="pvd-feedback-measurements">
      {rows.map((row, index) => (
        <div key={index} className="flex gap-1.5 items-center">
          <Input
            size="small"
            placeholder="指标键（如 rocking_curve_fwhm_002）"
            value={row.metric_key}
            onChange={e => update(index, "metric_key", e.target.value)}
            className="flex-[2]"
          />
          <Input
            size="small"
            placeholder="测量值"
            value={row.value}
            onChange={e => update(index, "value", e.target.value)}
            className="flex-[2]"
          />
          <Input
            size="small"
            placeholder="单位"
            value={row.unit}
            onChange={e => update(index, "unit", e.target.value)}
            className="flex-1"
          />
          <Button
            type="text"
            size="small"
            icon={<MinusCircleOutlined />}
            danger
            onClick={() => remove(index)}
            aria-label="删除此行"
          />
        </div>
      ))}
      <Button
        type="dashed"
        size="small"
        icon={<PlusOutlined />}
        onClick={add}
        className="w-fit"
      >
        添加测量结果
      </Button>
    </div>
  );
}

export function PvdFeedbackPanel({
  taskId,
  taskRevision,
  onSubmitted,
  onCancel,
}: PvdFeedbackPanelProps) {
  const [experiments, setExperiments] = useState<PvdFeedbackExperiment[] | null>(null);
  const [experimentsError, setExperimentsError] = useState<string | null>(null);
  const [selectedRef, setSelectedRef] = useState<string | null>(null);
  const [sourceRef, setSourceRef] = useState("");
  const [actualParams, setActualParams] = useState<ParameterRow[]>([]);
  const [plannedParams, setPlannedParams] = useState<ParameterRow[]>([]);
  const [measurements, setMeasurements] = useState<MeasurementRow[]>([emptyMeasurement()]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showPlanned, setShowPlanned] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setExperimentsError(null);
    fetchPvdFeedbackExperiments(taskId)
      .then(result => {
        if (!cancelled) setExperiments(result.experiments);
      })
      .catch(err => {
        if (!cancelled) {
          setExperiments([]);
          setExperimentsError(
            err instanceof PvdFeedbackError ? err.message : "实验清单加载失败，请稍后重试。",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [taskId]);

  const selected = useMemo(
    () => experiments?.find(e => e.experimentRef === selectedRef) ?? null,
    [experiments, selectedRef],
  );

  const handleSelectExperiment = (experimentRef: string) => {
    setSelectedRef(experimentRef);
    setError(null);
    const experiment = experiments?.find(e => e.experimentRef === experimentRef);
    if (!experiment) return;
    // Pre-fill actual execution parameters from the system recommendation.
    const prefilled = experiment.parameters
      .filter(p => p.parameterKey)
      .map(p => recRow(p.parameterKey, p.recommendedValue, p.unit));
    setActualParams(prefilled.length > 0 ? prefilled : [emptyParam()]);
    // Pre-fill the measurement rows the validation goal asks for.
    const metricRows: MeasurementRow[] = [];
    for (const key of Object.keys(KNOWN_METRIC_UNITS)) {
      if (
        (experiment.validationGoal ?? "").toLowerCase().includes(key.split("_")[0].toLowerCase()) ||
        key === "rocking_curve_fwhm_002" ||
        key === "deposition_rate"
      ) {
        metricRows.push({ metric_key: key, value: "", unit: KNOWN_METRIC_UNITS[key] });
      }
    }
    setMeasurements(metricRows.length > 0 ? metricRows : [emptyMeasurement()]);
  };

  const canSubmit =
    selectedRef !== null &&
    sourceRef.trim().length > 0 &&
    (toParams(actualParams).length > 0 || toMeasurements(measurements).length > 0);

  const handleSubmit = useCallback(async () => {
    if (!canSubmit || submitting || !selectedRef) return;
    setSubmitting(true);
    setError(null);
    const idempotencyKey = crypto.randomUUID();
    const params = toParams(actualParams);
    const actualExec = params.length > 0
      ? {
          execution_ref: `${selectedRef}-execution-user-001`,
          status: "executed",
          validity: "valid",
          parameters: params,
          other_process_conditions: [],
        }
      : null;
    const plannedExec = showPlanned && toParams(plannedParams).length > 0
      ? {
          parameters: toParams(plannedParams),
          other_process_conditions: [],
        }
      : null;
    const meas = toMeasurements(measurements);
    try {
      const result = await submitPvdExperimentFeedback(
        taskId,
        {
          expectedRevision: taskRevision,
          experimentRef: selectedRef,
          sourceRef: sourceRef.trim(),
          sourceOrigin: "user",
          actualExecution: actualExec,
          plannedExecution: plannedExec,
          measurements: meas,
        },
        idempotencyKey,
      );
      onSubmitted?.(result.taskRevision);
    } catch (err) {
      setError(err instanceof PvdFeedbackError ? err.message : "提交失败，请稍后重试。");
    } finally {
      setSubmitting(false);
    }
  }, [
    canSubmit, submitting, actualParams, plannedParams, measurements,
    showPlanned, taskId, taskRevision, selectedRef, sourceRef, onSubmitted,
  ]);

  return (
    <div
      className="rounded-lg border border-border bg-surface p-3 grid gap-3"
      data-testid="pvd-feedback-panel"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <ExperimentOutlined className="text-brand" />
          <Typography.Text strong>提交实验反馈</Typography.Text>
        </div>
        {onCancel ? (
          <Button type="text" size="small" onClick={onCancel} aria-label="关闭反馈面板">
            ✕
          </Button>
        ) : null}
      </div>

      {error ? (
        <Alert type="error" message={error} showIcon closable onClose={() => setError(null)} />
      ) : null}
      {experimentsError ? (
        <Alert type="warning" message={experimentsError} showIcon />
      ) : null}

      <Form layout="vertical" size="small" component="div" className="grid gap-2">
        <Form.Item
          label="选择实验"
          required
          className="mb-0"
          tooltip="只能对系统已规划的实验回报结果；下拉列表来自当前任务的科学状态"
        >
          {experiments === null ? (
            <Skeleton.Input active size="small" block />
          ) : (
            <Select
              size="small"
              placeholder="选择要回报的实验"
              value={selectedRef}
              onChange={handleSelectExperiment}
              showSearch
              optionFilterProp="label"
              data-testid="pvd-feedback-experiment-select"
              options={experiments.map(e => ({
                value: e.experimentRef,
                label: `${e.experimentRef}${e.hasActualExecution ? "（已有执行反馈）" : ""}`,
              }))}
            />
          )}
        </Form.Item>

        {selected ? (
          <Alert
            type="info"
            showIcon
            message={
              <div className="grid gap-0.5">
                {selected.validationGoal ? (
                  <Typography.Text className="text-xs">
                    验证目标：{selected.validationGoal}
                  </Typography.Text>
                ) : null}
                {selected.failureSignal ? (
                  <Typography.Text className="text-xs" type="warning">
                    失败信号：{selected.failureSignal}
                  </Typography.Text>
                ) : null}
              </div>
            }
            data-testid="pvd-feedback-experiment-context"
          />
        ) : null}

        <Form.Item
          label="来源标识"
          required
          className="mb-0"
          tooltip="数据来源标识，建议用日期+批次，如 2026-10-14-lab-report；每次提交需用新的来源标识"
        >
          <Input
            value={sourceRef}
            onChange={e => setSourceRef(e.target.value)}
            placeholder="如 2026-10-14-lab-report"
            data-testid="pvd-feedback-source-ref"
          />
        </Form.Item>

        <Collapse
          size="small"
          ghost
          className="[&_.ant-collapse-header]:px-0! [&_.ant-collapse-content-box]:px-0!"
          items={[
            {
              key: "actual",
              label: <Typography.Text strong>实际执行参数（已按推荐值预填，请改为实际值）</Typography.Text>,
              children: (
                <ParameterTable
                  rows={actualParams}
                  onChange={setActualParams}
                  testPrefix="pvd-feedback-actual"
                />
              ),
            },
            {
              key: "measurements",
              label: <Typography.Text strong>测量结果</Typography.Text>,
              children: (
                <MeasurementTable
                  rows={measurements}
                  onChange={setMeasurements}
                />
              ),
            },
          ]}
          defaultActiveKey={["actual", "measurements"]}
        />

        {showPlanned ? (
          <div>
            <Typography.Text strong className="text-sm">计划执行参数（可选）</Typography.Text>
            <div className="mt-1.5">
              <ParameterTable
                rows={plannedParams}
                onChange={setPlannedParams}
                testPrefix="pvd-feedback-planned"
              />
            </div>
          </div>
        ) : (
          <Button
            type="link"
            size="small"
            className="w-fit px-0"
            onClick={() => {
              setShowPlanned(true);
              const prefilled = selected?.parameters
                .filter(p => p.parameterKey)
                .map(p => recRow(p.parameterKey, p.recommendedValue, p.unit)) ?? [];
              setPlannedParams(prefilled.length > 0 ? prefilled : [emptyParam()]);
            }}
          >
            + 填写计划执行参数（可选）
          </Button>
        )}
      </Form>

      <div className="flex gap-2 justify-end">
        {onCancel ? (
          <Button size="small" onClick={onCancel} disabled={submitting}>
            取消
          </Button>
        ) : null}
        <Button
          type="primary"
          size="small"
          loading={submitting}
          disabled={!canSubmit}
          onClick={() => void handleSubmit()}
          data-testid="pvd-feedback-submit"
        >
          提交反馈
        </Button>
      </div>
    </div>
  );
}
