import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Task } from "@openair/shared";
import { TASK_ID, V1_TASK, renderWorkbench } from "./workbenchTestHarness";

describe("Final Result delivery in the Workbench", () => {
  it("shows the formal Final Result as the primary completed-research delivery", async () => {
    const completedTask: Task = {
      ...V1_TASK,
      status: "completed",
      creationStatus: "confirmed",
      finalResultAuthority: {
        mode: "artifact_set",
        researchDesignVersion: 1,
        resultArtifactRefs: [],
        sourceArtifacts: [],
        assembledAt: "2026-09-15T02:00:00Z",
        status: "current",
        currentResultArtifacts: [],
        finalResultId: "final-workbench-1",
        integrityContractVersion: "scientific_completion_integrity_v3",
        integrityStatus: "validated",
      },
      finalResult: {
        finalResultId: "final-workbench-1",
        completionDecisionId: "decision-workbench-1",
        scientificStateVersion: 4,
        disposition: "partial",
        directAnswer:
          "The legacy accepted result remains available when no current node product exists.",
        keyFindings: ["Legacy fallback finding."],
        evidenceRefs: [],
        scientificResult: {
          conclusion:
            "The accepted scientific state supports route A for the confirmed scope.",
        },
        scientificSourceRefs: [],
        uncertainty: ["One operating condition remains uncertain."],
        gaps: ["Additional validation is still needed."],
        limitations: ["The result applies only to the confirmed scope."],
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

    renderWorkbench(completedTask);

    const center = await screen.findByTestId("workbench-center");
    const right = screen.getByTestId("workbench-right-panel");
    const panel = within(center).getByTestId("final-result-panel");
    expect(panel).toHaveTextContent(
      "The legacy accepted result remains available when no current node product exists.",
    );
    expect(panel).toHaveTextContent("Legacy fallback finding.");
    expect(panel).not.toHaveTextContent(
      "The accepted scientific state supports route A for the confirmed scope.",
    );
    expect(panel).not.toHaveTextContent(
      "One operating condition remains uncertain.",
    );
    expect(
      within(right).queryByTestId("final-result-panel"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("workbench-artifact-empty")).toBeInTheDocument();
  });

  it("uses only the unique visible, usable, accepted, current node products and suppresses competing FinalResult narrative", async () => {
    const usableGate = {
      canConfirmPass: false,
      canUseDownstream: true,
      requiresHumanReview: false,
      requiresScopeExpansionConfirmation: false,
      allowedActions: ["use_downstream"],
      primaryAction: "use_downstream",
      blockedReason: null,
    };
    const acceptedArtifact = (
      id: string,
      artifactType: string,
      substage: string,
      subStageInstanceId: string,
      content: Record<string, unknown>,
      overrides: Record<string, unknown> = {},
    ) => ({
      id,
      taskId: TASK_ID,
      ownerId: "owner-rdx2",
      workspaceId: "workspace-rdx2",
      createdBy: "user-rdx2",
      kind: artifactType,
      artifactType,
      title: `${artifactType} result`,
      createdAt: "2026-09-15T01:00:00Z",
      format: "json",
      relatedStage: "insight_generation" as const,
      substage,
      subStageInstanceId,
      storageType: "inline" as const,
      inlineContent: JSON.stringify(content),
      artifactState: "usable" as const,
      humanGate: usableGate,
      ...overrides,
    });

    const currentConclusion = acceptedArtifact(
      "artifact-current-conclusion",
      "current_research_conclusion",
      "current_research_conclusion",
      "instance-current-conclusion",
      {
        scope: "Confirmed scope.",
        reasoning_summary: "CURRENT_ACCEPTED_NODE_CONCLUSION",
        assumptions: [],
        uncertainty: [],
        next_action: "Keep separate from the next recommendation.",
      },
    );
    const staleConclusion = acceptedArtifact(
      "artifact-stale-conclusion",
      "current_research_conclusion",
      "current_research_conclusion",
      "instance-superseded-conclusion",
      {
        scope: "Superseded scope.",
        reasoning_summary: "STALE_NODE_MUST_NOT_RENDER",
        assumptions: [],
        uncertainty: [],
        next_action: "Ignore.",
      },
    );
    const unacceptedUnknown = acceptedArtifact(
      "artifact-unaccepted-unknown",
      "key_unknowns_and_candidate_explanations",
      "key_unknowns_candidate_explanations",
      "instance-key-unknowns",
      {
        scope: "Unknowns.",
        reasoning_summary: "UNACCEPTED_NODE_MUST_NOT_RENDER",
        assumptions: [],
        uncertainty: [],
        next_action: "Ignore.",
      },
      {
        artifactState: "needs_review",
        humanGate: {
          ...usableGate,
          canUseDownstream: false,
          allowedActions: ["confirm_pass"],
        },
      },
    );
    const hiddenRecommendation = acceptedArtifact(
      "artifact-hidden-next",
      "next_research_recommendation",
      "next_research_recommendation",
      "instance-next-recommendation",
      {
        scope: "Next action.",
        reasoning_summary: "HIDDEN_NODE_MUST_NOT_RENDER",
        assumptions: [],
        uncertainty: [],
        next_action: "Ignore.",
      },
      { artifactVisibility: "internal" },
    );
    const ambiguousNextA = acceptedArtifact(
      "artifact-next-a",
      "next_research_recommendation",
      "next_research_recommendation",
      "instance-next-recommendation",
      {
        scope: "Next action.",
        reasoning_summary: "AMBIGUOUS_NEXT_A_MUST_NOT_RENDER",
        assumptions: [],
        uncertainty: [],
        next_action: "Run A.",
      },
    );
    const ambiguousNextB = acceptedArtifact(
      "artifact-next-b",
      "next_research_recommendation",
      "next_research_recommendation",
      "instance-next-recommendation",
      {
        scope: "Next action.",
        reasoning_summary: "AMBIGUOUS_NEXT_B_MUST_NOT_RENDER",
        assumptions: [],
        uncertainty: [],
        next_action: "Run B.",
      },
    );
    const completedTask: Task = {
      ...V1_TASK,
      status: "completed",
      creationStatus: "confirmed",
      currentStage: "insight_generation",
      stageDetails: [
        {
          stageKey: "insight_generation",
          label: "洞察生成",
          status: "completed",
          summary: null,
          risks: null,
          nextSteps: null,
          artifactRefs: [
            currentConclusion.id,
            unacceptedUnknown.id,
            hiddenRecommendation.id,
            ambiguousNextA.id,
            ambiguousNextB.id,
          ],
          subStages: [
            {
              subStage: "current_research_conclusion",
              subStageInstanceId: "instance-current-conclusion",
              status: "completed",
              artifactRefs: [currentConclusion.id],
            },
            {
              subStage: "key_unknowns_candidate_explanations",
              subStageInstanceId: "instance-key-unknowns",
              status: "completed",
              artifactRefs: [unacceptedUnknown.id],
            },
            {
              subStage: "next_research_recommendation",
              subStageInstanceId: "instance-next-recommendation",
              status: "completed",
              artifactRefs: [
                hiddenRecommendation.id,
                ambiguousNextA.id,
                ambiguousNextB.id,
              ],
            },
          ],
        },
      ],
      artifacts: [
        currentConclusion,
        staleConclusion,
        unacceptedUnknown,
        hiddenRecommendation,
        ambiguousNextA,
        ambiguousNextB,
      ] as Task["artifacts"],
      finalResultAuthority: {
        mode: "artifact_set",
        researchDesignVersion: 1,
        resultArtifactRefs: [],
        sourceArtifacts: [],
        assembledAt: "2026-09-15T02:00:00Z",
        status: "current",
        currentResultArtifacts: [],
        finalResultId: "final-workbench-node-product",
        integrityContractVersion: "scientific_completion_integrity_v3",
        integrityStatus: "validated",
      },
      finalResult: {
        finalResultId: "final-workbench-node-product",
        completionDecisionId: "decision-workbench-node-product",
        scientificStateVersion: 5,
        disposition: "full",
        directAnswer: "LEGACY_FINAL_RESULT_MUST_NOT_RENDER",
        keyFindings: ["LEGACY_FINDING_MUST_NOT_RENDER"],
        evidenceRefs: [],
        scientificResult: {},
        scientificSourceRefs: [],
        uncertainty: [],
        gaps: [],
        limitations: [],
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

    renderWorkbench(completedTask);

    const primary = await screen.findByTestId(
      "workbench-primary-node-products",
    );
    expect(primary).toHaveTextContent("CURRENT_ACCEPTED_NODE_CONCLUSION");
    expect(screen.queryByTestId("final-result-panel")).not.toBeInTheDocument();
    for (const forbidden of [
      "LEGACY_FINAL_RESULT_MUST_NOT_RENDER",
      "LEGACY_FINDING_MUST_NOT_RENDER",
      "STALE_NODE_MUST_NOT_RENDER",
      "UNACCEPTED_NODE_MUST_NOT_RENDER",
      "HIDDEN_NODE_MUST_NOT_RENDER",
      "AMBIGUOUS_NEXT_A_MUST_NOT_RENDER",
      "AMBIGUOUS_NEXT_B_MUST_NOT_RENDER",
    ]) {
      expect(primary.textContent).not.toContain(forbidden);
    }
  });
});
