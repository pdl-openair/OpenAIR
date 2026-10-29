import { App } from "antd";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchArtifacts: vi.fn(),
  fetchArtifactContentById: vi.fn(),
  fetchArtifactDetail: vi.fn(),
  fetchArtifactRelations: vi.fn(),
  fetchTaskTitleOptions: vi.fn(),
  reviewArtifact: vi.fn(),
  confirmArtifactScopeExpansion: vi.fn(),
  reuseArtifact: vi.fn(),
  uploadArtifact: vi.fn(),
}));

vi.mock("../../../api/client", () => mocks);
vi.mock("../../../components/artifacts/ArtifactRenderer", () => ({
  ArtifactRenderer: () => <div data-testid="artifact-renderer" />,
}));
vi.mock("../../../components/artifacts/ArtifactVersionDrawer", () => ({
  ArtifactVersionDrawer: () => null,
}));

import ArtifactLibraryPage from "../../ArtifactLibraryPage";

const TASK_A_ARTIFACT = {
  id: "artifact-a",
  taskId: "task-a",
  sourceTaskId: "task-a",
  sourceTaskTitle: "Task A",
  kind: "analysis_report",
  origin: "generated",
  title: "Task A artifact",
  summary: "summary",
  format: "markdown",
  createdAt: "2026-10-05T00:00:00Z",
  artifactState: "needs_review",
  humanGate: {
    canConfirmPass: false,
    canUseDownstream: false,
    requiresHumanReview: true,
    requiresScopeExpansionConfirmation: false,
    allowedActions: [],
    primaryAction: "",
    blockedReason: null,
  },
};

const TASK_B_ARTIFACT = {
  ...TASK_A_ARTIFACT,
  id: "artifact-b",
  taskId: "task-b",
  sourceTaskId: "task-b",
  sourceTaskTitle: "Task B",
  title: "Task B artifact",
};

