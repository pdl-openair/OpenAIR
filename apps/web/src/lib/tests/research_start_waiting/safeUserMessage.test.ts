import { describe, expect, it } from "vitest";
import type { ResearchStartSessionStatus } from "../../../api/client";
import { projectResearchStartWaitingView } from "../../researchStartWaiting";

function snapshotWithUserMessage(
  userMessage: string,
): ResearchStartSessionStatus {
  return {
    sessionId: "s1",
    status: "running",
    currentPhase: "planning",
    userMessage,
  } as unknown as ResearchStartSessionStatus;
}

describe("researchStartWaiting userMessage 安全过滤", () => {
  it("放行干净的服务端中文话术", () => {
    const view = projectResearchStartWaitingView(
      snapshotWithUserMessage("正在为你整理研究方案，请稍候。"),
    );
    expect(view.message).toContain("整理研究方案");
  });

  it("放行干净的服务端英文话术（不再丢弃英文）", () => {
    const view = projectResearchStartWaitingView(
      snapshotWithUserMessage(
        "We could not create a research design. Please try again later.",
      ),
    );
    expect(view.message).toContain("could not create a research design");
  });

  it("拦截含内部工程术语的消息", () => {
    const view = projectResearchStartWaitingView(
      snapshotWithUserMessage(
        "provider configuration error: HTTP 401 from gateway",
      ),
    );
    expect(view.message).not.toContain("provider");
    expect(view.message).not.toContain("401");
  });

  it("拦截 JSON/多行堆栈形态的消息", () => {
    const json = projectResearchStartWaitingView(
      snapshotWithUserMessage('{"error_code": "x"}'),
    );
    expect(json.message).not.toContain("error_code");
    const multiline = projectResearchStartWaitingView(
      snapshotWithUserMessage(
        'Traceback (most recent call last):\n  File "app.py"',
      ),
    );
    expect(multiline.message).not.toContain("Traceback");
  });
});
