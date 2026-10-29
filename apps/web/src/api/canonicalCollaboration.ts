import { getApiBaseUrl } from "./client";
import { buildAuthHeaders } from "./http";
import {
  CanonicalCollaborationContractError,
  parseCanonicalCollaborationPayload,
  type CanonicalCollaborationSnapshot,
} from "../model/canonicalCollaborationView";

export type CanonicalCollaborationApiErrorKind =
  | "aborted"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "revision_mismatch"
  | "unavailable"
  | "invalid_response";

export class CanonicalCollaborationApiError extends Error {
  constructor(
    public readonly kind: CanonicalCollaborationApiErrorKind,
    message: string,
    public readonly status: number | null = null,
    public readonly detail: string | null = null,
  ) {
    super(message);
    this.name = "CanonicalCollaborationApiError";
  }
}

export interface FetchCanonicalCollaborationOptions {
  signal?: AbortSignal;
  expectedRevision?: number | null;
  request?: typeof fetch;
}

function errorKind(status: number, detail: string | null): CanonicalCollaborationApiErrorKind {
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 409 && detail === "durable_collaboration_revision_mismatch") return "revision_mismatch";
  return "unavailable";
}

function errorMessage(kind: CanonicalCollaborationApiErrorKind): string {
  if (kind === "unauthorized") return "登录状态已失效，请重新登录后查看协作进展。";
  if (kind === "forbidden") return "当前账号无权查看该任务的协作进展。";
  if (kind === "not_found") return "没有找到该任务的协作进展。";
  if (kind === "revision_mismatch") return "协作进展与任务版本暂不一致，请刷新后重试。";
  if (kind === "invalid_response") return "协作进展合同校验失败，已停止展示可能过期或错误的信息。";
  return "暂时无法读取协作进展，请稍后重试。";
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new CanonicalCollaborationApiError(
      "invalid_response",
      errorMessage("invalid_response"),
      response.status,
      "non_json_response",
    );
  }
}

function responseDetail(payload: unknown): string | null {
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) return null;
  const detail = (payload as Record<string, unknown>).detail;
  return typeof detail === "string" ? detail : null;
}

export async function fetchCanonicalCollaboration(
  taskId: string,
  options: FetchCanonicalCollaborationOptions = {},
): Promise<CanonicalCollaborationSnapshot> {
  const normalizedTaskId = taskId.trim();
  if (!normalizedTaskId) {
    throw new CanonicalCollaborationApiError("not_found", errorMessage("not_found"));
  }
  const request = options.request ?? fetch;
  let response: Response;
  try {
    response = await request(
      `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(normalizedTaskId)}/collaboration/canonical`,
      {
        method: "GET",
        headers: buildAuthHeaders({ Accept: "application/json" }),
        credentials: "include",
        signal: options.signal,
      },
    );
  } catch (error) {
    if (options.signal?.aborted || (error instanceof DOMException && error.name === "AbortError")) {
      throw new CanonicalCollaborationApiError("aborted", "协作进展请求已取消。");
    }
    throw new CanonicalCollaborationApiError("unavailable", errorMessage("unavailable"));
  }

  const payload = await readJson(response);
  if (!response.ok) {
    const detail = responseDetail(payload);
    const kind = errorKind(response.status, detail);
    throw new CanonicalCollaborationApiError(kind, errorMessage(kind), response.status, detail);
  }

  try {
    return parseCanonicalCollaborationPayload(payload, {
      expectedRevision: options.expectedRevision,
    });
  } catch (error) {
    if (error instanceof CanonicalCollaborationContractError) {
      const kind = error.kind === "revision_mismatch" ? "revision_mismatch" : "invalid_response";
      throw new CanonicalCollaborationApiError(kind, errorMessage(kind), response.status, error.kind);
    }
    throw error;
  }
}
