import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ResultSource, Task } from "@openair/shared";

const mocks = vi.hoisted(() => ({
  fetchTask: vi.fn(),
  workbenchModelFromApiTask: vi.fn((task: Task) => ({ resultSource: task.resultSource ?? "none" })),
  loadCsResearchOpeWorkbenchFixture: vi.fn(),
  loadB2DemoWorkbenchFixture: vi.fn(),
}));

vi.mock("../../../api/client", () => ({ fetchTask: mocks.fetchTask }));
vi.mock("../../../model/workbenchTask", () => ({ workbenchModelFromApiTask: mocks.workbenchModelFromApiTask }));
vi.mock("../../../model/taskFromFixture", () => ({ taskFromWorkbenchModel: vi.fn() }));
vi.mock("../../../data/csResearchOpeFixture", () => ({
  CS_RESEARCH_OPE_FIXTURE_TASK_ID: "fixture-task",
  isB2DemoFixtureTask: vi.fn(() => false),
  loadB2DemoWorkbenchFixture: mocks.loadB2DemoWorkbenchFixture,
  loadCsResearchOpeWorkbenchFixture: mocks.loadCsResearchOpeWorkbenchFixture,
}));

import { loadTaskForPages, TaskIdentityMismatchError } from "../../loadTask";

function apiTask(resultSource: ResultSource): Task {
  return {
    taskId: `task-${resultSource}`,
    ownerId: "owner-1",
    workspaceId: "workspace-1",
    createdBy: "user-1",
    title: `${resultSource} task`,
    currentStage: "problem_modeling",
    status: "running",
    resultSource,
    stages: ["problem_modeling"],
    artifacts: [],
    confirmations: [],
    capabilityCalls: [],
  };
}

describe("authoritative page Task loading", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it.each<ResultSource>(["live", "replay", "demo", "none"])(
    "keeps the API source separate from %s result provenance",
    async (resultSource) => {
      const task = apiTask(resultSource);
      mocks.fetchTask.mockResolvedValue(task);

      const loaded = await loadTaskForPages(task.taskId, "zh-CN", { allowFixtureFallback: false });

      expect(loaded?.source).toBe("api");
      expect(loaded?.task).toBe(task);
      expect(loaded?.model.resultSource).toBe(resultSource);
      expect(mocks.loadCsResearchOpeWorkbenchFixture).not.toHaveBeenCalled();
      expect(mocks.loadB2DemoWorkbenchFixture).not.toHaveBeenCalled();
    },
  );

  it("preserves an authoritative absence without loading a fixture", async () => {
    mocks.fetchTask.mockResolvedValue(null);

    await expect(
      loadTaskForPages("fixture-task", "zh-CN", { allowFixtureFallback: false }),
    ).resolves.toBeNull();
    expect(mocks.loadCsResearchOpeWorkbenchFixture).not.toHaveBeenCalled();
    expect(mocks.loadB2DemoWorkbenchFixture).not.toHaveBeenCalled();
  });

  it("defaults to authoritative absence without fixture substitution", async () => {
    mocks.fetchTask.mockResolvedValue(null);

    await expect(loadTaskForPages("fixture-task")).resolves.toBeNull();
    expect(mocks.loadCsResearchOpeWorkbenchFixture).not.toHaveBeenCalled();
    expect(mocks.loadB2DemoWorkbenchFixture).not.toHaveBeenCalled();
  });

  it("rejects an API payload whose Task identity differs from the route", async () => {
    mocks.fetchTask.mockResolvedValue(apiTask("live"));

    await expect(loadTaskForPages("task-from-route")).rejects.toEqual(
      new TaskIdentityMismatchError("task-from-route", "task-live"),
    );
    expect(mocks.workbenchModelFromApiTask).not.toHaveBeenCalled();
  });

  it.each([
    ["401", new Error("HTTP 401")],
    ["403", new Error("HTTP 403")],
    ["5xx", new Error("HTTP 503")],
    ["network", new TypeError("Failed to fetch")],
  ])("preserves %s transport failures without fixture substitution", async (_name, error) => {
    mocks.fetchTask.mockRejectedValue(error);

    await expect(
      loadTaskForPages("fixture-task", "zh-CN", { allowFixtureFallback: false }),
    ).rejects.toBe(error);
    expect(mocks.loadCsResearchOpeWorkbenchFixture).not.toHaveBeenCalled();
    expect(mocks.loadB2DemoWorkbenchFixture).not.toHaveBeenCalled();
  });
});
