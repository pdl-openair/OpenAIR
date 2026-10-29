// CANONICAL_SHAPE_TEST_FIXTURE: exercises rendering, not a real PVD run.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { WorkbenchArtifact } from "../../../../model/workbenchTask";
import { ArtifactRenderer } from "../../ArtifactRenderer";
import {
  scientificArtifactPresentationKind,
  classifyScientificArtifactPresentation,
} from "../../ScientificArtifactPresentation";

afterEach(cleanup);

function artifact(
  document: object,
  opts: {
    type?: string;
    skill?: string;
  } = {},
): WorkbenchArtifact {
  return {
    id: "test-s1-artifact",
    type: opts.type ?? "structured_context",
    artifactType: opts.type ?? "structured_context",
    title: "研究目标与已知条件",
    format: "json",
    relatedStage: "problem_modeling" as never,
    relatedSkillId: opts.skill,
    content: JSON.stringify(document),
  };
}

// Minimal shape mirroring the S1 output.schema.json canonical contract.
function s1Fixture() {
  return {
    structured_context: {
      contract: { kind: "pvd_research_request_understanding", version: "1.0.0" },
      sources: [
        {
          source_id: "q1",
          source_type: "query",
          raw_text: "I am depositing a nitride thin film by reactive magnetron sputtering.",
        },
      ],
      research_context: {
        objectives: [
          {
            assertion_id: "a-obj",
            identity: "user_fact",
            statement: "沉积氮化物薄膜。",
            provenance: { source_id: "q1", raw_expression: "depositing a nitride thin film" },
          },
        ],
        target_metrics: [],
        current_problems: [],
        baseline: [],
        existing_result_summaries: [],
        judgment_criteria: [],
        process_methods: [],
      },
      materials: {
        target: {
          descriptions: [],
          composition: [
            {
              assertion_id: "a-tc",
              identity: "user_fact",
              composition_raw: "Al0.7Sc0.3N",
              provenance: { source_id: "q1", raw_expression: "target is Al0.7Sc0.3N" },
            },
          ],
        },
        film: { descriptions: [], measured_composition_result_refs: [] },
        substrate: [
          {
            assertion_id: "a-sub",
            identity: "user_fact",
            statement: "金属箔衬底。",
            provenance: { source_id: "q1", raw_expression: "substrate is metal foil" },
          },
        ],
        layers: [],
      },
      equipment: {
        deposition_method: [
          {
            assertion_id: "a-dm",
            identity: "user_fact",
            statement: "反应磁控溅射。",
            provenance: { source_id: "q1", raw_expression: "reactive magnetron sputtering" },
          },
        ],
        sputtering_mode: [],
        target_substrate_distance: [
          {
            assertion_id: "a-tsd",
            identity: "user_fact",
            quantity: { value_raw: "90", value_numeric: 90, unit_raw: "mm" },
            provenance: { source_id: "q1", raw_expression: "90 mm" },
          },
        ],
        chamber: [],
        target_size: [],
        substrate_temperature: [],
        gas_and_pressure: {
          n2_ar_ratio: [],
          total_flow: [],
          chamber_pressure: [],
          partial_pressures: [],
        },
        other_conditions: [],
        core_parameters: ["power", "n2_flow", "ar_flow", "deposition_time"],
        parameter_operability: {},
      },
      experimental_system_context: {
        contract: "pvd-experimental-system-context/v1",
        context_status: "partial",
        target_material: "Al0.7Sc0.3N",
        substrate: null,
        target_substrate_distance_mm: 90,
        power_supply_temperature: null,
        target_angle: null,
        field_metadata: {
          target_material: { status: "known", provenance: [{ source_id: "q1", raw_expression: "target" }] },
          substrate: { status: "missing", reason: "未显式提供", provenance: [] },
          target_substrate_distance_mm: { status: "known", provenance: [{ source_id: "q1", raw_expression: "90 mm" }] },
          power_supply_temperature: { status: "missing", reason: "未显式提供", provenance: [] },
          target_angle: { status: "missing", reason: "未显式提供", provenance: [] },
        },
        additional_facts: [],
      },
      rounds: [
        {
          round_id: "local-1",
          round_ref: "R0",
          source_record_ref: "pvd-query-source-record:sha256:000000000000000000000000",
          status: "recommendation_only",
          recommendation: [
            { parameter_key: "power", value_type: "continuous", value: 200, unit: "W", unit_identity: "watt", unit_dimension: "power", identity: "previous_system_recommendation", provenance: { source_id: "q1", raw_expression: "power 200W" } },
            { parameter_key: "n2_flow", value_type: "continuous", value: 30, unit: "sccm", unit_identity: "sccm", unit_dimension: "flow", identity: "previous_system_recommendation", provenance: { source_id: "q1", raw_expression: "n2 30" } },
          ],
          planned_execution: [],
          actual_execution: [],
          unresolved_execution: [],
          fixed_conditions: [],
          other_process_conditions: [],
          measurements: [],
          notes: [],
        },
      ],
      hard_constraints: [
        {
          constraint_id: "hc-temp",
          identity: "user_fact",
          kind: "equipment",
          field: "substrate_temperature",
          operator: "max",
          upper: { value_raw: "250", value_numeric: 250, unit_raw: "C" },
          raw_expression: "Keep substrate temperature below 250 C",
          source_id: "q1",
        },
      ],
      derived_values: [],
      unknowns: [
        { unknown_id: "u-power", identity: "unknown", field: "rounds.R0.actual_execution.power", reason: "尚未执行物理实验。", blocking: false, source_ids: ["q1"] },
        { unknown_id: "u-n2", identity: "unknown", field: "rounds.R0.actual_execution.n2_flow", reason: "尚未执行物理实验。", blocking: false, source_ids: ["q1"] },
      ],
      assumptions: [],
      quality: { non_blocking: true, warnings: [], identity_conflicts: [] },
      parameter_admission_judgments: [],
      parameter_observations: [],
    },
    recommendation_parameter_set: {
      parameters: [
        {
          parameter_key: "power",
          display_name: "功率",
          value_type: "continuous",
          unit: "W",
          allowed_values: [],
          rationale: "溅射功率决定沉积速率。",
          basis_refs: ["q1"],
        },
      ],
    },
    run_preferences: {
      initial_experiment_set_count: 4,
      source: "user_query",
      requested_count: 4,
      capped_to_maximum: false,
    },
  };
}

