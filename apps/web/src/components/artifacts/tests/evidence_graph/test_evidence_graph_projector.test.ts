import { describe, expect, it } from "vitest";
import { projectEvidenceGraph } from "../../evidenceGraph/projectEvidenceGraph";

const ID_A = "rr:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const ID_B = "rr:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const ID_C = "rr:cccccccccccccccccccccccccccccccc";
const ID_D = "rr:dddddddddddddddddddddddddddddddd";

function record(recordId: string, sourceTitle: string, doi = "") {
  return {
    record_id: recordId,
    record_type: "quantitative_result",
    research_object: `object ${sourceTitle}`,
    result: { statement: `result ${sourceTitle}` },
    uncertainty: "reported uncertainty",
    source: {
      title: sourceTitle,
      doi,
      arxiv_id: "",
      provider: "test-provider",
      source_locator: `https://example.test/${sourceTitle}`,
      section_name: "Results",
    },
  };
}

function judgment(
  statement: string,
  disposition: "supported" | "refuted" | "limited" | "uncertain",
  recordIds: string[],
) {
  return {
    statement,
    disposition,
    record_ids: recordIds,
    scope_boundary: `scope ${statement}`,
    rationale: `rationale ${statement}`,
    confidence: 0.8,
  };
}

const records = [
  record(ID_A, "source A", "10.1000/source-a"),
  record(ID_B, "source B"),
  record(ID_C, "source C"),
  record(ID_D, "source D"),
];

