import { buildAuthHeaders, getApiBaseUrl } from "../../api/http";

export interface AgentSessionStep {
  stepKey: string;
  pack: string;
  stage: string;
  substage: string;
  role: string | null;
  supportedRole: string | null;
  artifactContract: string | null;
  enabled: boolean;
}

export interface AgentSessionMessage {
  messageId: string;
  role: "system" | "user" | "assistant";
  content: string;
  createdAt: string;
}

export interface AgentSessionArtifact {
  artifactId: string;
  title: string;
  stepKey: string | null;
  artifactContract: string | null;
  runId?: string | null;
  summary: string;
  body: Record<string, unknown>;
  downloadAvailable?: boolean;
  createdAt: string;
}

export interface AgentSession {
  sessionId: string;
  title: string;
  status: string;
  resourceId: string;
  resourceVersion: string;
  agentId: string;
  agentDisplayName: string;
  sessionKind?: "agent" | "skill";
  activeStepKey: string | null;
  steps: AgentSessionStep[];
  messages: AgentSessionMessage[];
  artifacts: AgentSessionArtifact[];
  createdAt: string;
  updatedAt: string;
}

export class AgentSessionRequestError extends Error {
  constructor(public readonly status: number, message?: string) {
    super(message ?? "agent_session_request_failed");
    this.name = "AgentSessionRequestError";
  }
}

async function readJson(response: Response): Promise<unknown> {
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new AgentSessionRequestError(response.status, detail || undefined);
  }
  return response.json();
}

export async function createAgentSession(resourceId: string): Promise<AgentSession> {
  const response = await fetch(`${getApiBaseUrl()}/api/agent-sessions`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ resourceId }),
  });
  return (await readJson(response)) as AgentSession;
}

export async function fetchAgentSession(sessionId: string, signal?: AbortSignal): Promise<AgentSession> {
  const response = await fetch(`${getApiBaseUrl()}/api/agent-sessions/${encodeURIComponent(sessionId)}`, {
    headers: buildAuthHeaders(),
    signal,
  });
  return (await readJson(response)) as AgentSession;
}

export interface AgentSessionSummary {
  sessionId: string;
  title: string;
  status: string;
  resourceId: string;
  agentId: string;
  agentDisplayName: string;
  sessionKind?: "agent" | "skill";
  messageCount: number;
  artifactCount: number;
  createdAt: string;
  updatedAt: string;
}

export async function fetchAgentSessionList(
  limit = 50,
  signal?: AbortSignal,
): Promise<AgentSessionSummary[]> {
  const response = await fetch(
    `${getApiBaseUrl()}/api/agent-sessions/history?limit=${encodeURIComponent(String(limit))}`,
    {
      headers: buildAuthHeaders(),
      signal,
    },
  );
  const payload = (await readJson(response)) as { items?: AgentSessionSummary[] };
  return Array.isArray(payload.items) ? payload.items : [];
}

