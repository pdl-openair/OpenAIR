import { submitResearchStartStream, type ResearchStartAcceptedResponse } from "../api/client";
import type { ResearchStartPendingState } from "./userRouteState";

export function buildResearchStartSubmitFromPending(
  pending: ResearchStartPendingState,
): Parameters<typeof submitResearchStartStream>[0] {
  return {
    input: pending.input,
    sourcePage: pending.sourcePage ?? "home",
    presetQuery: pending.presetQuery,
    clientRequestId: pending.clientRequestId,
    sessionId: pending.sessionId,
    createClarificationDraft: true,
  };
}

export async function retryResearchStartFromPending(
  pending: ResearchStartPendingState,
  onAccepted?: (accepted: ResearchStartAcceptedResponse) => void,
): Promise<ResearchStartAcceptedResponse> {
  return submitResearchStartStream(
    buildResearchStartSubmitFromPending(pending),
    (event) => {
      if (event.type === "accepted") onAccepted?.(event);
    },
  );
}
