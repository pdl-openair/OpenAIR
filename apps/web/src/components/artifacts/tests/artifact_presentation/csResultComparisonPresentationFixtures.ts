/**
 * Presentation-only fixtures for T14 CS result comparison UI.
 * Shapes follow production adapter emit / closure consumer contracts.
 * These are NOT real CS durable-run results.
 */

export const PRESENTATION_FIXTURE_NOTE =
  "CANONICAL_SHAPE_TEST_FIXTURE — presentation only; not a real CS run";

const compatible = (note?: string) => ({
  status: "compatible" as const,
  evidence_refs: ["evidence:condition"],
  ...(note ? { note } : {}),
});

export function comparableConditions() {
  return {
    task_scope: compatible("与论文任务一致"),
    implementation: compatible(),
    data_split: compatible(),
    preprocessing: compatible(),
    evaluation_protocol: compatible(),
    metric_definition: compatible(),
    randomness: compatible(),
    hardware_environment: compatible(),
  };
}

export const actualMetricRecordsFixture = {
  status: "complete",
  execution_record_ref: "execution://run-simcse-demo",
  metrics: [
    {
      metric_name: "Avg.",
      actual_value: 81.58,
      value_type: "number",
      unit: null,
      extraction_status: "succeeded",
      source_trace: {
        execution_record_relative_path: "artifacts/execution.json",
        source_kind: "execution_stream",
        stream: "stdout",
        format: "pipe_table",
        source_sha256: "a".repeat(64),
        column: "Avg.",
        column_index: 1,
        raw_value: "81.58",
      },
    },
    {
      metric_name: "STS-B",
      actual_value: null,
      value_type: "number",
      unit: "%",
      extraction_status: "failed",
      failure_code: "metric_not_found",
    },
  ],
  reference_values_used: false,
  partial_failures: [{ opaque: true }],
  unknowns: ["stdout table missing STS-B column"],
};

export const comparisonJudgmentComparableFixture = {
  overall_verdict: "meets_standard",
  comparability: {
    status: "comparable",
    conditions: comparableConditions(),
    condition_mismatches: [],
    evidence_refs: ["evidence:comparability"],
    unknowns: [],
  },
  metric_judgments: [
    {
      metric_name: "Avg.",
      comparison_status: "succeeded",
      meets_rule: true,
      actual: {
        value: 81.58,
        value_type: "number",
        unit: null,
        extraction_status: "succeeded",
        source_trace: { format: "pipe_table" },
      },
      reference: {
        value: 81.5,
        value_type: "number",
        unit: null,
      },
      rule: {
        kind: "absolute_tolerance",
        tolerance: 0.1,
      },
      calculation_trace: {
        rule_kind: "absolute_tolerance",
        actual_operand: 81.58,
        reference_operand: 81.5,
        signed_delta_decimal: "0.08",
        absolute_delta_decimal: "0.08",
        operator: "abs(actual-reference) <= tolerance",
        tolerance_decimal: "0.1",
        allowed_delta_decimal: "0.1",
        result: true,
      },
    },
  ],
  condition_mismatches: [],
  rule_application: {
    deterministic_state: "succeeded",
    comparison_status: "complete",
    deterministic_overall_result: "meets_standard",
    requested_reference_count: 1,
    compared_metric_count: 1,
  },
  evidence_refs: ["evidence:comparability"],
  unknowns: [],
};

export const comparisonJudgmentIndeterminateFixture = {
  overall_verdict: "indeterminate",
  comparability: {
    status: "not_comparable",
    conditions: {
      ...comparableConditions(),
      data_split: {
        status: "mismatch",
        evidence_refs: ["evidence:split"],
        note: "划分协议不一致",
      },
    },
    condition_mismatches: [{ opaque_mismatch_record: true }],
    evidence_refs: ["evidence:split"],
    unknowns: ["comparability_not_established"],
  },
  metric_judgments: [
    {
      metric_name: "Avg.",
      comparison_status: "failed",
      meets_rule: null,
      failure_code: "comparability_not_established",
      actual: {
        value: 81.58,
        value_type: "number",
        unit: null,
        extraction_status: "succeeded",
      },
      reference: {
        value: 81.5,
        value_type: "number",
        unit: null,
      },
      rule: { kind: "exact" },
    },
  ],
  condition_mismatches: [{ opaque_mismatch_record: true }],
  rule_application: {
    deterministic_state: "failed",
    comparison_status: "incomplete",
    deterministic_overall_result: "indeterminate",
    requested_reference_count: 1,
    compared_metric_count: 0,
    failure_code: "comparability_not_established",
  },
  evidence_refs: ["evidence:split"],
  unknowns: ["comparability_not_established"],
};

export const invalidNestedComparisonFixture = {
  overall_verdict: "meets_standard",
  comparability: {
    status: "comparable",
    conditions: { task_scope: { status: "compatible" } },
    condition_mismatches: [],
    evidence_refs: [],
    unknowns: [],
  },
  metric_judgments: [{ metric_name: "Avg.", verdict: "pass" }],
  condition_mismatches: [],
  rule_application: { kind: "exact" },
  evidence_refs: [],
  unknowns: [],
};
