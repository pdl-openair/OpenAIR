import { describe, expect, it } from "vitest";
import type {
  WorkbenchArtifact,
  WorkbenchTaskModel,
} from "../../../../model/workbenchTask";
import {
  isEvidenceAssessmentArtifact,
  resolveEvidenceGraphResearchRecordsArtifacts,
} from "../../evidenceGraph";

function artifact(
  id: string,
  artifactType: string,
  overrides: Partial<WorkbenchArtifact> = {},
): WorkbenchArtifact {
  return {
    id,
    type: artifactType,
    artifactType,
    title: id,
    format: "json",
    relatedStage: "execution_validation",
    ...overrides,
  };
}

function model(
  artifacts: WorkbenchArtifact[],
  inputArtifactRefs: string[],
): WorkbenchTaskModel {
  return {
    artifacts,
    capabilityCalls: [
      {
        id: "call-assessment",
        name: "ot_v3_evidence_assessment_synthesis",
        status: "completed",
        inputArtifactRefs,
        outputArtifactRefs: ["assessment"],
      },
    ],
  } as unknown as WorkbenchTaskModel;
}

describe("Evidence Graph formal input resolution", () => {
  it("resolves the ResearchRecord artifact from the assessment producer input refs", () => {
    const assessment = artifact(
      "assessment",
      "evidence_assessment_and_research_synthesis",
      {
        sourceCapabilityCallId: "call-assessment",
      },
    );
    const records = artifact("records", "research_records");
    const task = model([assessment, records], [records.id]);

    expect(isEvidenceAssessmentArtifact(assessment, task)).toBe(true);
    expect(
      resolveEvidenceGraphResearchRecordsArtifacts(assessment, task).map(
        (source) => source.id,
      ),
    ).toEqual([records.id]);
  });

  it("also resolves the formal upstream ResearchRecord output through input call refs", () => {
    const assessment = artifact(
      "assessment",
      "evidence_assessment_and_research_synthesis",
      {
        sourceCapabilityCallId: "call-assessment",
      },
    );
    const records = artifact("records", "research_records");
    const task = {
      artifacts: [assessment, records],
      capabilityCalls: [
        {
          id: "call-records",
          name: "evidence_node_extraction",
          status: "completed",
          outputArtifactRefs: [records.id],
        },
        {
          id: "call-assessment",
          name: "ot_v3_evidence_assessment_synthesis",
          status: "completed",
          inputCallRefs: ["call-records"],
          outputArtifactRefs: [assessment.id],
        },
      ],
    } as unknown as WorkbenchTaskModel;

    expect(
      resolveEvidenceGraphResearchRecordsArtifacts(assessment, task).map(
        (source) => source.id,
      ),
    ).toEqual([records.id]);
  });

  it("accepts all formal inputs while excluding unrelated ResearchRecord artifacts", () => {
    const assessment = artifact(
      "assessment",
      "evidence_assessment_and_research_synthesis",
      {
        sourceCapabilityCallId: "call-assessment",
      },
    );
    const recordsA = artifact("records-a", "research_records");
    const recordsB = artifact("records-b", "research_records");
    const unrelated = artifact("records-unrelated", "research_records");

    expect(
      resolveEvidenceGraphResearchRecordsArtifacts(
        assessment,
        model([assessment, recordsA, recordsB], []),
      ),
    ).toEqual([]);
    expect(
      resolveEvidenceGraphResearchRecordsArtifacts(
        assessment,
        model(
          [assessment, recordsA, recordsB, unrelated],
          [recordsA.id, recordsB.id],
        ),
      ),
    ).toEqual([recordsA, recordsB]);
  });
});
