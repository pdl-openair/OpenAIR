import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ResearchActivityStream,
  type ResearchActivity,
} from "../../ResearchActivityStream";
import { composeCollaborationPresentation } from "../../../../model/collaborationPresentationCore.mjs";

import type { ResearchStreamHandlers } from "../../../../api/taskResearchStream";
const transport = vi.hoisted(() => ({ subscribe: vi.fn() }));
vi.mock("../../../../api/taskResearchStream", () => ({
  subscribeTaskResearchEvents: transport.subscribe,
}));
beforeEach(() => {
  transport.subscribe.mockReset();
  transport.subscribe.mockImplementation(
    (_taskId: string, _handlers: ResearchStreamHandlers, signal: AbortSignal) =>
      new Promise<void>((resolve) =>
        signal.addEventListener("abort", () => resolve(), { once: true }),
      ),
  );
});

function fact(
  key: string,
  phase: string,
  text: string,
  second: number,
): ResearchActivity {
  return {
    eventId: key + phase,
    activityKey: key,
    phase,
    headline: text,
    summary: null,
    eventType: "meaningful_progress",
    presentationLevel: "primary",
    workUnitId: null,
    occurredAt: `2026-10-10T00:00:0${second}Z`,
    sequence: second,
    aggregateCount: null,
  };
}

const start = fact("planning-attempt:1", "started", "正在组织研究方案", 1);
const thought = {
  ...fact("planning-thought:1", "formed", "方案思考", 2),
  summary: "先统一比较口径。",
};
const ready = fact("research-design:1:v1", "formed", "研究方案已形成", 3);
const confirmed = fact(
  "research-design:1:v1",
  "confirmed",
  "研究方案已确认",
  4,
);
const execution = fact("execution:1", "started", "证据核查已开始", 5);

function history(rows: ResearchActivity[], connected = true, taskId = "task") {
  return (
    <ResearchActivityStream
      taskId={taskId}
      enabled={connected}
      active={connected}
      durableActivities={rows}
    >
      {(stream) => (
        <div role="list">
          {composeCollaborationPresentation({
            researchFacts: stream.facts,
          }).map((node: { id: string }) => (
            <div key={node.id}>{stream.renderFact(node.id)}</div>
          ))}
        </div>
      )}
    </ResearchActivityStream>
  );
}

describe("continuous planning history", () => {
  it("uses the same Chinese result names as the artifact footer", () => {
    const result = { ...ready, artifactRefs: ["problem", "records"] };
    render(
      <ResearchActivityStream
        taskId="task"
        enabled={false}
        active={false}
        durableActivities={[result]}
        artifacts={[
          {
            id: "problem",
            title: "opentrajectory.problem_framing",
            type: "opentrajectory.problem_framing",
          },
          {
            id: "records",
            title: "research_records",
            type: "research_records",
          },
        ]}
        onSelectArtifact={vi.fn()}
      >
        {(stream) => (
          <>
            {stream.facts.map((fact) => (
              <div key={fact.id}>{stream.renderFact(fact.id)}</div>
            ))}
          </>
        )}
      </ResearchActivityStream>,
    );
    expect(
      screen.getByRole("button", { name: "研究问题与边界" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "研究记录" })).toBeVisible();
    expect(screen.queryByText("opentrajectory.problem_framing")).toBeNull();
  });
  it("keeps thoughts and plan formation through confirmation, execution, disconnection and remount", () => {
    const result = render(history([start, thought, ready]));
    expect(screen.getByText(thought.summary)).toBeVisible();
    result.rerender(history([execution, confirmed, start, ready, thought]));
    expect(
      screen.getAllByRole("listitem").map((row) => row.textContent),
    ).toEqual([
      expect.stringContaining("已开始组织研究方案"),
      expect.stringContaining(thought.summary),
      expect.stringContaining("研究方案已形成"),
      expect.stringContaining("研究方案已确认"),
      expect.stringContaining("证据核查已开始"),
    ]);
    result.rerender(
      history([start, thought, ready, confirmed, execution], false),
    );
    expect(screen.getByText(thought.summary)).toBeVisible();
    result.unmount();
    render(history([start, thought, ready, confirmed, execution]));
    expect(screen.getByText(thought.summary)).toBeVisible();
    expect(screen.getAllByRole("listitem")).toHaveLength(5);
  });
  it("retains live thoughts while disconnected and fences late events after switching tasks", async () => {
    const result = render(history([], true, "task-a"));
    await waitFor(() => expect(transport.subscribe).toHaveBeenCalled());
    const handlers = transport.subscribe.mock
      .calls[0][1] as ResearchStreamHandlers;
    const event = {
      id: "thought-v1",
      type: "meaningful_progress" as const,
      taskId: "task-a",
      occurredAt: thought.occurredAt,
      payload: {
        headline: thought.headline,
        summary: thought.summary,
        activityKey: thought.activityKey ?? undefined,
        phase: "forming",
      },
    };
    act(() => handlers.onEvent(event));
    expect(screen.getByText(thought.summary)).toBeVisible();
    act(() =>
      handlers.onEvent({
        ...event,
        id: "thought-v2",
        payload: {
          ...event.payload,
          summary: "先统一比较口径。再核查证据。",
          phase: "formed",
        },
      }),
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    result.rerender(history([], false, "task-a"));
    expect(screen.getByText("先统一比较口径。再核查证据。")).toBeVisible();
    result.rerender(history([], true, "task-b"));
    expect(screen.queryByText("先统一比较口径。再核查证据。")).toBeNull();
    act(() => handlers.onEvent({ ...event, id: "late" }));
    expect(screen.queryByText(thought.summary)).toBeNull();
  });
});
