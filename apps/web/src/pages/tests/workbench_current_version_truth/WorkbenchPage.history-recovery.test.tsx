import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Task } from "@openair/shared";
import {
  mocks,
  TASK_ID,
  V1_GOAL,
  AGENT_V2_GOAL,
  V1_WORK,
  V1_TASK,
  MANUAL_V2_TASK,
  AGENT_V2_TASK,
  designVersion,
  renderWorkbench,
  openResearchDesignEdit,
} from "./workbenchTestHarness";

describe("research design history recovery", () => {
  async function chooseV1() {
    await openResearchDesignEdit();
    fireEvent.click(screen.getByText("修改记录"));
    fireEvent.click(
      await screen.findByTestId("workbench-research-design-history-version-1"),
    );
    return screen.findByTestId("workbench-research-design-restore");
  }
  it("restores historical semantics with current CAS, then presents the new authoritative version", async () => {
    const restored: Task = {
      ...MANUAL_V2_TASK,
      revision: 43,
      goalSummary: V1_GOAL,
      creationPlan: V1_TASK.creationPlan,
      researchDesignVersions: [
        ...MANUAL_V2_TASK.researchDesignVersions!.map((v) => ({
          ...v,
          status: "superseded" as const,
        })),
        designVersion(3, "draft", V1_GOAL, V1_WORK),
      ] as Task["researchDesignVersions"],
    };
    mocks.updateTaskPlan.mockResolvedValue(restored);
    mocks.fetchTask.mockResolvedValue(restored);
    renderWorkbench(MANUAL_V2_TASK);
    fireEvent.click(await chooseV1());
    await waitFor(() => expect(mocks.updateTaskPlan).toHaveBeenCalledTimes(1));
    expect(mocks.updateTaskPlan).toHaveBeenCalledWith(
      TASK_ID,
      expect.objectContaining({
        expectedRevision: 42,
        goalSummary: V1_GOAL,
        stages: expect.arrayContaining([
          expect.objectContaining({
            substages: expect.arrayContaining([
              expect.objectContaining({
                instanceId: "instance-1",
                action: V1_WORK,
              }),
            ]),
          }),
        ]),
      }),
    );
    await waitFor(() =>
      expect(
        screen.getByTestId("workbench-research-design-edit-goal"),
      ).toHaveValue(V1_GOAL),
    );
    const v3 = await screen.findByTestId(
      "workbench-research-design-history-version-3",
    );
    expect(within(v3).getByText("当前")).toBeVisible();
    expect(
      screen.getByTestId("workbench-research-design-history-version-1"),
    ).toBeVisible();
    expect(
      screen.getByTestId("workbench-research-design-history-version-2"),
    ).toBeVisible();
  });
  it("CAS conflict refreshes newer authority, remains visible and never silently retries", async () => {
    mocks.updateTaskPlan.mockRejectedValue(new Error("task_revision_conflict"));
    mocks.fetchTask.mockResolvedValue(AGENT_V2_TASK);
    renderWorkbench(MANUAL_V2_TASK);
    fireEvent.click(await chooseV1());
    await waitFor(() =>
      expect(
        screen.getByTestId("workbench-research-design-edit-goal"),
      ).toHaveValue(AGENT_V2_GOAL),
    );
    expect(
      await screen.findByText(
        "撤销没有生效：研究方案在你查看历史期间已更新。已重新载入最新版本，请确认后再试。",
      ),
    ).toBeVisible();
    expect(mocks.updateTaskPlan).toHaveBeenCalledTimes(1);
    expect(mocks.fetchTask).toHaveBeenCalledWith(TASK_ID);
    expect(
      within(
        screen.getByTestId("workbench-research-design-history-version-2"),
      ).getByText("当前"),
    ).toBeVisible();
  });
  it("unsaved manual edits block history restore without an API mutation", async () => {
    renderWorkbench(MANUAL_V2_TASK);
    await openResearchDesignEdit();
    fireEvent.change(
      screen.getByTestId("workbench-research-design-edit-goal"),
      { target: { value: "unsaved goal" } },
    );
    fireEvent.click(screen.getByText("修改记录"));
    fireEvent.click(
      await screen.findByTestId("workbench-research-design-history-version-1"),
    );
    const restore = await screen.findByTestId(
      "workbench-research-design-restore",
    );
    expect(restore).toBeDisabled();
    fireEvent.click(restore);
    expect(mocks.updateTaskPlan).not.toHaveBeenCalled();
    expect(
      screen.getByTestId("workbench-research-design-edit-goal"),
    ).toHaveValue("unsaved goal");
  });
});
