import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SubstageProgressPanel } from "./SubstageProgressPanel";

vi.mock("../../api/client", () => ({
  fetchWorkUnitProgress: vi.fn(async () => ({
    exists: true,
    content: "- **[07:17:54]** 竞争相 VASP 输入就绪",
    updatedAt: "2026-10-18T07:17:54Z",
  })),
}));

vi.mock("../artifacts/StreamdownMarkdown", () => ({
  StreamdownMarkdown: ({ content }: { content: string }) => (
    <div data-testid="substage-progress-markdown">{content}</div>
  ),
}));

function renderPanel(running = true) {
  return render(
    <SubstageProgressPanel
      taskId="task_test"
      workUnitIds={["workunit_a"]}
      running={running}
    />,
  );
}

describe("SubstageProgressPanel drag collapse/expand", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders polled progress content with the default height", async () => {
    renderPanel();

    await waitFor(() =>
      expect(screen.getByTestId("substage-progress-markdown")).toHaveTextContent(
        "竞争相 VASP 输入就绪",
      ),
    );
    expect(screen.getByTestId("substage-progress-resizer")).toBeInTheDocument();
    const panel = screen.getByTestId("substage-progress-panel");
    expect(panel.querySelector("div.overflow-auto")).toHaveStyle({ height: "224px" });
  });

  it("resizes the content height when dragging the handle up", async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByTestId("substage-progress-markdown")).toBeInTheDocument());

    fireEvent.mouseDown(screen.getByTestId("substage-progress-resizer"), { clientY: 400 });
    fireEvent.mouseMove(window, { clientY: 380 });
    fireEvent.mouseUp(window);

    const panel = screen.getByTestId("substage-progress-panel");
    expect(panel.querySelector("div.overflow-auto")).toHaveStyle({ height: "244px" });
    expect(screen.getByTestId("substage-progress-markdown")).toBeInTheDocument();
  });

  it("collapses when dragged near the minimum and released", async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByTestId("substage-progress-markdown")).toBeInTheDocument());

    fireEvent.mouseDown(screen.getByTestId("substage-progress-resizer"), { clientY: 400 });
    fireEvent.mouseMove(window, { clientY: 600 });
    fireEvent.mouseUp(window);

    expect(screen.queryByTestId("substage-progress-markdown")).not.toBeInTheDocument();
    expect(screen.getByText(/已收起/)).toBeInTheDocument();
    // The panel shell (header + drag handle) stays available for expanding.
    expect(screen.getByTestId("substage-progress-resizer")).toBeInTheDocument();
  });

  it("expands a collapsed panel when dragging the handle up (same direction model)", async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByTestId("substage-progress-markdown")).toBeInTheDocument());

    fireEvent.mouseDown(screen.getByTestId("substage-progress-resizer"), { clientY: 400 });
    fireEvent.mouseMove(window, { clientY: 600 });
    fireEvent.mouseUp(window);
    expect(screen.queryByTestId("substage-progress-markdown")).not.toBeInTheDocument();

    fireEvent.mouseDown(screen.getByTestId("substage-progress-resizer"), { clientY: 400 });
    fireEvent.mouseMove(window, { clientY: 280 });
    fireEvent.mouseUp(window);

    const panel = screen.getByTestId("substage-progress-panel");
    expect(screen.getByTestId("substage-progress-markdown")).toBeInTheDocument();
    expect(panel.querySelector("div.overflow-auto")).toHaveStyle({ height: "120px" });
  });

  it("keeps the drag continuous across the collapsed-to-expanded transition", async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByTestId("substage-progress-markdown")).toBeInTheDocument());

    fireEvent.mouseDown(screen.getByTestId("substage-progress-resizer"), { clientY: 400 });
    fireEvent.mouseMove(window, { clientY: 600 });
    fireEvent.mouseUp(window);

    // Expand with an upward drag, then keep dragging up without releasing:
    // the anchor rebases at the transition, so the height follows the cursor.
    fireEvent.mouseDown(screen.getByTestId("substage-progress-resizer"), { clientY: 400 });
    fireEvent.mouseMove(window, { clientY: 280 });
    fireEvent.mouseMove(window, { clientY: 240 });
    fireEvent.mouseUp(window);

    const panel = screen.getByTestId("substage-progress-panel");
    expect(panel.querySelector("div.overflow-auto")).toHaveStyle({ height: "160px" });
  });
});
