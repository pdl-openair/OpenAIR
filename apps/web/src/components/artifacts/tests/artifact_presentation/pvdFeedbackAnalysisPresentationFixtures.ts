/**
 * Presentation-only fixtures for the S5 PVD feedback-analysis UI.
 * Shapes follow pvd-experimental-feedback-round-analysis output.schema.json.
 * These are NOT real PVD durable-run results.
 */

export const PRESENTATION_FIXTURE_NOTE =
  "CANONICAL_SHAPE_TEST_FIXTURE — presentation only; not a real PVD run";

export const feedbackAnalysisFixture = {
  analysis_status: "partial",
  experiment_ref: "PVD-R3-1",
  summary:
    "PVD-R3-1 按推荐参数执行，功率出现小幅偏差；FWHM 由 1.95° 降至 1.62°，沉积速率与膜厚符合预期。",
  execution_analysis: {
    status: "deviated",
    deviations: [
      {
        statement: "实际功率 245 W，高于推荐的 240 W。",
        basis_refs: ["measurement:verify-round4-1"],
      },
    ],
  },
  measurement_analysis: {
    status: "usable_with_caution",
    limitations: ["两套最终测量集存在冲突，需确认权威来源。"],
  },
  result_change: {
    state: "improved",
    basis_refs: ["measurement:verify-round4-1"],
    comparison_refs: ["measurement:user-measurement-1"],
  },
  findings: [
    {
      statement: "FWHM 降低表明 (002) 取向在该功率区间得到改善。",
      epistemic_state: "observed",
      basis_refs: ["measurement:verify-round4-1"],
      uncertainty: "仅单次执行，未重复验证。",
      missing_evidence: ["重复实验数据"],
    },
    {
      statement: "功率上调可能与取向改善相关。",
      epistemic_state: "inferred",
      basis_refs: ["evidence:aln-power-trend"],
      uncertainty: null,
      missing_evidence: [],
    },
  ],
  anomalies: [
    {
      statement: "基片表面存在轻微打火痕迹。",
      basis_refs: ["measurement:verify-round4-2"],
    },
  ],
  confounders: [
    {
      statement: "功率与沉积时间在历史轮次间共变，单因素归因受限。",
      basis_refs: ["diagnosis:PVD-R3-1"],
    },
  ],
  causal_boundary: {
    causality_established: false,
    statement: "本轮仅支持关联解释，不能确立功率与取向的因果关系。",
    basis_refs: ["diagnosis:PVD-R3-1"],
  },
  unresolved_questions: [
    "哪一套最终测量集是权威结果？",
    "沉积过程中是否发生靶氮化？",
  ],
  downstream_readiness: {
    status: "usable_with_caution",
    reasons: ["测量冲突未解决，下一轮应优先核对计量来源。"],
  },
  upstream_context: {
    authorized_evidence_refs: ["evidence:aln-power-trend"],
    factor_basis_refs: ["artifact:s6-candidate"],
    recommendation_basis_refs: ["artifact:s8-round1"],
  },
};
