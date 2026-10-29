/** 平台清单 API 层：实时访问 flux / JCC 端点（失败时由 hook 回落本地快照）。 */

import { buildAuthHeaders, getApiBaseUrl } from "../../../api/http";
import type { FluxSnapshot, JccInventoryResult } from "./types";

export class PlatformInventoryRequestError extends Error {
  constructor(
    public readonly status: number,
    message?: string,
  ) {
    super(
      message ??
        (status === 503
          ? "platform_inventory_unavailable"
          : "platform_inventory_request_failed"),
    );
    this.name = "PlatformInventoryRequestError";
  }
}

async function readJsonResponse(response: Response): Promise<unknown> {
  if (!response.ok) {
    await response.text().catch(() => "");
    throw new PlatformInventoryRequestError(response.status);
  }
  try {
    return await response.json();
  } catch {
    throw new PlatformInventoryRequestError(502);
  }
}

function isFluxSnapshot(value: unknown): value is FluxSnapshot {
  if (!value || typeof value !== "object") return false;
  const snapshot = value as FluxSnapshot;
  return (
    typeof snapshot.observedAt === "string" &&
    Boolean(snapshot.observedAt.trim()) &&
    Array.isArray(snapshot.templates) &&
    snapshot.templates.every(
      (t) => typeof t.id === "string" && Boolean(t.id.trim()),
    ) &&
    Array.isArray(snapshot.pvdSubRecipes) &&
    Array.isArray(snapshot.partialErrors)
  );
}

export async function fetchFluxInventory(
  signal?: AbortSignal,
): Promise<FluxSnapshot> {
  const response = await fetch(
    `${getApiBaseUrl()}/api/platform-inventory/flux`,
    { headers: buildAuthHeaders(), signal },
  );
  const payload = await readJsonResponse(response);
  if (!isFluxSnapshot(payload)) throw new PlatformInventoryRequestError(502);
  return payload;
}

function isJccInventoryResult(value: unknown): value is JccInventoryResult {
  if (!value || typeof value !== "object") return false;
  const result = value as JccInventoryResult;
  if (!result.meta || typeof result.meta !== "object") return false;
  const meta = result.meta;
  if (typeof meta.source !== "string" || typeof meta.collecting !== "boolean")
    return false;
  // payload 允许为 null（后端 seed 缺失时），但存在时必须有 resources 数组
  if (result.payload == null) return true;
  return (
    typeof result.payload === "object" && Array.isArray(result.payload.resources)
  );
}

export async function fetchJccInventory(
  signal?: AbortSignal,
): Promise<JccInventoryResult> {
  const response = await fetch(
    `${getApiBaseUrl()}/api/platform-inventory/jcc`,
    { headers: buildAuthHeaders(), signal },
  );
  const payload = await readJsonResponse(response);
  if (!isJccInventoryResult(payload)) throw new PlatformInventoryRequestError(502);
  return payload;
}

export async function requestJccRefresh(signal?: AbortSignal): Promise<{
  collecting: boolean;
  started: boolean;
}> {
  const response = await fetch(
    `${getApiBaseUrl()}/api/platform-inventory/jcc/refresh`,
    { method: "POST", headers: buildAuthHeaders(), signal },
  );
  const payload = await readJsonResponse(response);
  if (
    !payload ||
    typeof payload !== "object" ||
    typeof (payload as { collecting?: unknown }).collecting !== "boolean"
  ) {
    throw new PlatformInventoryRequestError(502);
  }
  return payload as { collecting: boolean; started: boolean };
}
