import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { useWorkbenchPanelWidths } from "../../../hooks/useWorkbenchPanelWidths";

function Widths({ taskId }: { taskId: string }) {
  const { leftPanelWidth, rightPanelWidth, dragStateRef } =
    useWorkbenchPanelWidths(taskId);
  return (
    <>
      <output data-testid="widths">
        {leftPanelWidth}:{rightPanelWidth}
      </output>
      <button
        onMouseDown={(event) => {
          dragStateRef.current = {
            side: "left",
            startX: event.clientX,
            startWidth: leftPanelWidth,
          };
        }}
      >
        resize
      </button>
    </>
  );
}
beforeEach(() => {
  localStorage.clear();
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    value: 1600,
  });
});

describe("Workbench preference and selection authority", () => {
  it("switching tasks initializes each saved layout before writing it", () => {
    const saved = (leftWidth: number, rightWidth: number) =>
      JSON.stringify({ leftWidth, rightWidth });
    localStorage.setItem(
      "openair.workbench.panel-widths.v2:A",
      saved(300, 400),
    );
    localStorage.setItem(
      "openair.workbench.panel-widths.v2:B",
      saved(350, 450),
    );
    const { rerender } = render(<Widths key="A" taskId="A" />);
    expect(screen.getByTestId("widths")).toHaveTextContent("300:400");
    rerender(<Widths key="B" taskId="B" />);
    expect(screen.getByTestId("widths")).toHaveTextContent("350:450");
    expect(localStorage.getItem("openair.workbench.panel-widths.v2:B")).toBe(
      saved(350, 450),
    );
    rerender(<Widths key="A" taskId="A" />);
    expect(screen.getByTestId("widths")).toHaveTextContent("300:400");
  });
  it("a task without preferences starts with defaults and still supports drag and resize", () => {
    const { rerender } = render(<Widths key="A" taskId="A" />);
    fireEvent.mouseDown(screen.getByText("resize"), { clientX: 100 });
    fireEvent.mouseMove(window, { clientX: 180 });
    fireEvent.mouseUp(window);
    expect(screen.getByTestId("widths")).toHaveTextContent("300:280");
    rerender(<Widths key="B" taskId="B" />);
    expect(screen.getByTestId("widths")).toHaveTextContent("220:280");
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1000,
    });
    fireEvent.resize(window);
    expect(screen.getByTestId("widths")).toHaveTextContent("190:260");
  });
});