describe("projectEvidenceGraph", () => {
  it("keeps uncited records visible without inventing relations", () => {
    const projection = projectEvidenceGraph(
      { evidence_judgments: [], conflicts: [], evidence_gaps: [] },
      records,
    );
    expect(projection.evidenceNodes).toHaveLength(4);
    expect(projection.relations).toEqual([]);
  });

  it("keeps supported/refuted/limited/uncertain relations distinct", () => {
    const projection = projectEvidenceGraph(
      {
        evidence_judgments: [
          judgment("J supported", "supported", [ID_A]),
          judgment("J refuted", "refuted", [ID_B]),
          judgment("J limited", "limited", [ID_C]),
          judgment("J uncertain", "uncertain", [ID_D]),
        ],
        conflicts: [],
        evidence_gaps: [],
      },
      records,
    );

    expect(projection.relations.map((edge) => edge.disposition).sort()).toEqual(
      ["limited", "refuted", "supported", "uncertain"],
    );
  });

  it("projects one Judgment to multiple record_ids", () => {
    const projection = projectEvidenceGraph(
      {
        evidence_judgments: [judgment("J multi", "supported", [ID_A, ID_B])],
        conflicts: [],
        evidence_gaps: [],
      },
      records,
    );

    expect(projection.judgmentNodes).toHaveLength(1);
    expect(projection.evidenceNodes.map((node) => node.recordId)).toEqual([
      ID_A,
      ID_B,
      ID_C,
      ID_D,
    ]);
    expect(projection.relations).toHaveLength(2);
    expect(
      new Set(projection.relations.map((edge) => edge.targetJudgmentKey)).size,
    ).toBe(1);
  });

  it("reuses one Evidence node across multiple Judgments", () => {
    const projection = projectEvidenceGraph(
      {
        evidence_judgments: [
          judgment("J one", "supported", [ID_A]),
          judgment("J two", "limited", [ID_A]),
        ],
        conflicts: [],
        evidence_gaps: [],
      },
      records,
    );

    expect(projection.evidenceNodes).toHaveLength(4);
    expect(projection.evidenceNodes[0].recordId).toBe(ID_A);
    expect(projection.relations).toHaveLength(2);
  });

  it("fails closed on dangling/unknown/ambiguous record_id instead of drawing partial relations", () => {
    const unknownId = "rr:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee";
    const projection = projectEvidenceGraph(
      {
        evidence_judgments: [
          judgment("safe", "supported", [ID_B]),
          judgment("dangling", "supported", [ID_B, unknownId]),
          judgment("ambiguous", "refuted", [ID_A]),
        ],
        conflicts: [],
        evidence_gaps: [],
      },
      [
        record(ID_A, "duplicate A1"),
        record(ID_A, "duplicate A2"),
        record(ID_B, "source B"),
        {
          research_object: "missing identity",
          result: { statement: "x" },
          source: {},
        },
      ],
    );

    expect(projection.judgmentNodes.map((node) => node.statement)).toEqual([
      "safe",
    ]);
    expect(projection.relations).toHaveLength(1);
    expect(projection.relations[0].sourceRecordId).toBe(ID_B);
    expect(projection.evidenceNodes.map((node) => node.recordId)).toEqual([
      ID_B,
    ]);
    expect(projection.omitted.judgments).toBe(2);
    expect(projection.omitted.researchRecords).toBe(3);
  });

  it("treats duplicate record_id as ambiguous even when one duplicate row is malformed", () => {
    const projection = projectEvidenceGraph(
      {
        evidence_judgments: [judgment("J", "supported", [ID_A])],
        conflicts: [],
        evidence_gaps: [],
      },
      [
        record(ID_A, "valid A"),
        {
          record_id: ID_A,
          research_object: "malformed duplicate",
          result: { statement: "x" },
          source: {},
        },
      ],
    );

    expect(projection.relations).toHaveLength(0);
    expect(projection.evidenceNodes).toHaveLength(0);
    expect(projection.omitted.judgments).toBe(1);
  });

  it("projects conflict as its own fact and includes every involved ResearchRecord without creating an ordinary relation", () => {
    const projection = projectEvidenceGraph(
      {
        evidence_judgments: [judgment("J", "supported", [ID_A])],
        conflicts: [
          {
            statement: "A and B disagree",
            record_ids: [ID_A, ID_B],
            implication: "The boundary remains contested.",
          },
        ],
        evidence_gaps: [],
      },
      records,
    );

    expect(projection.relations).toHaveLength(1);
    expect(projection.relations[0].sourceRecordId).toBe(ID_A);
    expect(projection.conflicts).toEqual([
      expect.objectContaining({ recordIds: [ID_A, ID_B] }),
    ]);
    expect(projection.evidenceNodes.map((node) => node.recordId)).toEqual([
      ID_A,
      ID_B,
      ID_C,
      ID_D,
    ]);
    expect(
      projection.evidenceNodes.find((node) => node.recordId === ID_B)
        ?.conflicted,
    ).toBe(true);
  });

  it("projects a gap and its related ResearchRecord without manufacturing an Evidence -> Judgment relation", () => {
    const projection = projectEvidenceGraph(
      {
        evidence_judgments: [judgment("J", "supported", [ID_A])],
        conflicts: [],
        evidence_gaps: [
          {
            gap: "No direct stability measurement",
            related_record_ids: [ID_B],
            impact: "Long-duration behavior remains unknown.",
          },
        ],
      },
      records,
    );

    expect(projection.gaps).toEqual([
      expect.objectContaining({ relatedRecordIds: [ID_B] }),
    ]);
    expect(projection.evidenceNodes.map((node) => node.recordId)).toEqual([
      ID_A,
      ID_B,
      ID_C,
      ID_D,
    ]);
    expect(
      projection.evidenceNodes.find((node) => node.recordId === ID_B)
        ?.gapRelated,
    ).toBe(true);
    expect(projection.relations).toHaveLength(1);
    expect(
      projection.relations.some((edge) => edge.sourceRecordId === ID_B),
    ).toBe(false);
  });

  it("preserves the ResearchRecord DOI exactly for provenance inspection", () => {
    const originalDoi = "https://doi.org/10.1000/Original.Mixed-Case";
    const projection = projectEvidenceGraph(
      {
        evidence_judgments: [judgment("J", "supported", [ID_A])],
        conflicts: [],
        evidence_gaps: [],
      },
      [record(ID_A, "source A", originalDoi)],
    );

    expect(projection.evidenceNodes[0].doi).toBe(originalDoi);
  });

  it("accepts the formal assessment and ResearchRecord artifact wrappers without changing semantics", () => {
    const innerAssessment = {
      evidence_judgments: [judgment("J", "supported", [ID_A])],
      conflicts: [],
      evidence_gaps: [],
    };

    expect(
      projectEvidenceGraph(
        { evidence_assessment_and_research_synthesis: innerAssessment },
        { research_records: [records[0]] },
      ),
    ).toEqual(projectEvidenceGraph(innerAssessment, [records[0]]));
  });

  it("is deterministic when assessment and ResearchRecord input order changes", () => {
    const assessment = {
      evidence_judgments: [
        judgment("J two", "limited", [ID_C, ID_A]),
        judgment("J one", "supported", [ID_B]),
      ],
      conflicts: [
        {
          statement: "conflict",
          record_ids: [ID_C, ID_B],
          implication: "boundary",
        },
      ],
      evidence_gaps: [
        {
          gap: "gap",
          related_record_ids: [ID_D, ID_A],
          impact: "impact",
        },
      ],
    };

    const reversed = {
      evidence_judgments: [...assessment.evidence_judgments]
        .reverse()
        .map((row) => ({
          ...row,
          record_ids: [...row.record_ids].reverse(),
        })),
      conflicts: [...assessment.conflicts].reverse().map((row) => ({
        ...row,
        record_ids: [...row.record_ids].reverse(),
      })),
      evidence_gaps: [...assessment.evidence_gaps].reverse().map((row) => ({
        ...row,
        related_record_ids: [...row.related_record_ids].reverse(),
      })),
    };

    expect(projectEvidenceGraph(reversed, [...records].reverse())).toEqual(
      projectEvidenceGraph(assessment, records),
    );
  });
});
