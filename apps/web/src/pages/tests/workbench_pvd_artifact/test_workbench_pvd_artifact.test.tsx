// CANONICAL_SHAPE_TEST_FIXTURE: verifies the Workbench page, not a real research run.
import { App } from "antd";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { Task } from "@openair/shared";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PageHeaderProvider } from "../../../context/PageHeaderContext";

const mocks = vi.hoisted(() => ({ model: vi.fn(), content: vi.fn() }));
vi.mock("../../../hooks/useWorkbenchTaskModel", () => ({
  useWorkbenchTaskModel: mocks.model,
}));
vi.mock("../../../hooks/useDurableRunProjection", () => ({
  useDurableRunProjection: () => ({ run: null, refresh: vi.fn() }),
}));
vi.mock("../../../hooks/useCollaborationProjection", () => ({
  useCollaborationProjection: () => ({ projection: null, refresh: vi.fn() }),
}));
vi.mock("../../../hooks/useTaskRecoveryProjection", () => ({
  useTaskRecoveryProjection: () => null,
}));
vi.mock("../../../hooks/useWorkbenchUserActions", () => ({
  useWorkbenchUserActions: () => ({
    lifecycleKind: "canonical",
    snapshot: {
      allowedActions: [],
      executionStopped: false,
      unrecoverableMessage: null,
    },
    items: [],
    controller: {
      busy: false,
      intent: null,
      receipt: null,
      operationState: "idle",
      reconcile: vi.fn(),
    },
    operationState: () => ({ state: "idle" }),
    explicitRetry: vi.fn(),
    resume: vi.fn(),
    stop: vi.fn(),
    submitSupplement: vi.fn(),
  }),
}));
vi.mock("../../../context/AuthContext", () => ({
  useAuth: () => ({ logout: vi.fn() }),
}));
vi.mock("../../../api/agent", () => ({
  fetchAgentMessages: vi.fn().mockResolvedValue({ messages: [] }),
}));
vi.mock("../../../components/workbench/ResearchActivityStream", () => ({
  ResearchActivityStream: ({
    children,
  }: {
    children: (value: unknown) => ReactNode;
  }) =>
    children({
      facts: [],
      summary: null,
      connection: null,
      renderFact: () => null,
    }),
}));
vi.mock("../../../components/workbench/WorkbenchFlowNav", () => ({
  WorkbenchFlowNav: ({
    onSelectSubStage,
  }: {
    onSelectSubStage: (id: string) => void;
  }) => (
    <button onClick={() => onSelectSubStage("test-records-substage")}>
      查看研究记录步骤
    </button>
  ),
}));
vi.mock("../../../components/artifacts/ArtifactVersionDrawer", () => ({
  ArtifactVersionDrawer: () => null,
}));
vi.mock("../../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../api/client")>()),
  fetchArtifactContent: mocks.content,
  fetchTaskExecutionPolicy: vi.fn().mockResolvedValue({
    currentMode: "automatic_research",
    pendingMode: null,
  }),
}));

import WorkbenchPage from "../../WorkbenchPage";
import { workbenchModelFromApiTask } from "../../../model/workbenchTask";

const TASK_ID = "test-pvd-workbench";
const ARTIFACT_ID = "test-pvd-records";
const ARTIFACT_TITLE = "文献研究记录（合同测试）";

function recordDocument() {
  return {
    research_records: [
      {
        record_id: "a",
        source_id: "source",
        sample_id: "样品 A",
        outcomes: [{ metric: "硬度", value: 12, unit: "GPa" }],
      },
      {
        record_id: "b",
        source_id: "source",
        sample_id: "样品 B",
        outcomes: [{ metric: "硬度", value: 15000, unit: "MPa" }],
        process: { pressure: { fact_status: "known", value: 0.6, unit: "Pa" } },
      },
    ],
    source_provenance: [
      {
        source_id: "source",
        citation: "合同测试来源（非真实研究）",
        doi: "10.0000/workbench-test",
        access_state: "verified",
      },
    ],
  };
}

