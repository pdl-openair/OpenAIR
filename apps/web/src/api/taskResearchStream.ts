import { apiErrorFromResponse, buildAuthHeaders, getApiBaseUrl } from "./http";

export type ResearchStreamEventType =
  | "research_progress"
  | "research_tool_summary"
  | "meaningful_progress"
  | "heartbeat"
  | "stream_reset";

export type ResearchStreamEvent = {
  id?: string;
  type: ResearchStreamEventType;
  taskId: string;
  sequence?: number | null;
  sourceIdentity?: string | null;
  occurredAt?: string | null;
  emittedAt?: string | null;
  payload?: {
    presentationLevel?: "primary" | "detail";
    workUnitId?: string | null;
    headline?: string;
    summary?: string | null;
    invalidateScopes?: string[];
    aggregateCount?: number;
    activityKey?: string;
    artifactRefs?: string[];
    phase?: string;
    reason?: string;
    transient?: boolean;
  };
};

export type ResearchStreamHandlers = {
  onOpen?: () => void;
  onEvent: (event: ResearchStreamEvent) => void;
  onRetry?: (retryMs: number) => void;
};

export class ResearchStreamHttpError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ResearchStreamHttpError";
    this.status = status;
  }
}

function parseRetry(value: string): number | null {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) return null;
  return Math.max(1000, Math.min(parsed, 30000));
}

export async function subscribeTaskResearchEvents(
  taskId: string,
  handlers: ResearchStreamHandlers,
  signal: AbortSignal,
  lastEventId?: string | null,
): Promise<void> {
  const response = await fetch(
    `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/research-events`,
    {
      headers: buildAuthHeaders({
        Accept: "text/event-stream",
        ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
      }),
      signal,
    },
  );
  if (!response.ok) throw await apiErrorFromResponse(response);
  if (!response.body) throw new Error("Research event stream is not available");

  handlers.onOpen?.();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventName = "";
  let eventId = "";
  let dataLines: string[] = [];

  const emit = () => {
    if (!dataLines.length) {
      eventName = "";
      eventId = "";
      return;
    }
    const parsed = JSON.parse(dataLines.join("\n")) as ResearchStreamEvent;
    const type = (parsed.type || eventName || "research_progress") as ResearchStreamEventType;
    if (parsed.id && eventId && parsed.id !== eventId) {
      eventName = "";
      eventId = "";
      dataLines = [];
      return;
    }
    if (parsed.type && eventName && parsed.type !== eventName) {
      eventName = "";
      eventId = "";
      dataLines = [];
      return;
    }
    handlers.onEvent({ ...parsed, type, ...(parsed.id || !eventId ? {} : { id: eventId }) });
    eventName = "";
    eventId = "";
    dataLines = [];
  };

  const processLine = (rawLine: string) => {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (!line) {
      emit();
      return;
    }
    if (line.startsWith(":")) return;
    const separator = line.indexOf(":");
    const field = separator >= 0 ? line.slice(0, separator) : line;
    const value = separator >= 0 ? line.slice(separator + 1).replace(/^ /, "") : "";
    if (field === "id") eventId = value;
    else if (field === "event") eventName = value;
    else if (field === "data") dataLines.push(value);
    else if (field === "retry") {
      const retryMs = parseRetry(value);
      if (retryMs !== null) handlers.onRetry?.(retryMs);
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    lines.forEach(processLine);
  }
  buffer += decoder.decode();
  if (buffer) processLine(buffer);
  emit();
}
