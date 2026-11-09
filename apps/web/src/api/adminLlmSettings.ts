/**
 * Admin LLM gateway settings API client.
 * Mirrors services/api/app/routes/admin_llm_settings.py (all endpoints require role=admin).
 */
import { buildAuthHeaders, getApiBaseUrl, parseJson } from "./http";

export interface LlmSecretPreview {
  configured: boolean;
  preview: string;
}

export interface LlmSettingsView {
  anthropicBaseUrl: string;
  anthropicApiKey: LlmSecretPreview;
  anthropicAuthToken: LlmSecretPreview;
  claudeModel: string;
  providerBaseUrl: string;
  providerApiKey: LlmSecretPreview;
  providerModel: string;
  plannerModel: string;
  workerRestartRequired: boolean;
}

export interface LlmSettingsUpdate {
  anthropicBaseUrl?: string;
  anthropicApiKey?: string;
  anthropicAuthToken?: string;
  claudeModel?: string;
  providerBaseUrl?: string;
  providerApiKey?: string;
  providerModel?: string;
  plannerModel?: string;
}

export interface GatewayProbeResult {
  role: "provider" | "anthropic";
  configured: boolean;
  ok: boolean;
  status: string;
  detail?: string;
  modelsFound?: number;
  modelAvailable?: boolean | null;
}

export interface LlmTestResult {
  provider: GatewayProbeResult;
  anthropic: GatewayProbeResult;
}

export async function getLlmSettings(): Promise<LlmSettingsView> {
  const res = await fetch(`${getApiBaseUrl()}/api/admin/llm-settings`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<LlmSettingsView>(res);
}

export async function updateLlmSettings(
  update: LlmSettingsUpdate,
): Promise<{ changed: string[]; settings: LlmSettingsView }> {
  const res = await fetch(`${getApiBaseUrl()}/api/admin/llm-settings`, {
    method: "PUT",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(update),
  });
  return parseJson<{ changed: string[]; settings: LlmSettingsView }>(res);
}

export async function testLlmSettings(
  overrides?: LlmSettingsUpdate,
): Promise<LlmTestResult> {
  const res = await fetch(`${getApiBaseUrl()}/api/admin/llm-settings/test`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(overrides ?? {}),
  });
  return parseJson<LlmTestResult>(res);
}
