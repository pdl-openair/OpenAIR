import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type {
  WorkbenchArtifact,
  WorkbenchTaskModel,
} from "../../../../model/workbenchTask";
import { ResearchInterruptionPanel } from "../../ResearchInterruptionPanel";

afterEach(cleanup);

describe("ResearchInterruptionPanel", () => {
  it.each(["user_input", "system_failure", "user_stop"])(
    "keeps %s status and retained-results information without duplicating artifact selectors",
    (kind) => {
      const model = {
        revision: 1,
        interruption: {
          revision: 1,
          kind,
          summary: "研究暂未继续",
          impact: "后续步骤尚未完成",
          responsibility: "system",
          responsibilityLabel: "系统",
          userAction: "请查看协作区",
          affectedSteps: [],
          completedSteps: [],
          retainedResults: {
            artifacts: [
              { artifactId: "a", artifactState: "usable", version: 1 },
            ],
            resultCount: 1,
            summary: "已有成果继续保留",
            finalResultStatus: null,
          },
          controlFact: null,
          diagnostics: [],
        },
      } as unknown as WorkbenchTaskModel;
      render(
        <ResearchInterruptionPanel
          model={model}
          artifacts={[
            { id: "a", title: "研究问题与边界" } as WorkbenchArtifact,
          ]}
        />,
      );
      expect(screen.getByText("研究暂未继续")).toBeInTheDocument();
      expect(screen.getByText("请查看协作区")).toBeInTheDocument();
      expect(screen.getByText("已有成果继续保留")).toBeInTheDocument();
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
      expect(screen.queryByText("研究问题与边界")).not.toBeInTheDocument();
    },
  );
});
