import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  mocks,
  V1_TASK,
  V1_GOAL,
  V1_WORK,
  MANUAL_V2_GOAL,
  MANUAL_V2_WORK,
  designVersion,
  renderWorkbench,
} from "./workbenchTestHarness";
import type { Task } from "@openair/shared";
import { ConfirmedResearchPlan } from "../../../components/workbench/ConfirmedResearchPlan";

function confirmedTask(status: Task["status"] = "running"): Task {
  return {
    ...V1_TASK,
    status,
    creationStatus: "confirmed",
    researchDesignVersions: [designVersion(1, "confirmed", V1_GOAL, V1_WORK)],
  };
}

describe("Confirmed research plan remains readable during execution", () => {
  it("keeps step dependencies and acceptance requirements hidden until the user expands them", () => {
    const design = designVersion(1, "confirmed", V1_GOAL, V1_WORK);
    const upstream = design.instances[0];
    design.instances.push({
      ...upstream,
      substageInstanceId: "instance-2",
      objective: "验证研究结果",
      dependencies: [
        {
          dependencyId: "dependency-1",
          upstreamInstanceRef: {
            substageInstanceId: upstream.substageInstanceId,
            instanceRevision: upstream.instanceRevision,
          },
          conditionType: "instance_completed",
          requiredArtifactTypes: [],
          confirmationTargetRef: null,
        },
      ],
      acceptanceCriteria: [
        {
          criterionId: "criterion-1",
          description: "保留可追溯的实验输出",
          criterionKind: "target_output",
          coverageTargetIds: [],
          requiredArtifactType: null,
          judgmentRequirement: "deterministic",
          evaluatorType: "deterministic",
          evaluatorRef: null,
        },
      ],
    });
    render(<ConfirmedResearchPlan design={design} />);
    expect(screen.getAllByText(V1_WORK, { exact: false })[0]).toBeVisible();
    expect(screen.getByText("验证研究结果", { exact: false })).toBeVisible();
    const requirement = screen.getByText("保留可追溯的实验输出");
    const dependency = screen.getByText(/^依赖：/);
    expect(requirement).not.toBeVisible();
    expect(dependency).not.toBeVisible();
    const toggle = screen.getByText("查看详细要求");
    fireEvent.click(toggle);
    expect(requirement).toBeVisible();
    expect(dependency).toBeVisible();
    fireEvent.click(toggle);
    expect(requirement).not.toBeVisible();
    expect(dependency).not.toBeVisible();
  });

  it("opens the confirmed snapshot, keeps reading through progress refresh, and returns to execution", async () => {
    const task = confirmedTask();
    const view = renderWorkbench(task);
    fireEvent.click(
      await screen.findByRole("button", { name: "研究方案 · 已确认" }),
    );
    const plan = await screen.findByTestId("workbench-confirmed-research-plan");
    expect(plan).toHaveTextContent(V1_GOAL);
    expect(plan).toHaveTextContent(V1_WORK);
    expect(plan).toHaveAttribute("data-design-version", "1");
    expect(within(plan).queryByRole("textbox")).not.toBeInTheDocument();
    const body = screen.getByTestId("workbench-center-scroll-body");
    body.scrollTop = 173;
    view.updateTask({ ...task, revision: task.revision + 1 });
    expect(
      screen.getByTestId("workbench-confirmed-research-plan"),
    ).toHaveTextContent(V1_GOAL);
    expect(body.scrollTop).toBe(173);
    fireEvent.click(screen.getByRole("button", { name: "查看旧节点" }));
    expect(
      screen.queryByTestId("workbench-confirmed-research-plan"),
    ).not.toBeInTheDocument();
    expect(mocks.confirmResearchDesign).not.toHaveBeenCalled();
    expect(mocks.startTask).not.toHaveBeenCalled();
    expect(mocks.agentReviseResearchDesign).not.toHaveBeenCalled();
    expect(mocks.updateTaskPlan).not.toHaveBeenCalled();
  });

  it("keeps a confirmed version readable when a newer draft differs from the creation plan", async () => {
    const task = confirmedTask("completed");
    task.creationPlan = { ...task.creationPlan!, goalSummary: MANUAL_V2_GOAL };
    task.researchDesignVersions!.push(
      designVersion(2, "draft", MANUAL_V2_GOAL, MANUAL_V2_WORK),
    );
    renderWorkbench(task, {
      initialEntries: [`/workbench/${task.taskId}?view=research-plan`],
    });
    const plan = await screen.findByTestId("workbench-confirmed-research-plan");
    expect(plan).toHaveTextContent(V1_GOAL);
    expect(plan).not.toHaveTextContent(MANUAL_V2_GOAL);
    expect(plan).toHaveAttribute("data-design-version", "1");
  });

  it("does not label an unconfirmed draft as a confirmed plan", async () => {
    renderWorkbench(V1_TASK);
    await screen.findByTestId("workbench-draft-start-confirmation");
    expect(
      screen.queryByRole("button", { name: "研究方案 · 已确认" }),
    ).not.toBeInTheDocument();
  });
});
