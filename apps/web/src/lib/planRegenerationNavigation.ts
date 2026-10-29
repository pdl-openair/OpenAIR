import type { AppliedCollaborationAction } from "../api/agent";

/** Return the planning-shell query when this turn actually restarted plan generation. */
export function planningSessionAfterRegeneration(
  current: URLSearchParams,
  receipt: AppliedCollaborationAction | null | undefined,
): URLSearchParams | null {
  if (receipt?.type !== "regenerate_research_plan" || receipt.applied !== true) return null;
  const sessionId = receipt.sessionId?.trim();
  if (!sessionId) return null;
  const next = new URLSearchParams(current);
  next.set("planningSessionId", sessionId);
  return next;
}
