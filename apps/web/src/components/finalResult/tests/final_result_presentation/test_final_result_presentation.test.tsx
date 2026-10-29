import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { Task } from "@openair/shared";
import type { WorkbenchArtifact } from "../../../../model/workbenchTask";
import {
  ScientificArtifactPresentation,
  classifyScientificArtifactPresentation,
} from "../../../artifacts/ScientificArtifactPresentation";
import { FinalResultPanel } from "../../FinalResultPanel";

const legacyResult: NonNullable<Task["finalResult"]> = {
  finalResultId: "final-1",
  completionDecisionId: "decision-1",
  scientificStateVersion: 7,
  disposition: "partial",
  directAnswer: "Legacy accepted answer.",
  keyFindings: ["Legacy accepted finding."],
  evidenceRefs: ["artifact-internal-evidence-ref"],
  scientificResult: {
    hidden_internal_fact: "Must not be re-rendered by FinalResultPanel.",
  },
  scientificSourceRefs: ["artifact-readable", "artifact-opaque"],
  uncertainty: [
    "Completion diagnostic must not become a second research report.",
  ],
  gaps: ["Completion gap must not become a second research report."],
  limitations: [
    "Completion limitation must not become a second research report.",
  ],
  acceptedScientificFacts: { hidden_historical_fact: "Do not render." },
  validatedAt: "2026-09-15T02:00:00Z",
  validationOutcome: {
    contractVersion: "scientific_completion_integrity_v3",
    validatorRef: "core:scientific_completion_integrity:v3",
    status: "passed",
    checkIds: ["scientific_state_snapshot"],
    validatedAt: "2026-09-15T02:00:00Z",
  },
};

function nodeArtifact(input: {
  id: string;
  artifactType: string;
  substage: string;
  document: Record<string, unknown>;
}): WorkbenchArtifact {
  return {
    id: input.id,
    type: "execution_output",
    artifactType: input.artifactType,
    title: input.artifactType,
    format: "json",
    relatedStage: "insight_generation",
    substage: input.substage,
    content: JSON.stringify(input.document),
  } as WorkbenchArtifact;
}

afterEach(() => cleanup());

