import type { Task } from "@openair/shared";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { CollaborationProjection } from "../../../../api/client";
import { workbenchModelFromApiTask } from "../../../../model/workbenchTask";
import { buildWorkbenchScreenView } from "../../../../model/workbenchScreenView";
import { AgentCollaborationOverview } from "../../AgentCollaborationOverview";

function canonicalModel() {
  const task: Task & { progressProjection: unknown } = {
    taskId: "current-research",
    revision: 12,
    ownerId: "owner",
    workspaceId: "workspace",
    createdBy: "user",
    title: "当前研究",
    goalSummary: "形成有来源的证据总结",
    currentStage: "solution_design",
    status: "running",
    resultSource: "live",
    stages: ["solution_design"],
    artifacts: [],
    confirmations: [],
    capabilityCalls: [],
    progressProjection: {
      revision: 12,
      substageProgress: [],
      stageProgress: [],
      taskProgress: {
        status: "running",
        activeStageKeys: [],
        awaitingConfirmationRefs: [],
      },
      workbenchProjection: {
        collaborationNodes: [],
        collaborationEvents: [],
        toolResourceRecords: [],
        artifactRefs: [],
      },
    },
  };
  const model = workbenchModelFromApiTask(task);
  expect(model.progressTrust.kind).toBe("canonical");
  return model;
}

function projection(
  model: ReturnType<typeof canonicalModel>,
): CollaborationProjection {
  return {
    taskId: model.taskId,
    revision: model.revision!,
    status: "running",
    statusLabel: "运行中",
    collaboration: {
      actual: [],
      handoffs: [],
      needsUserDecision: false,
      pendingDecisions: [],
    },
    progress: { status: "running", stages: [] },
    resources: { skills: [], materials: [], sources: [], files: [] },
    artifacts: {
      confirmableCount: 0,
      items: [
        {
          artifactId: "current-evidence",
          title: "当前版本证据表",
          kind: "source_table",
          format: "markdown",
          status: "usable",
          confirmable: false,
          provenance: "live_confirmed_run",
          createdAt: "2026-07-28T00:00:04Z",
        },
      ],
    },
  };
}

function expandOverview() {
  const toggle = screen.getByTestId("agent-collaboration-overview-toggle");
  if (toggle.getAttribute("aria-expanded") === "false") fireEvent.click(toggle);
}

describe("actual collaboration identity boundary", () => {
  it("opens the current artifact and removes the action when its response becomes stale", () => {
    const model = canonicalModel();
    expect(model.progressTrust.kind).toBe("canonical");
    const collaboration = projection(model);
    const currentScreen = buildWorkbenchScreenView(model, collaboration);
    const select = vi.fn();
    const view = render(
      <AgentCollaborationOverview
        taskId={model.taskId}
        model={model}
        screen={currentScreen}
        collaboration={collaboration}
        onSelectArtifact={select}
      />,
    );
    expandOverview();
    fireEvent.click(
      screen.getAllByRole("button", { name: /当前版本证据表/ })[0],
    );
    expect(select).toHaveBeenCalledWith("current-evidence");
    view.rerender(
      <AgentCollaborationOverview
        taskId={model.taskId}
        model={model}
        screen={currentScreen}
        collaboration={{
          ...collaboration,
          revision: collaboration.revision - 1,
        }}
        onSelectArtifact={select}
      />,
    );
    expect(
      screen.queryAllByRole("button", { name: /当前版本证据表/ }),
    ).toHaveLength(0);
    expect(select).toHaveBeenCalledTimes(1);
  });

  it.each([
    "task",
    "screen_revision",
    "model_revision",
    "trust",
    "projection_task",
  ])("rejects %s identity mismatch", (boundary) => {
    const base = canonicalModel();
    const collaboration = projection(base);
    const model =
      boundary === "model_revision"
        ? { ...base, progressRevision: base.progressRevision! - 1 }
        : boundary === "trust"
          ? { ...base, progressTrust: { kind: "legacy" as const } }
          : base;
    const currentScreen = buildWorkbenchScreenView(base, collaboration);
    const guardedScreen =
      boundary === "screen_revision"
        ? { ...currentScreen, revision: currentScreen.revision - 1 }
        : currentScreen;
    render(
      <AgentCollaborationOverview
        taskId={boundary === "task" ? "different-task" : base.taskId}
        model={model}
        screen={guardedScreen}
        collaboration={
          boundary === "projection_task"
            ? { ...collaboration, taskId: "different-task" }
            : collaboration
        }
      />,
    );
    expandOverview();
    expect(
      screen.queryAllByRole("button", { name: /当前版本证据表/ }),
    ).toHaveLength(0);
  });
});
