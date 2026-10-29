# BO Applicability Entrypoint Contract

The capability-owned applicability evaluator consumes the generic runtime fact set:

- `runtime:research-task:v1`
- `runtime:execution-instance:v1`
- `runtime:scientific-state:v1`

From ScientificState it reads the exact canonical PVD `scientificFacts` object. It resolves the current experimental system through `current_experimental_system_context_ref`, requires at least one usable real same-system observation in canonical `experiment_history`, and delegates the scientific system-match decision to the existing `evaluate_process_context()` implementation.

The BO model profile remains frozen capability configuration (`modelProfileId` in `applicability_evaluator.json`).

Fail-closed behavior:

- missing/malformed authority → `evaluation_failed`;
- missing usable real feedback → `evaluation_failed`;
- current system mismatch → `ineligible`;
- missing required current-system fact → `evaluation_failed`;
- eligible current system + real same-system feedback → `eligible`.

The evaluator never runs candidate generation and contains no Node/Workflow/Registry special case.
