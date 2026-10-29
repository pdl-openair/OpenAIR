// CANONICAL_SHAPE_TEST_FIXTURE: exercises rendering, not a real PVD run.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { WorkbenchArtifact } from "../../../../model/workbenchTask";
import { ArtifactRenderer } from "../../ArtifactRenderer";
import { scientificArtifactPresentationKind } from "../../ScientificArtifactPresentation";

afterEach(cleanup);

function artifact(
  document: object,
  opts: { type?: string; skill?: string } = {},
): WorkbenchArtifact {
  return {
    id: "test-s2-s4-artifact",
    type: opts.type ?? "evidence_schema",
    artifactType: opts.type ?? "evidence_schema",
    title: "S2/S4 测试产物",
    format: "json",
    relatedStage: "solution_design" as never,
    relatedSkillId: opts.skill,
    content: JSON.stringify(document),
  };
}

// ---------- S2: evidence_schema ----------

function schemaFixture() {
  return {
    evidence_schema: {
      contract: "pvd-evidence-schema/v1",
      evidence_targets: [
        {
          target_id: "target_001",
          target_type: "performance_metric",
          scientific_role: "core",
          description: "薄膜沉积速率随功率变化的定量关系。",
          requiredness: "required",
          completion_criteria: "至少 3 个功率点下的稳态速率。",
          covered_parameter_keys: ["power"],
          comparison_dimensions: ["rate_vs_power"],
          provenance_requirements: ["deposition_rate"],
        },
        {
          target_id: "target_002",
          target_type: "mechanism_evidence",
          scientific_role: "mechanism_support",
          description: "N₂ 比例对薄膜结构的影响机制。",
          requiredness: "preferred",
          completion_criteria: "至少两个 N₂ 比例点的 XRD 表征。",
          covered_parameter_keys: ["n2_flow"],
          comparison_dimensions: ["structure_vs_n2"],
          provenance_requirements: ["xrd_pattern"],
        },
      ],
      gap_taxonomy: ["missing_mechanism", "missing_baseline"],
      uncertainty_rules: ["不同来源的测量几何条件不可直接比较。"],
    },
    coverage_target: {
      formal_parameter_keys: ["power", "n2_flow", "ar_flow", "deposition_time"],
      pending_parameter_keys: ["n2_flow"],
      all_parameter_keys: ["power", "n2_flow", "ar_flow", "deposition_time"],
    },
    pending_parameter_evidence_identities: ["n2_flow"],
    design_warnings: ["部分目标缺少可比基线。"],
  };
}

function retrievalFixture() {
  return {
    retrieval_design: {
      contract: "pvd-evidence-retrieval-design/v1",
      mode: "core_plus_supplemental",
      query_variants: [
        {
          query_id: "query_001",
          search_intent: "core_topic",
          query: "reactive sputtering AlScN deposition rate",
          covered_parameter_keys: ["power"],
          rationale: "核心主题检索覆盖功率-速率关系。",
          priority: 1,
        },
        {
          query_id: "query_002",
          search_intent: "mechanism_focused",
          query: "N2 ratio effect on AlScN film structure",
          covered_parameter_keys: ["n2_flow"],
          rationale: "机制聚焦检索覆盖 N₂ 比例影响。",
          priority: 2,
        },
      ],
      design_rationale: "核心 + 补充检索覆盖四个核心参数的定量与机制证据。",
    },
    coverage_target: {
      formal_parameter_keys: ["power", "n2_flow"],
      pending_parameter_keys: [],
      all_parameter_keys: ["power", "n2_flow", "ar_flow", "deposition_time"],
    },
    pending_parameter_evidence_identities: [],
  };
}

