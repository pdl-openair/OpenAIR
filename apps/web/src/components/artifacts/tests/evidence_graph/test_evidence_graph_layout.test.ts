import { describe, expect, it } from "vitest";
import {
  AUXILIARY_NODE_WIDTH,
  EVIDENCE_NODE_WIDTH,
  JUDGMENT_NODE_WIDTH,
  buildEvidenceGraphLayout,
} from "../../evidenceGraph/layoutEvidenceGraph";
import { projectEvidenceGraph } from "../../evidenceGraph/projectEvidenceGraph";

const ID_A = "rr:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const ID_B = "rr:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const ID_C = "rr:cccccccccccccccccccccccccccccccc";

function record(recordId: string) {
  return {
    record_id: recordId,
    research_object: recordId,
    result: { statement: `result ${recordId}` },
    source: {
      title: `source ${recordId}`,
      doi: "",
      arxiv_id: "",
      provider: "provider",
      source_locator: `https://example.test/${recordId}`,
      section_name: "Results",
    },
  };
}

function judgment(statement: string, ids: string[]) {
  return {
    statement,
    disposition: "supported",
    record_ids: ids,
    scope_boundary: "scope",
    rationale: "rationale",
    confidence: 0.8,
  };
}

describe("buildEvidenceGraphLayout", () => {
  it("keeps Judgments on a central spine and distributes Evidence to both sides", () => {
    const projection = projectEvidenceGraph(
      {
        evidence_judgments: [
          judgment("J1", [ID_A, ID_B]),
          judgment("J2", [ID_B, ID_C]),
        ],
        conflicts: [],
        evidence_gaps: [],
      },
      [record(ID_A), record(ID_B), record(ID_C)],
    );

    const layout = buildEvidenceGraphLayout(projection);
    expect(new Set(layout.judgments.map((row) => row.x)).size).toBe(1);
    expect(new Set(layout.evidence.map((row) => row.side))).toEqual(
      new Set(["left", "right"]),
    );
    expect(layout.evidence.find((row) => row.recordId === ID_B)).toBeDefined();
  });

  it("keeps the primary graph compact enough for the real Workbench middle pane", () => {
    const projection = projectEvidenceGraph(
      {
        evidence_judgments: [
          judgment("J1", [ID_A, ID_B]),
          judgment("J2", [ID_B, ID_C]),
          judgment("J3", [ID_A]),
          judgment("J4", [ID_C]),
        ],
        conflicts: [
          {
            statement: "conflict",
            record_ids: [ID_A, ID_B],
            implication: "impact",
          },
        ],
        evidence_gaps: [
          { gap: "gap", related_record_ids: [ID_C], impact: "impact" },
        ],
      },
      [record(ID_A), record(ID_B), record(ID_C)],
    );

    const layout = buildEvidenceGraphLayout(projection);
    const primaryRight = Math.max(
      ...layout.evidence.map((row) => row.x + EVIDENCE_NODE_WIDTH),
      ...layout.judgments.map((row) => row.x + JUDGMENT_NODE_WIDTH),
    );
    const auxiliaryRight = Math.max(
      ...layout.auxiliary.map((row) => row.x + AUXILIARY_NODE_WIDTH),
    );

    expect(layout.width).toBeLessThanOrEqual(820);
    expect(primaryRight).toBeLessThanOrEqual(layout.width);
    expect(auxiliaryRight).toBeLessThanOrEqual(layout.width);
    expect(Math.max(...layout.judgments.map((row) => row.y))).toBeLessThan(640);
  });

  it("places Conflict and Gap as first-class auxiliary nodes below the scientific relation network", () => {
    const projection = projectEvidenceGraph(
      {
        evidence_judgments: [judgment("J1", [ID_A])],
        conflicts: [
          {
            statement: "conflict",
            record_ids: [ID_A, ID_B],
            implication: "impact",
          },
        ],
        evidence_gaps: [
          { gap: "gap", related_record_ids: [ID_C], impact: "impact" },
        ],
      },
      [record(ID_A), record(ID_B), record(ID_C)],
    );

    const layout = buildEvidenceGraphLayout(projection);
    const mainBottom = Math.max(
      ...layout.judgments.map((row) => row.y),
      ...layout.evidence.map((row) => row.y),
    );
    expect(layout.auxiliary).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "conflict" }),
        expect.objectContaining({ kind: "gap" }),
      ]),
    );
    expect(Math.min(...layout.auxiliary.map((row) => row.y))).toBeGreaterThan(
      mainBottom,
    );
  });
});
