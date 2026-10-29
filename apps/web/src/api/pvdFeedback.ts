import { buildAuthHeaders, getApiBaseUrl, parseJson } from "./http";

export interface PvdFeedbackExperimentParameter {
  parameterKey: string;
  displayName?: string | null;
  recommendedValue?: string | number | boolean | null;
  unit?: string | null;
}

export interface PvdFeedbackExperiment {
  experimentRef: string;
  hasActualExecution: boolean;
  hasMeasurements: boolean;
  parameters: PvdFeedbackExperimentParameter[];
  validationGoal?: string | null;
  failureSignal?: string | null;
  nextStep?: string | null;
}

export interface PvdFeedbackExperiments {
  taskId: string;
  scientificStateVersion: number;
  experiments: PvdFeedbackExperiment[];
}

export async function fetchPvdFeedbackExperiments(
  taskId: string,
): Promise<PvdFeedbackExperiments> {
  let res: Response;
  try {
    res = await fetch(
      `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/pvd/experiments`,
      { headers: buildAuthHeaders() },
    );
  } catch {
    throw new PvdFeedbackError("实验清单请求未送达，请检查网络连接后重试。", { status: null });
  }
  if (!res.ok) {
    if (res.status === 404) {
      throw new PvdFeedbackError("任务不存在或实验状态不可用。", { status: 404 });
    }
    throw new PvdFeedbackError("实验清单加载失败，请稍后重试。", { status: res.status });
  }
  return parseJson<PvdFeedbackExperiments>(res);
}

export interface PvdFeedbackParameter {
  parameter_key?: string;
  name?: string;
  value_type?: string;
  unit_identity?: string | null;
  unit_dimension?: string | null;
  value: unknown;
  unit?: string | null;
}

export interface PvdFeedbackExecution {
  parameters?: PvdFeedbackParameter[];
  execution_ref?: string;
  status?: string;
  validity?: string;
  other_process_conditions?: unknown[];
  [key: string]: unknown;
}

export interface PvdFeedbackMeasurement {
  metric_key?: string;
  metric?: string;
  measurement_ref?: string;
  status?: string;
  validity?: string;
  method?: string | null;
  value: unknown;
  unit?: string | null;
  qualitative_result?: string | null;
  [key: string]: unknown;
}

export interface PvdExperimentFeedbackRequest {
  expectedRevision: number;
  experimentRef: string;
  sourceRef: string;
  sourceOrigin: "user" | "imported_project_record";
  plannedExecution?: PvdFeedbackExecution | null;
  actualExecution?: PvdFeedbackExecution | null;
  measurements?: PvdFeedbackMeasurement[];
  processEvents?: Record<string, unknown>[];
}

export interface PvdExperimentFeedbackResponse {
  taskId: string;
  taskRevision: number;
  scientificStateVersion: number;
  experimentRef: string;
  selectionRequestId: string;
  state: string;
}

export class PvdFeedbackError extends Error {
  readonly status: number | null;
  readonly code: string | null;

  constructor(message: string, options: { status?: number | null; code?: string | null } = {}) {
    super(message);
    this.name = "PvdFeedbackError";
    this.status = options.status ?? null;
    this.code = options.code ?? null;
  }
}

function extractErrorCode(text: string): string | null {
  try {
    const body = JSON.parse(text) as unknown;
    if (body && typeof body === "object" && "detail" in body && typeof (body as Record<string, unknown>).detail === "string") {
      return (body as Record<string, unknown>).detail as string;
    }
  } catch {
    // ignore
  }
  return null;
}

export async function submitPvdExperimentFeedback(
  taskId: string,
  body: PvdExperimentFeedbackRequest,
  idempotencyKey: string,
): Promise<PvdExperimentFeedbackResponse> {
  let res: Response;
  try {
    res = await fetch(
      `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/pvd/experiment-feedback`,
      {
        method: "POST",
        headers: buildAuthHeaders({
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey,
        }),
        body: JSON.stringify(body),
      },
    );
  } catch {
    throw new PvdFeedbackError("实验反馈请求未送达，请检查网络连接后重试。", { status: null });
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    const code = extractErrorCode(text);
    if (res.status === 409) {
      const msg =
        code === "pvd_experiment_feedback_execution_in_flight"
          ? "当前有执行任务正在进行中，请等待完成后再提交反馈。"
          : code === "pvd_experiment_feedback_idempotency_conflict"
            ? "该来源引用已被使用，请更换来源标识后重试。"
            : code === "pvd_experiment_feedback_experiment_not_found"
              ? "该实验标识不存在，请从实验下拉列表中选择系统已规划的实验。"
              : code?.startsWith("pvd_experiment_feedback_") && code.endsWith("_conflict")
                ? "该实验已有同名反馈事实且与本次提交不一致，请核对后重试。"
                : "当前任务版本冲突，请刷新后重试。";
      throw new PvdFeedbackError(msg, { status: 409, code });
    }
    if (res.status === 422) {
      const msg =
        code === "pvd_experiment_feedback_invalid"
          ? "反馈参数/测量需要完整的参数键、数值类型与单位（如 power=240 W），请从预填行修改数值。"
          : code === "pvd_experiment_feedback_empty"
            ? "至少需要填写一项实际执行参数或测量结果。"
            : "反馈内容格式不符合要求，请检查必填字段。";
      throw new PvdFeedbackError(msg, { status: 422, code });
    }
    if (res.status === 428) {
      throw new PvdFeedbackError("缺少幂等键，请重试。", { status: 428, code });
    }
    throw new PvdFeedbackError(`提交失败（${res.status}），请稍后重试。`, { status: res.status, code });
  }
  return parseJson<PvdExperimentFeedbackResponse>(res);
}
