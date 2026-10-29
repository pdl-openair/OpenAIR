import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  mocks,
  renderWorkbench,
  openResearchDesignEdit,
  V1_TASK,
  MANUAL_V2_TASK,
  TASK_ID,
} from "./workbenchTestHarness";
import {
  researchDesignAvailableSubStages,
  researchDesignEditDraftFrom,
  researchDesignEditHasBlankWork,
  researchDesignEditUpdateFrom,
} from "../../../lib/researchDesignEdit";

async function chooseType(label = "假说形成") {
  const select = screen.getByTestId("workbench-research-design-edit-type-0-1");
  fireEvent.mouseDown(within(select).getByRole("combobox"));
  fireEvent.click(
    await screen.findByText(label, {
      selector: ".ant-select-item-option-content",
    }),
  );
}

describe("ResearchDesign subgoal selection", () => {
  it("saves a catalog type through the existing revision entry and releases it after deletion", async () => {
    mocks.updateTaskPlan.mockResolvedValue(MANUAL_V2_TASK);
    renderWorkbench({ ...V1_TASK, skillPack: "opentrajectory" });
    await openResearchDesignEdit();
    const add = screen.getByTestId("workbench-research-design-edit-add-step-0");
    await waitFor(() => expect(add).toBeEnabled());
    expect(mocks.fetchPackStageCatalog).toHaveBeenCalledWith("opentrajectory");
    fireEvent.click(add);
    const select = screen.getByTestId(
      "workbench-research-design-edit-type-0-1",
    );
    fireEvent.mouseDown(within(select).getByRole("combobox"));
    expect(
      screen.queryByText("明确研究问题", {
        selector: ".ant-select-item-option-content",
      }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      await screen.findByText("假说形成", {
        selector: ".ant-select-item-option-content",
      }),
    );
    expect(add).toBeDisabled();
    expect(
      screen.getAllByText("当前阶段没有可新增的工作类型").length,
    ).toBeGreaterThan(0);
    fireEvent.click(
      screen.getByTestId("workbench-research-design-edit-delete-work-0-1"),
    );
    expect(add).toBeEnabled();
    fireEvent.click(add);
    await chooseType();
    fireEvent.click(screen.getByTestId("workbench-draft-complete-edit"));
    expect(mocks.updateTaskPlan).not.toHaveBeenCalled();
    fireEvent.change(
      screen.getByTestId("workbench-research-design-edit-work-0-1"),
      { target: { value: "形成可由现有证据检验的假说。" } },
    );
    fireEvent.click(screen.getByTestId("workbench-draft-complete-edit"));
    await waitFor(() => expect(mocks.updateTaskPlan).toHaveBeenCalledTimes(1));
    expect(mocks.updateTaskPlan).toHaveBeenCalledWith(
      TASK_ID,
      expect.objectContaining({
        stages: expect.arrayContaining([
          expect.objectContaining({
            stageKey: "problem_modeling",
            substages: [
              expect.objectContaining({
                instanceId: "instance-1",
                substage: "question_refinement",
              }),
              expect.objectContaining({
                instanceId: null,
                substage: "hypothesis_formation",
                label: "假说形成",
                action: "形成可由现有证据检验的假说。",
              }),
            ],
          }),
        ]),
      }),
    );
  });

  it("disables addition when all types are already present or the catalog cannot be read", async () => {
    mocks.fetchPackStageCatalog.mockResolvedValue([
      {
        stageKey: "problem_modeling",
        allowedSubStages: [
          {
            key: "question_refinement",
            stageKey: "problem_modeling",
            label: "明确研究问题",
          },
        ],
      },
    ]);
    renderWorkbench();
    await openResearchDesignEdit();
    await waitFor(() =>
      expect(screen.queryByText("正在读取工作类型…")).not.toBeInTheDocument(),
    );
    expect(
      screen.getByTestId("workbench-research-design-edit-add-step-0"),
    ).toBeDisabled();
    expect(
      screen.getByTestId("workbench-research-design-edit-add-step-1"),
    ).toBeDisabled();
  });

  it("reports catalog read failure without allowing a fabricated type", async () => {
    mocks.fetchPackStageCatalog.mockRejectedValue(new Error("unavailable"));
    renderWorkbench();
    await openResearchDesignEdit();
    await screen.findAllByText("工作类型读取失败，请重新进入编辑后重试。");
    expect(
      screen.getByTestId("workbench-research-design-edit-add-step-0"),
    ).toBeDisabled();
  });

  it("preserves other stages and makes removed original types selectable again", () => {
    const plan = {
      ...V1_TASK.creationPlan!,
      stages: [
        ...V1_TASK.creationPlan!.stages,
        {
          stageKey: "solution_design" as const,
          title: "方案设计",
          description: "选择可复现的方法。",
          subStages: ["method_selection"],
          substages: [
            {
              instanceId: "method-1",
              substage: "method_selection",
              label: "方法论选型",
              action: "选择可复现的方法。",
              expectedArtifact: "method",
            },
          ],
        },
      ],
    };
    const draft = researchDesignEditDraftFrom(plan, 41);
    draft.addedSteps.push({
      localKey: "new-step-1",
      stageKey: "problem_modeling",
      substage: "hypothesis_formation",
      label: "假说形成",
      objective: "检验假说。",
    });
    expect(researchDesignEditHasBlankWork(draft)).toBe(false);
    expect(researchDesignEditUpdateFrom(draft).stages[1].substages).toEqual(
      plan.stages[1].substages,
    );
    draft.removedInstanceIds.push("instance-1");
    const catalog = [
      {
        stageKey: "problem_modeling" as const,
        allowedSubStages: [
          {
            key: "question_refinement",
            stageKey: "problem_modeling" as const,
            label: "明确研究问题",
          },
          {
            key: "hypothesis_formation",
            stageKey: "problem_modeling" as const,
            label: "假说形成",
          },
          {
            key: "disabled",
            stageKey: "problem_modeling" as const,
            label: "不可用类型",
            enabled: false,
          },
        ],
      },
    ];
    expect(
      researchDesignAvailableSubStages(draft, "problem_modeling", catalog).map(
        (entry) => entry.key,
      ),
    ).toEqual(["question_refinement"]);
    expect(
      researchDesignAvailableSubStages(draft, "solution_design", catalog),
    ).toEqual([]);
  });
});
