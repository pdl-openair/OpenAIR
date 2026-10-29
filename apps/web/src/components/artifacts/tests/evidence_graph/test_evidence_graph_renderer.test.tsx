import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { EvidenceGraph } from "../../evidenceGraph/EvidenceGraph";

const ID_A = "rr:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const ID_B = "rr:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const ID_C = "rr:cccccccccccccccccccccccccccccccc";

class ResizeObserverMock {
  constructor(private readonly callback: ResizeObserverCallback) {}

  observe(target: Element) {
    queueMicrotask(() => {
      this.callback(
        [{ target } as ResizeObserverEntry],
        this as unknown as ResizeObserver,
      );
    });
  }

  unobserve() {}

  disconnect() {}
}

class DOMMatrixReadOnlyMock {
  readonly m22 = 1;
}

beforeAll(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverMock);
  vi.stubGlobal("DOMMatrixReadOnly", DOMMatrixReadOnlyMock);
  Object.defineProperties(HTMLElement.prototype, {
    offsetWidth: { configurable: true, get: () => 1200 },
    offsetHeight: { configurable: true, get: () => 600 },
  });
  Object.defineProperty(SVGElement.prototype, "getBBox", {
    configurable: true,
    value: () => ({ x: 0, y: 0, width: 80, height: 16 }),
  });
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    () => ({
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      right: 1200,
      bottom: 600,
      width: 1200,
      height: 600,
      toJSON: () => ({}),
    }),
  );
});

function record(recordId: string, title: string, doi = "") {
  return {
    record_id: recordId,
    record_type: "quantitative_result",
    research_object: `object ${title}`,
    result: { statement: `result ${title}` },
    uncertainty: "uncertainty",
    source: {
      title,
      doi,
      arxiv_id: "",
      provider: "provider",
      source_locator: `https://example.test/${title}`,
      section_name: "Results",
    },
  };
}

function judgment(statement: string, recordIds: string[]) {
  return {
    statement,
    disposition: "supported",
    record_ids: recordIds,
    scope_boundary: "Only the measured regime.",
    rationale: "Direct measurement.",
    confidence: 0.9,
  };
}

