import type { Task } from "@openair/shared";
import { ApiError, buildAuthHeaders, getApiBaseUrl, parseJson } from "./http";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export type TaskListPage = {
  tasks: Task[];
  total: number;
  limit: number | null;
  offset: number;
  statusTotals?: Record<string, number>;
};

/** Paginated task list (slim projection). Default server page size is 20. */
export async function fetchTaskListPage(opts?: {
  limit?: number;
  offset?: number;
  statuses?: string[];
  signal?: AbortSignal;
}): Promise<TaskListPage> {
  const params = new URLSearchParams();
  if (opts?.limit != null) params.set("limit", String(opts.limit));
  if (opts?.offset != null && opts.offset > 0)
    params.set("offset", String(opts.offset));
  for (const status of opts?.statuses ?? []) params.append("status", status);
  const qs = params.toString();
  const res = await fetch(`${getApiBaseUrl()}/api/tasks${qs ? `?${qs}` : ""}`, {
    headers: buildAuthHeaders(),
    signal: opts?.signal,
  });
  const data = await parseJson<unknown>(res);
  if (!isRecord(data) || !Array.isArray(data.tasks)) {
    throw new ApiError(
      "Task list response contract is invalid.",
      200,
      "response_contract_invalid",
      data,
    );
  }
  return {
    tasks: data.tasks as Task[],
    total: typeof data.total === "number" ? data.total : data.tasks.length,
    limit: typeof data.limit === "number" ? data.limit : null,
    offset: typeof data.offset === "number" ? data.offset : 0,
    statusTotals: isRecord(data.statusTotals)
      ? (Object.fromEntries(
          Object.entries(data.statusTotals).filter(
            ([, value]) => typeof value === "number",
          ),
        ) as Record<string, number>)
      : {},
  };
}