function task(): Task {
  return {
    taskId: TASK_ID,
    title: "PVD 中间产物区合同测试",
    revision: 1,
    goalSummary: "验证正式工作台产物加载与展示",
    domain: "research_task",
    skillPack: "pvd",
    currentStage: "execution_validation",
    status: "running",
    resultSource: "live",
    executionMode: "automatic_research",
    creationStatus: "confirmed",
    createdAt: "2026-10-09T00:00:00Z",
    updatedAt: "2026-10-09T00:00:00Z",
    stages: [
      "problem_modeling",
      "solution_design",
      "execution_validation",
      "insight_generation",
    ],
    stageDetails: [],
    researchDesignVersions: [],
    confirmations: [],
    capabilityCalls: [],
    trustRecords: [],
    userVisibleEvents: [],
    artifacts: [
      {
        id: ARTIFACT_ID,
        taskId: TASK_ID,
        sourceTaskId: TASK_ID,
        kind: "execution_output",
        title: ARTIFACT_TITLE,
        artifactType: "pvd.research_records",
        artifactState: "usable",
        createdAt: "2026-10-09T00:00:00Z",
        format: "json",
        relatedStage: "execution_validation",
        storageType: "external",
      },
    ],
  };
}

function openWorkbench(
  input: Task,
  document: object,
  deepLink = false,
  producer?: string,
) {
  const model = workbenchModelFromApiTask(input);
  if (!deepLink) {
    const stage = model.stages.find(
      (row) => row.stage === "execution_validation",
    )!;
    stage.subStages = [
      {
        id: "test-records-substage",
        subStage: "records",
        label: "研究记录",
        status: "completed",
        artifactRefs: [ARTIFACT_ID],
      },
    ];
    model.artifacts[0].subStageInstanceId = "test-records-substage";
  }
  if (producer)
    model.capabilityCalls.push({
      id: "test-closure-call",
      name: producer,
    } as (typeof model.capabilityCalls)[number]);
  mocks.model.mockReturnValue({
    status: "ready",
    model,
    source: "api",
    reload: vi.fn(),
    silentReload: vi.fn(),
    isRefreshing: false,
    progressStreamStale: false,
  });
  mocks.content.mockResolvedValue({
    artifactId: ARTIFACT_ID,
    format: "json",
    sourceMode: "live",
    content: JSON.stringify(document),
  });
  render(
    <MemoryRouter
      initialEntries={[
        `/workbench/${TASK_ID}${deepLink ? `?artifact=${ARTIFACT_ID}` : ""}`,
      ]}
    >
      <App>
        <PageHeaderProvider>
          <Routes>
            <Route path="/workbench/:taskId" element={<WorkbenchPage />} />
          </Routes>
        </PageHeaderProvider>
      </App>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
});

