import { App } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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

function gate(overrides: Record<string, unknown> = {}) {
  return {
    canConfirmPass: false,
    canUseDownstream: false,
    requiresHumanReview: true,
    requiresScopeExpansionConfirmation: false,
    allowedActions: [],
    primaryAction: "",
    blockedReason: null,
    ...overrides,
  };
}

function artifact(overrides: Record<string, unknown> = {}) {
  return {
    id: "artifact-1",
    taskId: "task-1",
    sourceTaskId: "task-1",
    sourceTaskTitle: "Research task",
    kind: "analysis_report",
    origin: "generated",
    title: "Research artifact",
    summary: "summary",
    format: "markdown",
    createdAt: "2026-10-05T00:00:00Z",
    artifactState: "needs_review",
    humanGate: gate({
      canConfirmPass: true,
      allowedActions: ["confirm_pass", "request_revision"],
      primaryAction: "confirm_pass",
    }),
    ...overrides,
  };
}

function renderLibrary() {
  return render(
    <App>
      <MemoryRouter>
        <ArtifactLibraryPage />
      </MemoryRouter>
    </App>,
  );
}

describe("Artifact humanGate action authority", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.fetchArtifactContentById.mockResolvedValue({ content: "# report", sourceMode: "live" });
    mocks.fetchArtifactDetail.mockResolvedValue(null);
    mocks.fetchArtifactRelations.mockResolvedValue([]);
    mocks.fetchTaskTitleOptions.mockResolvedValue({ tasks: [], total: 0 });
    mocks.reuseArtifact.mockResolvedValue({});
    mocks.uploadArtifact.mockResolvedValue({});
  });

  it("renders the server-authorized review controls and sends request_revision to its consumer", async () => {
    const row = artifact({
      humanGate: gate({
        canConfirmPass: true,
        allowedActions: ["confirm_pass", "request_revision", "view_trace"],
        primaryAction: "confirm_pass",
      }),
    });
    mocks.fetchArtifacts.mockResolvedValue({ artifacts: [row], total: 1 });
    mocks.reviewArtifact.mockResolvedValue(artifact({
      artifactState: "rejected_or_needs_revision",
      humanGate: gate(),
    }));

    renderLibrary();
    fireEvent.click(await screen.findByRole("button", { name: "Research artifact" }));

    expect(await screen.findByRole("button", { name: "核查通过" })).toBeVisible();
    const revision = screen.getByRole("button", { name: "退回修改" });
    expect(revision).toBeVisible();
    expect(screen.queryByRole("button", { name: /trace|诊断|来源/ })).not.toBeInTheDocument();

    fireEvent.click(revision);
    await waitFor(() => expect(mocks.reviewArtifact).toHaveBeenCalledWith(
      "artifact-1",
      "needs_revision",
      "User requested revision before formal use.",
    ));
  });

  it("renders scope confirmation only for partial_success and invokes the real client consumer", async () => {
    const row = artifact({
      artifactState: "partial_success",
      humanGate: gate({
        requiresScopeExpansionConfirmation: true,
        allowedActions: ["confirm_scope_expansion"],
        primaryAction: "confirm_scope_expansion",
      }),
    });
    mocks.fetchArtifacts.mockResolvedValue({ artifacts: [row], total: 1 });
    mocks.confirmArtifactScopeExpansion.mockResolvedValue(artifact({
      artifactState: "needs_input",
      humanGate: gate({ blockedReason: "missing_inputs" }),
    }));

    renderLibrary();
    fireEvent.click(await screen.findByRole("button", { name: "Research artifact" }));
    const confirm = await screen.findByRole("button", { name: "确认扩大范围" });
    expect(confirm).toBeVisible();

    fireEvent.click(confirm);
    await waitFor(() => expect(mocks.confirmArtifactScopeExpansion).toHaveBeenCalledWith(
      "artifact-1",
      "User confirmed the expanded research scope.",
    ));
    await waitFor(() => expect(screen.queryByRole("button", { name: "确认扩大范围" })).not.toBeInTheDocument());
  });
});
