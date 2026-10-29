import { describe, expect, it } from "vitest";

import { planningSessionAfterRegeneration } from "./planRegenerationNavigation";

describe("planningSessionAfterRegeneration", () => {
  it("reopens the planning shell only after a regeneration was queued", () => {
    const next = planningSessionAfterRegeneration(new URLSearchParams("artifact=a"), {
      type: "regenerate_research_plan",
      applied: true,
      sessionId: "intent_abc123def456",
    });

    expect(next?.get("planningSessionId")).toBe("intent_abc123def456");
    expect(next?.get("artifact")).toBe("a");
  });

  it("leaves the current workbench in place when regeneration did not start", () => {
    const current = new URLSearchParams("artifact=a");

    expect(
      planningSessionAfterRegeneration(current, {
        type: "regenerate_research_plan",
        applied: false,
        sessionId: "intent_abc123def456",
      }),
    ).toBeNull();
    expect(
      planningSessionAfterRegeneration(current, {
        type: "explicit_retry",
        applied: true,
        sessionId: "intent_abc123def456",
      }),
    ).toBeNull();
  });
});