describe("PVD artifacts through the actual Workbench page center", () => {
  it("hydrates and automatically renders a converted experiment series inside the real center", async () => {
    const body = recordDocument();
    const template = body.research_records[0];
    body.research_records = [0.2, 0.4, 0.6].map((pressure, index) => ({
      ...template,
      record_id: `trend-${index}`,
      sample_id: `趋势样品 ${index + 1}`,
      material: { film_composition: { fact_status: "known", value: "TiN" } },
      process: {
        pressure: {
          fact_status: "known",
          value: index === 1 ? "0.0004" : pressure,
          unit: index === 1 ? "kPa" : "Pa",
        },
        power: { fact_status: "known", value: 200, unit: "W" },
      },
      outcomes: [
        {
          metric: "硬度",
          value: [12, 15000, 14][index],
          unit: index === 1 ? "MPa" : "GPa",
        },
      ],
    }));
    openWorkbench(task(), body, true);
    const center = screen.getByTestId("workbench-center");
    const chart = await within(center).findByTestId("pvd-experiment-trends");
    expect(mocks.content).toHaveBeenCalledWith(TASK_ID, ARTIFACT_ID);
    expect(chart).toHaveTextContent("硬度 随 压力 的变化");
    expect(chart).toHaveTextContent("0.4 Pa → 15 GPa");
    expect(chart).toHaveTextContent("原文：0.0004 kPa → 15000 MPa");
    expect(chart.querySelectorAll("polyline")).toHaveLength(1);
    expect(within(chart).getAllByTestId("trend-point")).toHaveLength(3);
    const link = within(chart).getAllByRole("link", { name: /查看来源/ })[1];
    expect(center).toContainElement(
      document.getElementById(link.getAttribute("href")!.slice(1)),
    );
    expect(
      within(screen.getByTestId("workbench-right-panel")).queryByTestId(
        "pvd-experiment-trends",
      ),
    ).not.toBeInTheDocument();
  });

  it("selects and hydrates an external artifact into the center with converted bars and original provenance", async () => {
    const input = task();
    input.finalResult = {
      finalResultId: "final-selection",
      completionDecisionId: "decision-selection",
      scientificStateVersion: 1,
      disposition: "full",
      directAnswer: "最终总结不得遮住选择的成果",
      keyFindings: [],
      evidenceRefs: [],
      scientificResult: {},
      scientificSourceRefs: [],
      uncertainty: [],
      gaps: [],
      limitations: [],
      acceptedScientificFacts: {},
      validatedAt: "2026-10-09T00:00:00Z",
      validationOutcome: {
        contractVersion: "scientific_completion_integrity_v3",
        validatorRef: "test",
        status: "passed",
        checkIds: [],
        validatedAt: "2026-10-09T00:00:00Z",
      },
    };
    openWorkbench(input, recordDocument());
    fireEvent.click(screen.getByRole("button", { name: "查看研究记录步骤" }));
    expect(
      fireEvent.click(
        await screen.findByRole("link", { name: ARTIFACT_TITLE }),
      ),
    ).toBe(false);
    const center = screen.getByTestId("workbench-center");
    expect(
      within(center).queryByTestId("final-result-panel"),
    ).not.toBeInTheDocument();
    const chart = await within(center).findByTestId(
      "pvd-reported-values-chart",
    );
    expect(mocks.content).toHaveBeenCalledWith(TASK_ID, ARTIFACT_ID);
    expect(
      within(center).getByTestId("workbench-durable-artifact-projection"),
    ).toHaveAttribute("data-artifact-id", ARTIFACT_ID);
    expect(chart).toHaveTextContent("12 GPa");
    expect(chart).toHaveTextContent("原文：15000 MPa；换算：15 GPa");
    const bars = chart.querySelectorAll(".bg-brand");
    expect(bars).toHaveLength(2);
    expect(bars[0]).toHaveStyle({ width: "80%" });
    expect(bars[1]).toHaveStyle({ width: "100%" });
    const link = within(chart).getByRole("link", {
      name: "样品 B · 查看条件与来源",
    });
    const record = document.getElementById(
      link.getAttribute("href")!.slice(1),
    )!;
    expect(center).toContainElement(record);
    expect(record).toHaveTextContent("15000 MPa");
    expect(record).toHaveTextContent("压力：0.6 Pa");
    expect(
      within(record).getByRole("link", { name: "合同测试来源（非真实研究）" }),
    ).toHaveAttribute("href", "https://doi.org/10.0000/workbench-test");
    expect(
      within(screen.getByTestId("workbench-right-panel")).queryByTestId(
        "pvd-reported-values-chart",
      ),
    ).not.toBeInTheDocument();
  });

  it("restores a deep-linked complete closure output through its formal producer call", async () => {
    const input = task();
    input.artifacts[0].artifactType =
      "pvd.literature_experiment_record_verification.planned_output";
    input.artifacts[0].sourceCapabilityCallId = "test-closure-call";
    openWorkbench(
      input,
      recordDocument(),
      true,
      "pvd_literature_evidence_acquisition_closure",
    );
    expect(
      await within(screen.getByTestId("workbench-center")).findByTestId(
        "pvd-reported-values-chart",
      ),
    ).toBeVisible();
  });

  it("keeps records readable in the center and silently omits a chart without resolvable sources", async () => {
    const body = { ...recordDocument(), source_provenance: [] };
    openWorkbench(task(), body, true);
    const center = screen.getByTestId("workbench-center");
    expect(
      await within(center).findByTestId("pvd-research-record-view"),
    ).toHaveTextContent("样品 B");
    expect(
      within(center).queryByTestId("pvd-reported-values-chart"),
    ).not.toBeInTheDocument();
    expect(center).not.toHaveTextContent("暂无图表");
    expect(center).not.toHaveTextContent("无法绘图");
  });
});
