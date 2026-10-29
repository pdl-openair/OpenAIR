# Capability 7 integration handoff

## What changed inside the Skill

Capability 7 now owns concrete parameter values and next-round portfolio design. Upstream Cap5 values are no longer copied into the final recommendation by the deterministic finalizer.

## Expected upstream artifacts

The existing three top-level inputs remain stable for this isolated migration:

- `current_context`: must expose `recommendation_parameter_set`; may also carry `run_preferences` and explicit `device_hard_boundaries`.
- `candidate_analysis`: supplies direction/candidate scientific facts and lineage. Concrete values in this artifact are not recommendation authority.
- `feasibility_validation`: supplies per-candidate admission decisions, conditions, conflicts, risks and unknowns.

For full 407b-equivalent evidence use, future wiring may additionally project research-record/comparability/ScientificState facts into one of these existing artifacts or evolve the binding in a separate integration task. This package intentionally does not modify Registry/Binding/Workflow.

## Required semantics at the Cap5/6 → Cap7 boundary

1. Every direction/candidate referenced by a Cap7 experiment has a stable `candidate_id`.
2. Cap6 produces exactly one assessment for each candidate identity it evaluates.
3. `block` / `defer` never become recommendation permission.
4. `conditional_release.conditions` remain mandatory and are preserved in the final experiment set.
5. `current_context.recommendation_parameter_set.parameters[]` is the only formal parameter identity set.
6. Explicit device limits should be supplied as numeric formal boundaries when known; unknown limits stay absent/unknown rather than receiving defaults.

## Remaining integration wiring

No shared wiring is changed here. Before enabling this migrated scientific behavior in an end-to-end run, the integration owner must verify that the current binding provides a non-empty `current_context.recommendation_parameter_set` and feasibility assessment identities expected by this contract. If richer evidence/comparability/ScientificState fields are required by the production prompt projection, add them in a separate wiring task rather than re-introducing concrete-value authority into Cap5.