describe("证据关系图 renderer", () => {
  it("renders a Judgment-centered network with Conflict and Gap as first-class graph nodes", async () => {
    render(
      <EvidenceGraph
        assessment={{
          evidence_judgments: [judgment("Current judgment", [ID_A])],
          conflicts: [
            {
              statement: "Sources disagree outside the measured regime.",
              record_ids: [ID_A, ID_B],
              implication: "Do not generalize beyond the measured regime.",
            },
          ],
          evidence_gaps: [
            {
              gap: "Long-duration behavior is not measured.",
              related_record_ids: [ID_C],
              impact: "Long-duration stability remains unknown.",
            },
          ],
        }}
        researchRecords={[
          record(ID_A, "Source A"),
          record(ID_B, "Source B"),
          record(ID_C, "Source C"),
        ]}
      />,
    );

    expect(screen.getByTestId("ot-evidence-graph")).toBeInTheDocument();
    expect(screen.getByTestId(`evidence-node-${ID_A}`)).toBeInTheDocument();
    expect(screen.getByTestId(`evidence-node-${ID_B}`)).toBeInTheDocument();
    expect(screen.getByTestId(`evidence-node-${ID_C}`)).toBeInTheDocument();
    expect(screen.getByTestId("judgment-node-01")).toHaveTextContent(
      "Current judgment",
    );
    expect(screen.getByTestId("conflict-node-01")).toHaveTextContent(
      "Sources disagree",
    );
    expect(screen.getByTestId("gap-node-01")).toHaveTextContent(
      "Long-duration behavior",
    );
    expect((await screen.findAllByText("冲突涉及")).length).toBeGreaterThan(0);
    expect((await screen.findAllByText("相关")).length).toBeGreaterThan(0);
  });

  it("keeps record nodes compact by default and reveals provenance details on hover", async () => {
    const originalDoi = "10.1000/hover-detail";
    render(
      <EvidenceGraph
        assessment={{
          evidence_judgments: [judgment("Current judgment", [ID_A])],
          conflicts: [],
          evidence_gaps: [],
        }}
        researchRecords={[record(ID_A, "Source A", originalDoi)]}
      />,
    );

    const evidenceButton = await screen.findByRole("button", {
      name: `查看证据来源 ${ID_A}`,
    });
    expect(evidenceButton).not.toHaveTextContent(ID_A);
    expect(evidenceButton).not.toHaveTextContent(originalDoi);

    fireEvent.mouseEnter(evidenceButton);
    expect(await screen.findByText(ID_A)).toBeInTheDocument();
    expect(await screen.findByText(`DOI：${originalDoi}`)).toBeInTheDocument();
  });

  it("opens an in-graph provenance inspector and preserves the original DOI text", async () => {
    const originalDoi = "https://doi.org/10.1000/Original.Mixed-Case";
    render(
      <EvidenceGraph
        assessment={{
          evidence_judgments: [judgment("Current judgment", [ID_A])],
          conflicts: [],
          evidence_gaps: [],
        }}
        researchRecords={[record(ID_A, "Source A", originalDoi)]}
      />,
    );

    const evidenceButton = await screen.findByRole("button", {
      name: `查看证据来源 ${ID_A}`,
    });
    expect(evidenceButton.parentElement).toHaveStyle({ pointerEvents: "all" });
    fireEvent.click(evidenceButton);
    expect(screen.getByTestId("evidence-record-inspector")).toBeInTheDocument();
    expect(
      screen.getByTestId("evidence-inspector-record-id"),
    ).toHaveTextContent(ID_A);
    const doi = screen.getByTestId("evidence-inspector-doi");
    expect(doi).toHaveTextContent(originalDoi);
    expect(doi).toHaveAttribute(
      "href",
      "https://doi.org/10.1000/Original.Mixed-Case",
    );
  });

  it("keeps formal ResearchRecord navigation optional and separate from DOI inspection", async () => {
    const onOpenRecord = vi.fn();
    render(
      <EvidenceGraph
        assessment={{
          evidence_judgments: [judgment("Current judgment", [ID_A])],
          conflicts: [],
          evidence_gaps: [],
        }}
        researchRecords={[record(ID_A, "Source A", "10.1000/source-a")]}
        onOpenRecord={onOpenRecord}
      />,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: `查看证据来源 ${ID_A}` }),
    );
    expect(onOpenRecord).not.toHaveBeenCalled();
    fireEvent.click(
      await screen.findByRole("button", { name: "查看完整研究记录" }),
    );
    expect(onOpenRecord).toHaveBeenCalledTimes(1);
    expect(onOpenRecord).toHaveBeenCalledWith(ID_A);
  });

  it("focuses a Judgment without removing the shared Evidence network", async () => {
    render(
      <EvidenceGraph
        assessment={{
          evidence_judgments: [
            judgment("J one", [ID_A, ID_B]),
            judgment("J two", [ID_B]),
          ],
          conflicts: [],
          evidence_gaps: [],
        }}
        researchRecords={[record(ID_A, "Source A"), record(ID_B, "Source B")]}
      />,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: /聚焦判断 J one/ }),
    );
    expect(screen.getByTestId(`evidence-node-${ID_A}`)).not.toHaveClass(
      "opacity-25",
    );
    expect(screen.getByTestId(`evidence-node-${ID_B}`)).not.toHaveClass(
      "opacity-25",
    );
    expect(screen.getByRole("button", { name: "取消聚焦" })).toBeEnabled();
  });

  it("keeps available records visible when all judgment links are unavailable", async () => {
    render(
      <EvidenceGraph
        assessment={{
          evidence_judgments: [
            {
              ...judgment("Must not be shown partially", [ID_A]),
              record_ids: [ID_A, "rr:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"],
            },
          ],
          conflicts: [],
          evidence_gaps: [],
        }}
        researchRecords={[record(ID_A, "Source A")]}
      />,
    );

    expect(await screen.findByTestId(`evidence-node-${ID_A}`)).toBeInTheDocument();
    expect(screen.queryByTestId("judgment-node-01")).not.toBeInTheDocument();
    expect(screen.queryByTestId("evidence-graph-empty")).not.toBeInTheDocument();
  });
});