export async function postAgentSessionMessage(sessionId: string, content: string): Promise<AgentSession> {
  const requestId = crypto.randomUUID();
  const response = await fetch(
    `${getApiBaseUrl()}/api/agent-sessions/${encodeURIComponent(sessionId)}/messages`,
    {
      method: "POST",
      headers: buildAuthHeaders({ "Content-Type": "application/json", "Idempotency-Key": requestId }),
      body: JSON.stringify({ content }),
    },
  );
  const accepted = (await readJson(response)) as { jobId: string; sessionId: string; state: "accepted" };
  for (;;) {
    const statusResponse = await fetch(
      `${getApiBaseUrl()}/api/model-jobs/agent_session/${encodeURIComponent(sessionId)}/${encodeURIComponent(accepted.jobId)}`,
      { headers: buildAuthHeaders() },
    );
    const job = (await readJson(statusResponse)) as {
      state: "accepted" | "running" | "succeeded" | "blocked" | "failed";
      errorCode?: string | null;
    };
    if (job.state === "succeeded") return fetchAgentSession(sessionId);
    if (!["accepted", "running"].includes(job.state)) {
      throw new AgentSessionRequestError(409, job.errorCode || `agent session turn ${job.state}`);
    }
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
}

export async function downloadAgentSessionArtifact(sessionId: string, artifactId: string): Promise<void> {
  const response = await fetch(
    `${getApiBaseUrl()}/api/agent-sessions/${encodeURIComponent(sessionId)}/artifacts/${encodeURIComponent(artifactId)}/download`,
    { headers: buildAuthHeaders() },
  );
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new AgentSessionRequestError(response.status, detail || undefined);
  }
  const disposition = response.headers.get("content-disposition") ?? "";
  const filename = resolveMarkdownDownloadName(disposition, artifactId);
  const raw = await response.blob();
  // Force a markdown blob type so the saved file is treated as .md, not JSON.
  const blob =
    raw.type.includes("markdown") || raw.type.startsWith("text/")
      ? raw
      : new Blob([raw], { type: "text/markdown;charset=utf-8" });
  triggerBrowserDownload(blob, filename);
}

export async function downloadAgentSessionArtifactsZip(sessionId: string): Promise<void> {
  const response = await fetch(
    `${getApiBaseUrl()}/api/agent-sessions/${encodeURIComponent(sessionId)}/artifacts/download-zip`,
    { headers: buildAuthHeaders() },
  );
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new AgentSessionRequestError(response.status, detail || undefined);
  }
  const disposition = response.headers.get("content-disposition") ?? "";
  const filename = resolveZipDownloadName(disposition, sessionId);
  const blob = await response.blob();
  triggerBrowserDownload(blob, filename);
}

function triggerBrowserDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function resolveZipDownloadName(disposition: string, sessionId: string): string {
  const star = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(disposition);
  if (star?.[1]) {
    try {
      const decoded = decodeURIComponent(star[1].trim().replace(/^"|"$/g, ""));
      if (decoded) return ensureZipExtension(decoded);
    } catch {
      // ignore malformed RFC5987 value
    }
  }
  const plain = /(?:^|;)\s*filename\s*=\s*"([^"]+)"|(?:^|;)\s*filename\s*=\s*([^;]+)/i.exec(disposition);
  const candidate = (plain?.[1] || plain?.[2] || "").trim();
  if (candidate && !candidate.includes("*")) {
    return ensureZipExtension(candidate);
  }
  return `agent_session_${sessionId.slice(-12)}_artifacts.zip`;
}

function ensureZipExtension(name: string): string {
  const trimmed = name.trim() || "artifacts.zip";
  if (/\.zip$/i.test(trimmed)) return trimmed;
  return `${trimmed}.zip`;
}

function resolveMarkdownDownloadName(disposition: string, artifactId: string): string {
  const star = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(disposition);
  if (star?.[1]) {
    try {
      const decoded = decodeURIComponent(star[1].trim().replace(/^"|"$/g, ""));
      if (decoded) return ensureMarkdownExtension(decoded);
    } catch {
      // ignore malformed RFC5987 value
    }
  }
  const plain = /(?:^|;)\s*filename\s*=\s*"([^"]+)"|(?:^|;)\s*filename\s*=\s*([^;]+)/i.exec(disposition);
  const candidate = (plain?.[1] || plain?.[2] || "").trim();
  if (candidate && !candidate.includes("*")) {
    return ensureMarkdownExtension(candidate);
  }
  return `artifact_${artifactId.slice(-8)}.md`;
}

function ensureMarkdownExtension(name: string): string {
  const trimmed = name.trim() || "artifact.md";
  if (/\.md$/i.test(trimmed)) return trimmed;
  if (/\.json$/i.test(trimmed)) return trimmed.replace(/\.json$/i, ".md");
  return `${trimmed}.md`;
}
