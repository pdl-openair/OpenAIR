# PVD Capability 5 → Capability 6 / 7 Integration Handoff

## Capability 5 authority after this migration

`pvd_process_factor_candidate_experiment_analysis` is a legacy stable Skill identity whose scientific responsibility is now **Candidate Direction Analysis**.

Its authoritative facts are:

- the exact dynamic parameter scope inherited from `current_context.recommendation_parameter_set.parameters`;
- one direction assessment per formal parameter;
- `candidate_direction`: `increase | decrease | hold | change | uncertain`;
- `priority_role`;
- evidence references and feedback/diagnosis references;
- rationale and uncertainty;
- a non-numeric `recommended_next_experiment_focus` over formal parameter identities.

It does **not** authorize concrete parameter values, executable candidates, recipes, feasibility judgments, or final experiment portfolios.

## Capability 6 should consume

Capability 6 (Parameter Feasibility & Experimental Admission) should consume:

1. formal parameter identities from the current project context;
2. Cap5 per-parameter direction facts and uncertainty;
3. the evidence / diagnosis references that justify those direction facts;
4. applicable hard equipment/process boundaries from its own authorized inputs;
5. optional deterministic candidate structures only when a later integration explicitly provides them under the formal workflow contract.

Cap6 must not reinterpret Cap5 direction facts into concrete values merely to preserve the old candidate contract. `uncertain` remains a valid upstream state.

## Capability 7 should consume later

Capability 7 (Experiment Recommendation / Portfolio Design) is the scientific owner of concrete recommendation values and executable experiment portfolio design.

After Cap6 admission is migrated, Cap7 should combine:

- formal dynamic parameter scope;
- Cap5 direction facts;
- Cap6 feasibility/admission status, conditions, and risks;
- applicable evidence/comparability facts;
- ScientificState / current experiment facts;

and only then generate concrete values and experiment groups within hard boundaries.

## Legacy candidate fields

The pre-migration Cap5 fields below are **not scientific Authority** after this change:

- `candidates`;
- candidate-level `parameters[].value`;
- candidate `priority`;
- `experiment_intent` / `validation_goal` attached to a concrete setting;
- `maintained_conditions` / `combination_constraints` as parts of a Cap5 concrete recipe.

If any of these fields still exist elsewhere in current Cap6/Cap7 code or historical artifacts, treat them only as legacy transition structures until those capabilities are migrated. They must not be used to claim that Cap5 produced an authoritative concrete recommendation.

## Wiring boundary

This migration intentionally does not modify Workflow, Registry, Binding, Capability 6, Capability 7, enabled state, or shared runtime. Downstream wiring must be updated only in the later dedicated migration task for those capabilities.
