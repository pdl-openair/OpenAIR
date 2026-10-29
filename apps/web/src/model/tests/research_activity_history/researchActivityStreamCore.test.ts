import { describe, expect, it } from "vitest";
import {
  foldResearchActivityPresentation,
  coalesceResearchActivityBlocks,
  researchWorkUnitContexts,
  mergeResearchActivityHistory,
} from "../../researchActivityStreamCore.mjs";

const planningStarted = {
  eventId: "planning-started",
  eventType: "meaningful_progress",
  presentationLevel: "primary" as const,
  workUnitId: null,
  headline: "正在组织研究方案",
  summary: null,
  sequence: 1,
  occurredAt: "2026-10-07T03:01:18Z",
  aggregateCount: null,
  activityKey: "planning-attempt:attempt-1",
  phase: "started",
};

describe("research activity planning lifecycle presentation", () => {
  it("grows a persisted open thought without changing its identity or position, then rejects older replay", () => {
    const initial = {
      ...planningStarted,
      activityKey: "planning-thought:1",
      phase: "forming",
      summary: "先统一口径。",
    };
    const live = {
      ...initial,
      eventId: "live-update",
      summary: "先统一口径。再核查证据。",
      phase: "formed",
    };
    const merged = mergeResearchActivityHistory([initial], [live], [], {
      revision: 10,
    });
    expect(merged.primaryActivities).toHaveLength(1);
    expect(merged.primaryActivities[0]).toMatchObject({
      eventId: initial.eventId,
      occurredAt: initial.occurredAt,
      summary: live.summary,
      phase: "formed",
    });
    const replayed = mergeResearchActivityHistory(
      merged.primaryActivities,
      [initial],
      [],
      { revision: 10 },
    );
    expect(replayed.primaryActivities[0].summary).toBe(live.summary);
  });
  it("keeps an active planning milestone before ResearchDesign confirmation", () => {
    const folded = foldResearchActivityPresentation(
      [planningStarted],
      [],
      null,
    );

    expect(
      folded.unscoped.map((row: { headline: string }) => row.headline),
    ).toEqual(["正在组织研究方案"]);
  });

  it("keeps planning start before formal confirmation", () => {
    const folded = foldResearchActivityPresentation(
      [
        planningStarted,
        {
          ...planningStarted,
          eventId: "research-design-confirmed",
          headline: "研究方案已确认",
          sequence: 2,
          occurredAt: "2026-10-07T03:03:03Z",
          activityKey: "research-design:design-1:v1:confirmed",
          phase: "confirmed",
        },
      ],
      [],
      null,
    );

    expect(
      folded.unscoped.map((row: { headline: string }) => row.headline),
    ).toEqual(["正在组织研究方案", "研究方案已确认"]);
  });

  it("keeps both failed planning attempts across retries", () => {
    const folded = foldResearchActivityPresentation(
      [
        planningStarted,
        {
          ...planningStarted,
          eventId: "planning-failed-1",
          headline: "本次研究方案生成未完成",
          sequence: 2,
          occurredAt: "2026-10-08T10:08:14Z",
          activityKey: "planning-attempt:attempt-1",
          phase: "failed",
        },
        {
          ...planningStarted,
          eventId: "planning-started-2",
          headline: "正在组织研究方案",
          sequence: 3,
          occurredAt: "2026-10-08T10:08:14.500Z",
          activityKey: "planning-attempt:attempt-2",
          phase: "started",
        },
        {
          ...planningStarted,
          eventId: "planning-failed-2",
          headline: "本次研究方案生成未完成",
          sequence: 4,
          occurredAt: "2026-10-08T10:08:15Z",
          activityKey: "planning-attempt:attempt-2",
          phase: "failed",
        },
      ],
      [],
      null,
    );

    expect(
      folded.unscoped
        .filter((row: { phase?: string | null }) => row.phase === "failed")
        .map((row: { eventId: string }) => row.eventId),
    ).toEqual(["planning-failed-1", "planning-failed-2"]);
    expect(
      folded.unscoped.filter(
        (row: { headline: string }) =>
          row.headline === "本次研究方案生成未完成",
      ),
    ).toHaveLength(2);
  });
});

it("groups historical workflow focuses while retaining every process and artifact", () => {
  const contexts = researchWorkUnitContexts([
    {
      substageInstanceId: "old-1",
      substageDefinitionId: "demo:recommendation",
      objective: "提出下一步建议。当前项目科研重点：验证长度效应。",
      workUnitIds: ["w1"],
    },
    {
      substageInstanceId: "old-2",
      substageDefinitionId: "demo:recommendation",
      objective: "提出下一步建议。当前项目科研重点：验证解释效应。",
      workUnitIds: ["w2"],
    },
  ]);
  const blocks = ["w1", "w2"].map((workUnitId, index) => ({
    workUnitId,
    rows: [{ eventId: workUnitId }],
    processActivities: [{ eventId: workUnitId }],
    currentStatus: "completed",
    terminalActivity: null,
    artifactRefs: [`artifact-${index}`],
    resultRefs: [`result-${index}`],
    scientificStateVersions: [index],
    startedAt: null,
    endedAt: null,
    latestOccurredAt: null,
  }));
  const grouped = coalesceResearchActivityBlocks(
    blocks,
    new Map(contexts.map((row) => [row.workUnitId, row.objective])),
    new Map(contexts.map((row) => [row.workUnitId, row.goalKey])),
  );
  expect(grouped).toHaveLength(1);
  expect(grouped[0].processActivities).toHaveLength(2);
  expect(grouped[0].rows).toHaveLength(2);
  expect(grouped[0].artifactRefs).toEqual(["artifact-0", "artifact-1"]);
  expect(contexts[0].objective).toContain("验证长度效应");
  expect(contexts[0].objective).toContain("验证解释效应");
  const separate = researchWorkUnitContexts([
    {
      substageInstanceId: "independent-a",
      substageDefinitionId: "demo:recommendation",
      objective: "判断中文问题。",
      workUnitIds: ["w1"],
    },
    {
      substageInstanceId: "independent-b",
      substageDefinitionId: "demo:recommendation",
      objective: "判断代码问题。",
      workUnitIds: ["w2"],
    },
  ]);
  expect(
    coalesceResearchActivityBlocks(
      blocks,
      new Map(separate.map((row) => [row.workUnitId, row.objective])),
      new Map(separate.map((row) => [row.workUnitId, row.goalKey])),
    ),
  ).toHaveLength(2);
});
