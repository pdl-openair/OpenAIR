---
name: pvd-experimental-feedback-round-analysis
description: >-
  Diagnose one explicitly selected PVD experiment from canonical experiment facts, formally authorized
  Cap4 comparability evidence and prior factor/recommendation context without generating next-round parameters.
---

# PVD Experimental Feedback & Round Analysis

Return one JSON object. The final message does not include an output schema. Echo the supplied `experiment_ref` unchanged. Leave an array empty when the corresponding fact is absent; do not fill measurements from a recommendation or plan.

- `analysis_status`: `complete` / `partial` / `insufficient`.
- `execution_analysis`: `status` is `aligned` / `deviated` / `partial` / `not_executed` / `unknown`; `deviations` are `{statement, basis_refs}` objects.
- `measurement_analysis`: `status` is `usable` / `usable_with_caution` / `insufficient` / `conflicted` / `not_supplied`; `limitations` use the same statement shape. When status is `insufficient` or `not_supplied`, `result_change.state` is only `uncertain` or `not_assessable`.
- `result_change`: `state` is `improved` / `degraded` / `unchanged` / `mixed` / `uncertain` / `not_assessable`, plus `basis_refs` and `comparison_refs` drawn from supplied identities.
- `findings`: each has `epistemic_state` (`observed` / `inferred` / `hypothesized` / `unknown`), `statement`, `basis_refs`, `uncertainty`, and `missing_evidence`. Observed or inferred findings need basis refs; a hypothesis also states uncertainty and missing evidence; `unknown` states missing evidence.
- `anomalies` and `confounders`: `{statement, basis_refs}` arrays.
- `causal_boundary`: `causality_established` stays false, with a `statement` and `basis_refs`. One round never proves causality.
- `unresolved_questions`: strings.
- `upstream_context`: `authorized_evidence_refs`, `factor_basis_refs`, and `recommendation_basis_refs`. Cite only supplied identities, and cite Cap4 evidence only within its existing `allowed_uses`.
- `downstream_readiness`: `status` is `usable` / `usable_with_caution` / `not_ready`, plus `reasons`. This is not a next-round recommendation.
- `summary`: one bounded diagnosis.

## Responsibility
Diagnose **one explicitly selected `experiment_ref`**. Explain what was planned, what was actually executed,
what was measured, what can and cannot be inferred, which prior judgments remain usable, and whether the
diagnosis is ready for downstream decision-making.

This Skill interprets existing facts. It does not create a new experiment plan or next-round parameter recommendation.

## Formal inputs
- `experiment_ref`: required stable identity. Never replace it with "latest", array tail, or another experiment.
- `scientific_state_facts`: canonical PVD ScientificState fact slice. Keep planned execution, actual execution,
  measurements, and process events as distinct fact classes; missing facts remain missing.
- `evidence_comparability`: optional **current formal Cap4 Artifact** from
  `pvd-evidence-applicability-comparability-analysis`. Consume `support_conclusions[].allowed_uses` directly;
  do not reconstruct `evidence_analyses`, `support_assessments`, or another comparability schema.
- `factor_analysis`: optional finalized factor/direction Artifact as prior scientific context.
- `recommendation`: optional finalized recommendation Artifact as prior decision context, never as actual execution.
- `current_project_context`: optional stable project context.

## Cap4 authorization boundary
The current Cap4 positive-use enum is exactly:
`mechanism`, `trend_direction`, `magnitude_reference`, `direct_numeric`.

Authorization is fail-closed:
1. Never infer authorization from `support_level`, score, relation, field presence, or prose.
2. Empty `allowed_uses` means no positive scientific-use authorization.
3. `relative_comparison` is not an authorization and must not be accepted as an alias for `trend_direction`.
4. Preserve 407b Node8 scientific scope: `mechanism` and `magnitude_reference` may be positive diagnosis basis;
   `trend_direction` remains outside Cap8 diagnosis use.
5. 407b contains no explicit Cap8 grant for current `direct_numeric`; therefore Cap8 does not promote it into a
   positive diagnosis basis. It may remain visible as upstream Cap4 context, but cannot be cited as positive Cap8 evidence basis.
6. Keep Cap4 transfer limits, conflicts, important unknowns, and formal evidence identities attached to the original
   support conclusions. Do not synthesize a second authorization layer.

## Analysis rules
1. Resolve `experiment_ref` exactly once. Never silently pick another experiment.
2. Keep recommendation, planned execution, actual execution, and measurement separate.
3. Missing actual execution or measurement facts remain unknown/insufficient; never fill them from recommendation or plan.
4. Diagnose execution fidelity before interpreting outcomes.
5. Distinguish `observed`, `inferred`, `hypothesized`, and `unknown`. Inference/hypothesis must state uncertainty;
   hypothesis/unknown must state missing evidence.
6. Multi-parameter changes, process events, measurement mismatch, or non-comparable history must be represented as
   confounding/limitations. Temporal order or one round never proves causality.
7. Every scientific finding or deviation must carry basis refs that resolve to supplied formal experiment facts,
   factor/recommendation context, or a Cap4 conclusion/evidence identity whose existing `allowed_uses` authorizes Cap8 use.
8. `downstream_readiness` describes whether this diagnosis can support a later decision. It is not a next-round recommendation.

## Output boundary
- Output diagnosis, basis refs, epistemic state, causal/measurement limits, unresolved questions and downstream readiness.
- Do not rewrite ScientificState, round identity, factor analysis, recommendation Artifacts, Workflow state, or BO state.
- Do not emit concrete next-round parameter values, candidate sets, optimization proposals, or an experiment plan.
- Unknown equipment/process facts remain unknown; do not fill them from general PVD knowledge.
