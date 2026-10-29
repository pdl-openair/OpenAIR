import { describe, expect, it } from "vitest";
import type { Task } from "@openair/shared";
import { workbenchModelFromApiTask } from "../../workbenchTask";

function taskWithFinalResult(skillPack = "cs_research"): Task {
  return {
    taskId: "task-final-result",
    ownerId: "owner-1",
    workspaceId: "workspace-1",
    createdBy: "user-1",
    title: "Formal result delivery",
    goalSummary: "Answer the confirmed research question.",
    currentStage: "insight_generation",
    status: "completed",
    resultSource: "live",
    skillPack,
    stages: ["problem_modeling", "solution_design", "execution_validation", "insight_generation"],
    artifacts: [{
      id: "artifact-source-1",
      taskId: "task-final-result",
      kind: "research_output",
      title: "Supporting process artifact",
      createdAt: "2026-09-15T01:00:00Z",
      storageType: "inline",
      inlineContent: JSON.stringify({
        direct_answer: "Artifact-only answer must not become the formal result.",
        recommendation: "Artifact-only recommendation.",
        metric: { value: 999 },
      }),
    }],
    confirmations: [],
    capabilityCalls: [],
    finalResultAuthority: {
      mode: "artifact_set",
      researchDesignVersion: 1,
      resultArtifactRefs: ["artifact-source-1"],
      sourceArtifacts: [{
        artifactId: "artifact-source-1",
        artifactType: "research_output",
        version: 1,
      }],
      assembledAt: "2026-09-15T02:00:00Z",
      status: "current",
      currentResultArtifacts: [{
        artifactId: "artifact-source-1",
        artifactType: "research_output",
        version: 1,
        artifactState: "usable",
      }],
      finalResultId: "final-1",
      integrityContractVersion: "scientific_completion_integrity_v3",
      integrityStatus: "validated",
    },
    finalResult: {
      finalResultId: "final-1",
      completionDecisionId: "decision-1",
      scientificStateVersion: 3,
      disposition: "partial",
      directAnswer: null,
      keyFindings: [],
      evidenceRefs: [],
      scientificResult: {
        accepted_conclusion: "The accepted conclusion must survive the page read model unchanged.",
        nested_fact: { value: 17, status: "supported" },
      },
      scientificSourceRefs: ["artifact-source-1"],
      uncertainty: ["One variable remains unresolved."],
      gaps: ["Additional validation is still needed."],
      limitations: ["Applies to the confirmed scope only."],
      acceptedScientificFacts: {},
      validatedAt: "2026-09-15T02:00:00Z",
      validationOutcome: {
        contractVersion: "scientific_completion_integrity_v3",
        validatorRef: "core:scientific_completion_integrity:v3",
        status: "passed",
        checkIds: ["scientific_state_snapshot"],
        validatedAt: "2026-09-15T02:00:00Z",
      },
    },
  };
}

describe("workbenchModelFromApiTask Final Result", () => {
  it.each([
    ["CS", "cs_research"],
    ["OT", "open_trajectory"],
    ["PVD", "pvd"],
  ])("preserves the canonical Final Result for %s without interpreting its Artifact", (_workflow, skillPack) => {
    const task = taskWithFinalResult(skillPack);
    const model = workbenchModelFromApiTask(task);

    expect(model.finalResult).toEqual(task.finalResult);
    expect(model.finalResult?.scientificResult).toEqual(task.finalResult?.scientificResult);
    expect(model.finalResult?.scientificSourceRefs).toEqual(["artifact-source-1"]);
    expect(model.artifacts[0]?.content).toContain("Artifact-only answer");
    expect(JSON.stringify(model.finalResult)).not.toContain("Artifact-only answer");
  });

  it.each(["missing", "stale", "needs_review"] as const)(
    "does not expose a Final Result when its Authority is %s",
    (authorityState) => {
      const task = taskWithFinalResult();
      task.status = "running";
      task.finalResultAuthority = authorityState === "missing"
        ? null
        : { ...task.finalResultAuthority!, status: authorityState };

      const model = workbenchModelFromApiTask(task);

      expect(model.finalResult).toBeNull();
      expect(model.artifacts).toHaveLength(1);
      expect(model.artifacts[0]?.content).toContain("Artifact-only answer");
    },
  );

  it("rejects a Final Result that does not match the current Authority identity", () => {
    const task = taskWithFinalResult();
    task.finalResultAuthority = { ...task.finalResultAuthority!, finalResultId: "final-newer" };

    expect(workbenchModelFromApiTask(task).finalResult).toBeNull();
  });
});
