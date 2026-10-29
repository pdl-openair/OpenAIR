---
name: pvd-process-factor-candidate-experiment-analysis
description: >-
  Analyze the task-defined PVD recommendation parameter scope and determine the next exploration direction for every authorized parameter without generating concrete parameter values or experiment recipes.
---

# PVD Process Factor Candidate Direction Analysis

Return one JSON object. The final message does not include a draft schema. Deterministic finalization restores formal parameter identity and exact scope coverage.

- `analysis_status`: `complete` or `degraded`. Use `degraded` when the formal scope cannot be fully judged; do not invent a stronger direction to look complete.
- `factor_assessments`: one object for every formal parameter, and no others. Each has `factor`, `candidate_direction` (`increase` / `decrease` / `hold` / `change` / `uncertain`), `priority_role` (`primary_lever` / `secondary_lever` / `hold_constant` / `uncertain`), `evidence_refs`, `diagnosis_refs`, `rationale`, and `uncertainty` (`low` / `medium` / `high`). Refs stay empty when no supplied evidence or diagnosis supports the row; weak or missing evidence uses `uncertain`.
- `recommended_next_experiment_focus`: object with `factor_refs` and `rationale`. Directional only; no numeric setpoints.
- `limitations`: strings. Empty when there is no extra limitation.

## Responsibility
Determine, for every parameter in the formal dynamic scope, whether the next scientifically justified direction is `increase`, `decrease`, `hold`, `change`, or `uncertain`.

This capability owns **direction judgment only**. It does not own concrete values, executable candidates, recipes, feasibility decisions, or experiment portfolios.

## Formal input contract
- The exact parameter scope is `current_context.recommendation_parameter_set.parameters`.
- Analyze every authorized `parameter_key` exactly once and no others.
- `evidence_applicability`, when present, may support direction reasoning only within its scientific-use boundaries.
- `feedback_analysis`, when present, supplies feedback-round diagnosis context. First-round and feedback-round execution use the same direction-analysis responsibility.

## Rules
- Use only the 407b direction vocabulary: `increase`, `decrease`, `hold`, `change`, `uncertain`.
- Weak, conflicting, missing, or non-transferable evidence may produce `uncertain`; do not force a stronger direction.
- Unknown facts remain unknown. Do not infer equipment limits, setpoints, ranges, or missing process values.
- Preserve evidence and feedback/diagnosis references that actually support each direction assessment.
- `recommended_next_experiment_focus` may identify which formal parameters deserve attention next, but it must remain directional and non-numeric.
- Do not emit concrete parameter values, candidate settings, candidate experiment groups, recipes, feasibility claims, or final portfolios.
- The parameter scope is dynamic. A historical four-parameter PVD case is only one possible scope instance.
