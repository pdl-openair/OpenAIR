---
name: pvd-round-experiment-plan-parameter-recommendation
description: Generate concrete values and the next-round PVD portfolio from the Cap5 Direction Artifact plus Cap6 Admission Artifact.
---
# PVD Round Experiment Plan & Parameter Recommendation

Capability 7 is the concrete-value authority. Its required formal inputs are exactly `current_context + direction_analysis + feasibility_validation`. Upstream concrete candidates are not required.

Every recommended experiment must cover the exact dynamic formal parameter scope. `block` or `defer` prevents a complete recipe; `conditional_release` conditions must be preserved. Values are generated here, validated against type and the intersection of formal and device hard boundaries, and never copied from Cap5. Optional candidate/BO lineage is provenance only. Unknown or pending facts are not filled. The result is a bounded next-round portfolio, never a global-optimum claim.

Recipes are validated independently. A malformed, incomplete, ambiguous, or out-of-bound recipe is excluded from `experiments` and recorded in `blocked_experiments` with machine-readable reason codes; it does not erase other accepted recipes. Duplicate experiment IDs or priorities make every row in the ambiguous group ineligible. Formal scope, handoff identity, and direction/admission Authority failures still fail finalization.

`first_run_ranking_status` is orthogonal to recommendation existence: zero accepted experiments is `not_applicable`; one or more accepted experiments with no accepted first-run ID is `pending`; and a non-empty accepted-ID subset is `ranked`. A single candidate is never automatically ranked. Invalid first-run IDs are removed and recorded in `blocked_experiments`. The model-authored `next_step`, `important_unknowns`, and rationale fields remain unchanged.
