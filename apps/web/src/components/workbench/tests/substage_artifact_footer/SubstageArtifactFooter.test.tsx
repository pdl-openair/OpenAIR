import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { WorkbenchArtifact } from "../../../../model/workbenchTask";
import { SubstageArtifactFooter } from "../../SubstageArtifactFooter";

function artifact(
  id: string,
  overrides: Partial<WorkbenchArtifact> = {},
): WorkbenchArtifact {
  const titles: Record<string, string> = {
    a: "研究问题与边界",
    b: "核心证据与来源",
    c: "风险、不确定性与下一步",
    hidden: "内部执行诊断",
  };
  return {
    id,
    taskId: "task-a",
    title: titles[id] ?? "研究成果",
    type: "report",
    format: "markdown",
    relatedStage: "problem_modeling",
    subStageInstanceId: "sub-a",
    artifactVisibility: "user",
    ...overrides,
  };
}

describe("SubstageArtifactFooter", () => {
  it("renders every visible Artifact as a deep link and highlights the stable selection", () => {
    const onSelectArtifact = vi.fn();
    render(
      <SubstageArtifactFooter
        taskId="task-a"
        substageInstanceId="sub-a"
        artifactRefs={["a", "b", "c", "hidden"]}
        artifacts={[
          artifact("a"),
          artifact("b"),
          artifact("c"),
          artifact("hidden", { artifactVisibility: "internal" }),
        ]}
        selection={{
          taskId: "task-a",
          substageInstanceId: "sub-a",
          artifactId: "b",
        }}
        artifactHref={(artifactId) =>
          `/workbench/task-a?artifact=${artifactId}`
        }
        onSelectArtifact={onSelectArtifact}
      />,
    );

    expect(screen.getByText("本步骤已形成的研究成果")).toBeInTheDocument();
    expect(screen.getByText("已形成 3 项，可随时切换")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "研究问题与边界" }),
    ).toHaveAttribute("href", "/workbench/task-a?artifact=a");
    expect(
      screen.getByRole("link", { name: "核心证据与来源" }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      screen.getByRole("link", { name: "风险、不确定性与下一步" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "内部执行诊断" }),
    ).not.toBeInTheDocument();

    const artifactCLink = screen.getByRole("link", {
      name: "风险、不确定性与下一步",
    });
    expect(fireEvent.click(artifactCLink)).toBe(false);
    expect(onSelectArtifact).toHaveBeenCalledTimes(1);
    expect(fireEvent.click(artifactCLink, { ctrlKey: true })).toBe(true);
    expect(onSelectArtifact).toHaveBeenCalledTimes(1);
    expect(onSelectArtifact).toHaveBeenCalledWith("c");
  });
});

describe("SubstageArtifactFooter round grouping", () => {
  it("groups artifacts by execution round when the substage re-ran", () => {
    const onSelectArtifact = vi.fn();
    render(
      <SubstageArtifactFooter
        taskId="task-a"
        substageInstanceId="sub-a"
        artifactRefs={["r1a", "r1b", "r2a"]}
        artifacts={[
          artifact("r1a", { title: "实验反馈与轮次分析" }),
          artifact("r1b", { title: "实验反馈与轮次分析（复）" }),
          artifact("r2a", { title: "实验反馈与轮次分析" }),
        ]}
        workUnits={[
          {
            workUnitId: "w1",
            executionDecisionId: "decision-r1",
            subStageInstanceId: "sub-a",
            status: "completed",
            dispatchedAt: "2026-10-14T13:25:41.000000Z",
            finishedAt: "2026-10-14T13:26:50.000000Z",
            outputArtifactRefs: ["r1a", "r1b"],
          },
          {
            workUnitId: "w2",
            executionDecisionId: "decision-r2",
            subStageInstanceId: "sub-a",
            status: "completed",
            dispatchedAt: "2026-10-15T10:05:12.000000Z",
            finishedAt: "2026-10-15T10:06:13.000000Z",
            outputArtifactRefs: ["r2a"],
          },
        ]}
        selection={{
          taskId: "task-a",
          substageInstanceId: "sub-a",
          artifactId: "r2a",
        }}
        artifactHref={(artifactId) => `/workbench/task-a?artifact=${artifactId}`}
        onSelectArtifact={onSelectArtifact}
      />,
    );

    expect(screen.getByText("已形成 3 项，共 2 轮，可随时切换")).toBeInTheDocument();
    const groups = screen.getAllByTestId("substage-artifact-round-group");
    expect(groups).toHaveLength(2);
    expect(groups[0]).toHaveAttribute("data-round-index", "1");
    expect(groups[1]).toHaveAttribute("data-round-index", "2");
    expect(within(groups[0]).getAllByRole("link")).toHaveLength(2);
    expect(within(groups[1]).getAllByRole("link")).toHaveLength(1);
    expect(
      within(groups[1]).getByRole("link", { name: "实验反馈与轮次分析" }),
    ).toHaveAttribute("aria-current", "page");
    // Round headers exist and are ordered.
    const headers = groups.map((group) => within(group).getByText(/^第 \d+ 轮/));
    expect(headers).toHaveLength(2);
  });

  it("stays flat without round headers when no workUnit facts arrive", () => {
    render(
      <SubstageArtifactFooter
        taskId="task-a"
        substageInstanceId="sub-a"
        artifactRefs={["a", "b"]}
        artifacts={[artifact("a"), artifact("b")]}
        selection={null}
        artifactHref={(artifactId) => `/workbench/task-a?artifact=${artifactId}`}
        onSelectArtifact={vi.fn()}
      />,
    );

    expect(screen.getByText("已形成 2 项，可随时切换")).toBeInTheDocument();
    expect(screen.queryAllByTestId("substage-artifact-round-group")).toHaveLength(0);
    expect(screen.getByRole("link", { name: "研究问题与边界" })).toBeInTheDocument();
  });
});
