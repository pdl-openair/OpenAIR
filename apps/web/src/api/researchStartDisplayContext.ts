import { buildAuthHeaders, getApiBaseUrl, parseJson } from "./http";

export interface ResearchStartDisplayContext {
  taskId: string;
  sessionId: string | null;
  originalInput: string | null;
  displayTitle: string | null;
  titlePending: boolean;
}

/** Optional display context. Scoped server input is never replaced by a generated goal. */
export async function fetchResearchStartDisplayContext(
  taskId: string,
  sessionId?: string | null,
): Promise<ResearchStartDisplayContext> {
  const query = sessionId ? `?sessionId=${encodeURIComponent(sessionId)}` : "";
  const res = await fetch(
    `${getApiBaseUrl()}/api/research-start/tasks/${encodeURIComponent(taskId)}/display-context${query}`,
    {
      headers: buildAuthHeaders(),
    },
  );
  return parseJson<ResearchStartDisplayContext>(res);
}