describe("PVD S2 evidence schema / retrieval design presentation", () => {
  it("routes evidence_schema to the specialized view", () => {
    expect(
      scientificArtifactPresentationKind(
        artifact(schemaFixture(), { skill: "pvd_evidence_schema_design" }),
      ),
    ).toBe("pvd_evidence_schema");
  });

  it("renders targets table, coverage, gap taxonomy and warnings in Chinese", () => {
    render(
      <ArtifactRenderer
        artifact={artifact(schemaFixture(), { skill: "pvd_evidence_schema_design" })}
        bodyOnly
      />,
    );
    const view = screen.getByTestId("pvd-evidence-schema-view");
    expect(view).toHaveTextContent("证据目标");
    expect(view).toHaveTextContent("target_001");
    expect(view).toHaveTextContent("薄膜沉积速率随功率变化的定量关系。");
    expect(view).toHaveTextContent("必需");
    expect(view).toHaveTextContent("优先");
    // Coverage (parameter keys translated via shared dict)
    expect(view).toHaveTextContent("功率");
    expect(view).toHaveTextContent("氮气流量");
    // Gap taxonomy translated
    expect(view).toHaveTextContent("缺失机制");
    expect(view).toHaveTextContent("缺失基线");
    // Uncertainty rule kept verbatim
    expect(view).toHaveTextContent("不同来源的测量几何条件不可直接比较。");
    // Design warning surfaced
    expect(view).toHaveTextContent("部分目标缺少可比基线。");
  });

  it("does not surface raw keys under 补充研究信息", () => {
    const { container } = render(
      <ArtifactRenderer
        artifact={artifact(schemaFixture(), { skill: "pvd_evidence_schema_design" })}
        bodyOnly
      />,
    );
    expect(container).not.toHaveTextContent("补充研究信息");
  });

  it("routes retrieval_design to the specialized view and renders query variants", () => {
    expect(
      scientificArtifactPresentationKind(
        artifact(retrievalFixture(), { skill: "pvd_evidence_retrieval_design", type: "retrieval_design" }),
      ),
    ).toBe("pvd_retrieval_design");
    render(
      <ArtifactRenderer
        artifact={artifact(retrievalFixture(), { skill: "pvd_evidence_retrieval_design", type: "retrieval_design" })}
        bodyOnly
      />,
    );
    const view = screen.getByTestId("pvd-retrieval-design-view");
    expect(view).toHaveTextContent("查询变体");
    expect(view).toHaveTextContent("query_001");
    expect(view).toHaveTextContent("核心主题");
    expect(view).toHaveTextContent("reactive sputtering AlScN deposition rate");
    expect(view).toHaveTextContent("核心 + 补充");
  });

  it("exposes full structured content on request", () => {
    render(
      <ArtifactRenderer
        artifact={artifact(schemaFixture(), { skill: "pvd_evidence_schema_design" })}
        bodyOnly
      />,
    );
    expect(screen.queryByText("evidence_schema")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看完整结构化内容" }));
    expect(screen.getByText("evidence_schema")).toBeInTheDocument();
  });
});

// ---------- S4: evidence_applicability ----------

function applicabilityFixture() {
  return {
    analysis_status: "partial",
    evidence_assessments: [
      {
        record_id: "record_1",
        source_id: "src_1",
        evidence_refs: ["evidence_1"],
        key_matches: ["相同材料体系"],
        important_differences: ["几何条件不同"],
        measurement_compatibility: "partially_compatible",
        transfer_limits: ["不可直接数值迁移"],
        uncertainty: ["几何条件差异"],
      },
    ],
    support_conclusions: [
      {
        conclusion_id: "support_conclusion:target_001",
        judgment_target: "target_001",
        evidence_record_refs: ["record_1"],
        evidence_refs: ["evidence_1"],
        support_level: "directional",
        allowed_uses: ["trend_direction"],
        conditions: ["保留靶材体系不确定性"],
        transfer_limits: ["不可直接数值迁移"],
        conflicts: [],
        important_unknowns: ["靶基距未知"],
        rationale: "核验后的记录对比支持方向性使用，但不支持数值迁移。",
      },
      {
        conclusion_id: "support_conclusion:target_002",
        judgment_target: "target_002",
        evidence_record_refs: ["record_1"],
        evidence_refs: ["evidence_1"],
        support_level: "insufficient",
        allowed_uses: [],
        conditions: [],
        transfer_limits: [],
        conflicts: [],
        important_unknowns: ["机制证据缺失"],
        rationale: "当前证据不足以支持该目标的任何正向使用。",
      },
    ],
    conflicts: [],
    important_unknowns: ["靶基距未知"],
    summary: "核验后的证据仅支持方向性使用，不支持直接数值迁移。",
  };
}

describe("PVD S4 evidence applicability presentation", () => {
  it("routes evidence_applicability to the specialized view", () => {
    expect(
      scientificArtifactPresentationKind(
        artifact(applicabilityFixture(), {
          type: "evidence_applicability",
          skill: "pvd_evidence_applicability_comparability_analysis",
        }),
      ),
    ).toBe("pvd_evidence_applicability");
  });

  it("renders summary, authorization cards, compatibility and unknowns in Chinese", () => {
    render(
      <ArtifactRenderer
        artifact={artifact(applicabilityFixture(), {
          type: "evidence_applicability",
          skill: "pvd_evidence_applicability_comparability_analysis",
        })}
        bodyOnly
      />,
    );
    const view = screen.getByTestId("pvd-evidence-applicability-view");
    // Summary
    expect(view).toHaveTextContent("核验后的证据仅支持方向性使用，不支持直接数值迁移。");
    // Authorization card: support level + allowed uses translated
    expect(view).toHaveTextContent("方向性支持");
    expect(view).toHaveTextContent("授权用途：趋势方向");
    expect(view).toHaveTextContent("无正向授权");
    expect(view).toHaveTextContent("证据不足");
    // Transfer limits / conditions / unknowns kept verbatim
    expect(view).toHaveTextContent("不可直接数值迁移");
    expect(view).toHaveTextContent("保留靶材体系不确定性");
    expect(view).toHaveTextContent("靶基距未知");
    // Per-record compatibility
    expect(view).toHaveTextContent("record_1");
    expect(view).toHaveTextContent("部分可比");
    // Analysis status tag
    expect(view).toHaveTextContent("分析受限");
  });

  it("does not surface raw keys under 补充研究信息", () => {
    const { container } = render(
      <ArtifactRenderer
        artifact={artifact(applicabilityFixture(), {
          type: "evidence_applicability",
          skill: "pvd_evidence_applicability_comparability_analysis",
        })}
        bodyOnly
      />,
    );
    expect(container).not.toHaveTextContent("补充研究信息");
  });

  it("does not mis-route a PVD applicability payload as evidence_graph", () => {
    // evidence_graph targets OT assessment-and-research-synthesis; the PVD
    // support_conclusions shape must stay on the PVD specialized view.
    const kind = scientificArtifactPresentationKind(
      artifact(applicabilityFixture(), {
        type: "evidence_applicability",
        skill: "pvd_evidence_applicability_comparability_analysis",
      }),
    );
    expect(kind).toBe("pvd_evidence_applicability");
    expect(kind).not.toBe("evidence_graph");
  });
});