describe("PVD S1 research context specialized presentation", () => {
  it("routes the S1 structured_context to the specialized view, not generic projection", () => {
    const doc = s1Fixture();
    const input = artifact(doc, { type: "structured_context", skill: "pvd_research_request_understanding" });
    expect(scientificArtifactPresentationKind(input)).toBe("pvd_research_context");
  });

  it("renders objectives, equipment, materials, rounds, constraints and unknowns as Chinese", () => {
    render(<ArtifactRenderer artifact={artifact(s1Fixture(), { skill: "pvd_research_request_understanding" })} bodyOnly />);
    const view = screen.getByTestId("pvd-research-context-view");
    // Objectives
    expect(view).toHaveTextContent("沉积氮化物薄膜。");
    // Equipment facts
    expect(view).toHaveTextContent("反应磁控溅射。");
    expect(view).toHaveTextContent("90 mm");
    // Materials
    expect(view).toHaveTextContent("Al0.7Sc0.3N");
    expect(view).toHaveTextContent("金属箔衬底。");
    // Round recommendation params translated via parameter labels
    expect(view).toHaveTextContent("功率");
    expect(view).toHaveTextContent("200 W");
    // Hard constraint rendered as a tag
    expect(view).toHaveTextContent("substrate_temperature");
    expect(view).toHaveTextContent("250 C");
    // Unknowns aggregated, not a wall of "未知"
    expect(view).toHaveTextContent("rounds.R0.actual_execution.power：尚未执行物理实验。");
    // Run preferences
    expect(view).toHaveTextContent("初始实验组数");
    // System identity status
    expect(view).toHaveTextContent("系统身份部分缺失");
  });

  it("does not surface raw JSON keys under a generic 补充研究信息 label", () => {
    const { container } = render(
      <ArtifactRenderer artifact={artifact(s1Fixture(), { skill: "pvd_research_request_understanding" })} bodyOnly />,
    );
    expect(container).not.toHaveTextContent("补充研究信息");
  });

  it("exposes full structured content on request via the collapsible source view", () => {
    render(<ArtifactRenderer artifact={artifact(s1Fixture(), { skill: "pvd_research_request_understanding" })} bodyOnly />);
    expect(screen.queryByText("recommendation_parameter_set")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看完整结构化内容" }));
    // Raw key surfaces only after expanding the source details.
    expect(screen.getByText("recommendation_parameter_set")).toBeInTheDocument();
  });

  it("also classifies the experimental-context-structuring sibling skill output", () => {
    // The sibling emits the same shape as the top-level object (no structured_context wrapper).
    const siblingDoc = (s1Fixture().structured_context as Record<string, unknown>);
    const input = artifact(siblingDoc, { type: "structured_context", skill: "pvd_experimental_context_structuring" });
    expect(classifyScientificArtifactPresentation(input, siblingDoc as never)).toBe("pvd_research_context");
  });
});