function deferred<T>() {
  let resolve: (value: T) => void = () => {};
  let reject: (reason?: unknown) => void = () => {};
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

type ArtifactPage = {
  artifacts: Array<typeof TASK_A_ARTIFACT>;
  total: number;
};

function RouteHarness() {
  const navigate = useNavigate();
  const location = useLocation();
  return (
    <>
      <button
        type="button"
        onClick={() =>
          navigate("/artifacts?sourceTaskId=task-b&artifactId=artifact-a")
        }
      >
        switch task route
      </button>
      <button
        type="button"
        onClick={() => navigate("/artifacts?sourceTaskId=task-b")}
      >
        switch task list
      </button>
      <output data-testid="route-location">{`${location.pathname}${location.search}`}</output>
      <ArtifactLibraryPage />
    </>
  );
}

describe("Artifact deep-link Task identity", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.fetchArtifacts.mockResolvedValue({
      artifacts: [],
      total: 0,
    });
    mocks.fetchArtifactDetail.mockResolvedValue(TASK_A_ARTIFACT);
    mocks.fetchArtifactContentById.mockResolvedValue({
      content: "# report",
      sourceMode: "live",
    });
    mocks.fetchArtifactRelations.mockResolvedValue([]);
    mocks.fetchTaskTitleOptions.mockResolvedValue({ tasks: [], total: 0 });
  });

  it("keeps the newest Task list when the prior Task response arrives late", async () => {
    const taskARequest = deferred<ArtifactPage>();
    const taskBRequest = deferred<ArtifactPage>();
    mocks.fetchArtifacts.mockImplementation(({ sourceTaskId }: { sourceTaskId?: string }) =>
      sourceTaskId === "task-a" ? taskARequest.promise : taskBRequest.promise,
    );

    const { container } = render(
      <App>
        <MemoryRouter initialEntries={["/artifacts?sourceTaskId=task-a"]}>
          <RouteHarness />
        </MemoryRouter>
      </App>,
    );

    await waitFor(() =>
      expect(mocks.fetchArtifacts).toHaveBeenCalledWith(
        expect.objectContaining({ sourceTaskId: "task-a" }),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "switch task list" }));
    await waitFor(() =>
      expect(mocks.fetchArtifacts).toHaveBeenCalledWith(
        expect.objectContaining({ sourceTaskId: "task-b" }),
      ),
    );

    await act(async () => {
      taskBRequest.resolve({ artifacts: [TASK_B_ARTIFACT], total: 7 });
    });
    expect(await screen.findByText("Task B artifact")).toBeVisible();
    expect(screen.getByText("共 7 条")).toBeVisible();
    expect(container.querySelector(".ant-spin-spinning")).toBeNull();

    await act(async () => {
      taskARequest.resolve({ artifacts: [TASK_A_ARTIFACT], total: 99 });
    });
    expect(screen.getByText("Task B artifact")).toBeVisible();
    expect(screen.queryByText("Task A artifact")).not.toBeInTheDocument();
    expect(screen.queryByTestId("artifact-renderer")).not.toBeInTheDocument();
    expect(screen.getByText("共 7 条")).toBeVisible();
    expect(screen.queryByText(/加载失败/)).not.toBeInTheDocument();
    expect(container.querySelector(".ant-spin-spinning")).toBeNull();
  });

  it("ignores a late failure from the prior Task after the current Task succeeds", async () => {
    const taskARequest = deferred<ArtifactPage>();
    const taskBRequest = deferred<ArtifactPage>();
    mocks.fetchArtifacts.mockImplementation(({ sourceTaskId }: { sourceTaskId?: string }) =>
      sourceTaskId === "task-a" ? taskARequest.promise : taskBRequest.promise,
    );

    const { container } = render(
      <App>
        <MemoryRouter initialEntries={["/artifacts?sourceTaskId=task-a"]}>
          <RouteHarness />
        </MemoryRouter>
      </App>,
    );
    await waitFor(() => expect(mocks.fetchArtifacts).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "switch task list" }));
    await waitFor(() => expect(mocks.fetchArtifacts).toHaveBeenCalledTimes(2));

    await act(async () => {
      taskBRequest.resolve({ artifacts: [TASK_B_ARTIFACT], total: 3 });
    });
    expect(await screen.findByText("Task B artifact")).toBeVisible();

    await act(async () => {
      taskARequest.reject(new Error("late Task A failure"));
    });
    expect(screen.getByText("Task B artifact")).toBeVisible();
    expect(screen.getByText("共 3 条")).toBeVisible();
    expect(screen.queryByText(/加载失败/)).not.toBeInTheDocument();
    expect(container.querySelector(".ant-spin-spinning")).toBeNull();
  });

  it("fails closed when a filtered response contains another Task's Artifact", async () => {
    mocks.fetchArtifacts.mockResolvedValue({
      artifacts: [TASK_A_ARTIFACT],
      total: 1,
    });

    const { container } = render(
      <App>
        <MemoryRouter initialEntries={["/artifacts?sourceTaskId=task-b"]}>
          <RouteHarness />
        </MemoryRouter>
      </App>,
    );

    expect(await screen.findByText(/返回的产物不属于当前项目/)).toBeVisible();
    expect(screen.queryByText("Task A artifact")).not.toBeInTheDocument();
    expect(screen.queryByText(/共 \d+ 条/)).not.toBeInTheDocument();
    await waitFor(() =>
      expect(container.querySelector(".ant-spin-spinning")).toBeNull(),
    );
  });

  it("clears the previous Task list before the next Task request completes", async () => {
    const taskBRequest = deferred<ArtifactPage>();
    mocks.fetchArtifacts.mockImplementation(({ sourceTaskId }: { sourceTaskId?: string }) =>
      sourceTaskId === "task-a"
        ? Promise.resolve({ artifacts: [TASK_A_ARTIFACT], total: 1 })
        : taskBRequest.promise,
    );

    render(
      <App>
        <MemoryRouter initialEntries={["/artifacts?sourceTaskId=task-a"]}>
          <RouteHarness />
        </MemoryRouter>
      </App>,
    );
    expect(await screen.findByText("Task A artifact")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "switch task list" }));
    await waitFor(() =>
      expect(screen.queryByText("Task A artifact")).not.toBeInTheDocument(),
    );
  });

  it("drops a preview when the route switches to a different source Task", async () => {
    mocks.fetchArtifacts.mockResolvedValue({
      artifacts: [TASK_A_ARTIFACT],
      total: 1,
    });
    render(
      <App>
        <MemoryRouter
          initialEntries={[
            "/artifacts?sourceTaskId=task-a&artifactId=artifact-a",
          ]}
        >
          <RouteHarness />
        </MemoryRouter>
      </App>,
    );

    expect(await screen.findByTestId("artifact-renderer")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "switch task route" }));

    await waitFor(() =>
      expect(screen.queryByTestId("artifact-renderer")).not.toBeInTheDocument(),
    );
    await waitFor(() => {
      expect(screen.getByTestId("route-location")).toHaveTextContent(
        "sourceTaskId=task-b",
      );
      expect(screen.getByTestId("route-location")).not.toHaveTextContent(
        "artifactId",
      );
    });
  });
});