describe("T5 R2.3 node-product-first presentation", () => {
  it("prioritizes the accepted answer before full node products", () => {
    const artifact = nodeArtifact({
      id: "answer-first",
      artifactType: "current_research_conclusion",
      substage: "current_research_conclusion",
      document: { reasoning_summary: "Accepted detailed conclusion." },
    });
    render(
      <FinalResultPanel
        result={{
          ...legacyResult,
          scientificResult: {
            conclusion: {
              provenance: { sourceArtifactId: artifact.id },
              validatedArtifact: {
                reasoning_summary: "Accepted detailed conclusion.",
              },
            },
          },
        }}
        artifacts={[artifact]}
      />,
    );
    const answer = screen.getByTestId("final-result-direct-answer");
    const product = screen.getByTestId("final-result-accepted-product");
    expect(
      answer.compareDocumentPosition(product) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("separates the actual OT answer from detailed reasoning without hiding decision boundaries", () => {
    const artifact = nodeArtifact({
      id: "reading-hierarchy",
      artifactType: "current_research_conclusion",
      substage: "current_research_conclusion",
      document: {
        reasoning_summary: {
          direct_answer: "Only a conditional effect is supported.",
          what_is_supported: "Style sensitivity is observed.",
          what_is_not_supported:
            "Independent causal attribution is unavailable.",
          conclusion_strength: "Limited evidence.",
          conditions_most_pronounced: "Use matched-content comparisons.",
          mechanism_calibration:
            "Several confounded mechanisms remain possible.",
          unexpected_scientific_observation:
            "A new observation must remain readable.",
        },
        scope: "Only the present task family.",
        assumptions: "Keep the content equivalent.",
        uncertainty: "No independent measurements yet.",
        next_action: "Run the controlled comparison.",
      },
    });
    render(<ScientificArtifactPresentation artifact={artifact} />);
    for (const text of [
      "Only a conditional effect is supported.",
      "Independent causal attribution is unavailable.",
      "Use matched-content comparisons.",
      "A new observation must remain readable.",
      "Only the present task family.",
      "Keep the content equivalent.",
      "No independent measurements yet.",
      "Run the controlled comparison.",
    ]) {
      expect(screen.getByText(text).closest("details")).toBeNull();
    }
    const reasoning = screen.getByText(
      "Several confounded mechanisms remain possible.",
    );
    expect(reasoning.closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("详细机制论证")).toBeInTheDocument();
  });

  it("renders accepted v3 research content using its formal artifact identity and preserves boundaries", () => {
    const artifact = nodeArtifact({
      id: "accepted-conclusion",
      artifactType: "current_research_conclusion",
      substage: "current_research_conclusion",
      document: { reasoning_summary: "Superseded content must not be reused." },
    });
    const result = {
      ...legacyResult,
      directAnswer: null,
      keyFindings: [],
      scientificResult: {
        accepted: {
          provenance: {
            sourceArtifactId: artifact.id,
            workUnitId: "internal-work-unit",
          },
          validatedArtifact: {
            reasoning_summary:
              "The current evidence supports only a controlled design.",
            scope: "Only the confirmed research question is covered.",
            uncertainty: "No judge measurements were performed.",
            next_action: "Run the missing comparison.",
          },
        },
        unbound: {
          validatedArtifact: { reasoning_summary: "Unbound internal fact." },
        },
      },
    };
    const { rerender } = render(
      <FinalResultPanel result={result} artifacts={[artifact]} />,
    );
    expect(
      screen.getByText(
        "The current evidence supports only a controlled design.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("No judge measurements were performed."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Only the confirmed research question is covered."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Superseded content must not be reused."),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("internal-work-unit")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Unbound internal fact."),
    ).not.toBeInTheDocument();
    rerender(<FinalResultPanel result={result} artifacts={[]} />);
    expect(
      screen.queryByTestId("final-result-accepted-product"),
    ).not.toBeInTheDocument();
    rerender(
      <FinalResultPanel
        result={result}
        artifacts={[{ ...artifact, artifactType: "opaque", substage: null }]}
      />,
    );
    expect(
      screen.queryByTestId("final-result-accepted-product"),
    ).not.toBeInTheDocument();
  });

  it("keeps FinalResultPanel as a thin legacy shell instead of rebuilding scientific state", () => {
    render(<FinalResultPanel result={legacyResult} />);

    expect(screen.getByText("Legacy accepted answer.")).toBeInTheDocument();
    expect(screen.getByText("Legacy accepted finding.")).toBeInTheDocument();
    expect(
      screen.queryByText("Must not be re-rendered by FinalResultPanel."),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Do not render.")).not.toBeInTheDocument();
    expect(
      screen.queryByText("artifact-internal-evidence-ref"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("artifact-opaque")).not.toBeInTheDocument();
    expect(
      screen.queryByText(
        "Completion diagnostic must not become a second research report.",
      ),
    ).not.toBeInTheDocument();

    expect(
      screen.queryByTestId("final-result-scientific-sources"),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("keeps CS execution, evidence sufficiency, and the formal indeterminate verdict independent", () => {
    const artifact = nodeArtifact({
      id: "cs-conclusion",
      artifactType: "reproduction_conclusion",
      substage: "reproduction_conclusion_recommendation",
      document: {
        verdict: "indeterminate",
        answer: {
          text: "The current evidence does not support a reproduction verdict yet.",
          evidence_refs: ["comparison:1"],
        },
        scope: {
          text: "Frozen revision and STS evaluation scope.",
          evidence_refs: ["baseline:1"],
        },
        execution_status: "succeeded",
        evidence_sufficiency: "insufficient",
        evidence_refs: ["artifact:internal-comparison"],
        limitations: [
          {
            text: "Only the confirmed evaluation scope is covered.",
            evidence_refs: [],
          },
        ],
        unknowns: [
          {
            text: "The comparison basis is still incomplete.",
            evidence_refs: [],
          },
        ],
        next_steps: [
          {
            step_id: "step-1",
            action: "Obtain the missing comparison evidence.",
            reason: "It can change the current judgment.",
            expected_judgment_effect: "Resolve the indeterminate verdict.",
            target_definition_id:
              "cs_reproduction:result_comparability_judgment",
            requires_design_change: false,
          },
        ],
      },
    });

    render(<ScientificArtifactPresentation artifact={artifact} />);

    expect(screen.getByText("实际执行：已执行")).toBeInTheDocument();
    expect(screen.getByText("证据：证据不足")).toBeInTheDocument();
    expect(screen.getByText("复现结论：当前无法判定")).toBeInTheDocument();
    expect(screen.queryByText("复现达标")).not.toBeInTheDocument();
    expect(
      screen.queryByText("artifact:internal-comparison"),
    ).not.toBeInTheDocument();
  });

  it("renders OT conclusion, unknowns, and next step as separate formal node products", () => {
    const conclusion = nodeArtifact({
      id: "ot-current",
      artifactType: "current_research_conclusion",
      substage: "current_research_conclusion",
      document: {
        scope: "Applies to the confirmed material system and operating window.",
        reasoning_summary:
          "Current evidence supports a local, conditional relationship.",
        assumptions: ["Compared measurements use the same evaluation basis."],
        uncertainty: ["Long-duration behavior remains uncertain."],
        next_action:
          "Do not promote this slot into the separate recommendation product.",
        evidence_assessment: "BORROWED_EVIDENCE_MUST_NOT_RENDER",
      },
    });
    const unknowns = nodeArtifact({
      id: "ot-unknowns",
      artifactType: "key_unknowns_and_candidate_explanations",
      substage: "key_unknowns_candidate_explanations",
      document: {
        scope: "Unknowns that can change the current judgment.",
        reasoning_summary:
          "Interface chemistry remains the highest-value unresolved discriminator.",
        assumptions: [],
        uncertainty: [
          "Two candidate explanations still require distinguishing evidence.",
        ],
        next_action: "Collect discriminating evidence.",
      },
    });
    const next = nodeArtifact({
      id: "ot-next",
      artifactType: "next_research_recommendation",
      substage: "next_research_recommendation",
      document: {
        scope: "Next research decision.",
        reasoning_summary:
          "Prioritize the experiment that distinguishes the competing explanations.",
        assumptions: [],
        uncertainty: [],
        next_action:
          "Measure the interface before and after cycling under the confirmed condition.",
      },
    });

    const { rerender } = render(
      <ScientificArtifactPresentation artifact={conclusion} />,
    );
    expect(
      screen.getByTestId("workflow-node-ot-current-conclusion"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Current evidence supports a local, conditional relationship.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("BORROWED_EVIDENCE_MUST_NOT_RENDER"),
    ).not.toBeInTheDocument();

    rerender(<ScientificArtifactPresentation artifact={unknowns} />);
    expect(
      screen.getByTestId("workflow-node-ot-key-unknowns"),
    ).toHaveTextContent("Interface chemistry remains");

    rerender(<ScientificArtifactPresentation artifact={next} />);
    expect(
      screen.getByTestId("workflow-node-ot-next-recommendation"),
    ).toHaveTextContent("Prioritize the experiment");
    expect(
      screen.getByText(
        "Measure the interface before and after cycling under the confirmed condition.",
      ),
    ).toBeInTheDocument();
  });

  it("preserves nested OT scientific reasoning, scope and uncertainty without rendering provenance", () => {
    const artifact = nodeArtifact({
      id: "ot-no-carrier",
      artifactType: "current_research_conclusion",
      substage: "current_research_conclusion",
      document: {
        scope: { responsibility: "Confirmed research scope." },
        reasoning_summary: {
          answer: "A controlled design remains a conditional recommendation.",
        },
        assumptions: [{ text: "The compared content must remain equivalent." }],
        uncertainty: [
          { summary: "No measurements establish a causal effect yet." },
        ],
        next_action: { text: "Perform the actual comparison." },
        provenance: { workUnitId: "INTERNAL_WORK_UNIT_DO_NOT_RENDER" },
      },
    });

    const { container } = render(
      <ScientificArtifactPresentation artifact={artifact} />,
    );
    expect(screen.getByText("Confirmed research scope.")).toBeInTheDocument();
    expect(
      screen.getByText(
        "A controlled design remains a conditional recommendation.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("The compared content must remain equivalent."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("No measurements establish a causal effect yet."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Perform the actual comparison."),
    ).toBeInTheDocument();
    expect(container.textContent).not.toContain(
      "INTERNAL_WORK_UNIT_DO_NOT_RENDER",
    );
  });

  it("uses the exact PVD finalizer fields and hides a parameter together with its value when public identity is absent", () => {
    const artifact = nodeArtifact({
      id: "pvd-recommendation",
      artifactType: "round_experiment_plan_parameter_recommendation",
      substage: "round_experiment_plan_parameter_recommendation",
      document: {
        decision_status: "recommendation",
        first_run_ranking_status: "ranked",
        experiment_count: 1,
        experiments: [
          {
            experiment_id: "experiment_internal_01",
            priority: 1,
            parameters: [
              {
                parameter_key: "power",
                display_name: "Power",
                recommended_value: 250,
                unit: "W",
              },
              {
                parameter_key: "internal_only_key",
                display_name: null,
                recommended_value: 9876,
                unit: "arb",
              },
            ],
            design_rationale:
              "Highest information value within the current resource boundary.",
            validation_goal: "Determine whether the target response improves.",
            failure_signal: "Reverse direction if the response degrades.",
            feasibility_conditions: [
              "Keep the substrate and measurement protocol fixed.",
            ],
            recommendation_risks: ["Monitor discharge stability."],
            candidate_lineage_refs: ["internal:lineage"],
          },
        ],
        limited_resource_first_to_run_experiment_ids: [
          "experiment_internal_01",
        ],
        direction_change_signals: [
          "Stop this direction if the target response degrades.",
        ],
        next_step:
          "Run the highest-priority experiment and feed the measured result into the next round.",
        shared_conditions: ["Use the confirmed equipment configuration."],
        shared_risks: ["Monitor discharge stability."],
        important_unknowns: ["Long-duration stability remains unknown."],
        portfolio_note:
          "One feasible experiment is recommended for this round.",
      },
    });

    render(<ScientificArtifactPresentation artifact={artifact} />);

    expect(screen.getByText("Power")).toBeInTheDocument();
    expect(screen.getByText("250 W")).toBeInTheDocument();
    expect(screen.queryByText("internal_only_key")).not.toBeInTheDocument();
    expect(screen.queryByText("9876 arb")).not.toBeInTheDocument();
    expect(
      screen.queryByText("experiment_internal_01"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/参数 1/)).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "Run the highest-priority experiment and feed the measured result into the next round.",
      ),
    ).toBeInTheDocument();
  });

  it("does not infer a scientific presentation kind from JSON shape alone", () => {
    const artifact = nodeArtifact({
      id: "shape-only",
      artifactType: "execution_output",
      substage: "unrelated_substage",
      document: {
        direct_answer: { answer: "Shape should not grant a research role." },
        evidence_supported_conclusions: [],
        conflicts: [],
        evidence_gaps: [],
      },
    });
    const document = JSON.parse(artifact.content ?? "{}") as Record<
      string,
      unknown
    >;
    expect(
      classifyScientificArtifactPresentation(artifact, document),
    ).toBeNull();

    const conflictingIdentity = nodeArtifact({
      id: "conflicting-identity",
      artifactType: "current_research_conclusion",
      substage: "next_research_recommendation",
      document: {
        scope: "Conflicting role identity.",
        reasoning_summary: "Must fail closed.",
        assumptions: [],
        uncertainty: [],
        next_action: "Must not render.",
      },
    });
    const conflictingDocument = JSON.parse(
      conflictingIdentity.content ?? "{}",
    ) as Record<string, unknown>;
    expect(
      classifyScientificArtifactPresentation(
        conflictingIdentity,
        conflictingDocument,
      ),
    ).toBeNull();
  });

  it("does not surface projection/completeness system language in the legacy shell", () => {
    const { container } = render(
      <FinalResultPanel
        result={{ ...legacyResult, directAnswer: null, keyFindings: [] }}
      />,
    );
    const body = container.textContent ?? "";
    for (const forbidden of [
      "public_research_semantics",
      "completenessGaps",
      "公共视图",
      "公开科研语义",
      "科研语义投影",
      "权威可读名称",
      "科研身份",
      "尚未转成用户可读文字",
    ]) {
      expect(body).not.toContain(forbidden);
    }
  });
});
