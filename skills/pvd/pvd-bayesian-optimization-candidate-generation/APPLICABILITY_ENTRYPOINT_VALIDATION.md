# Applicability Entrypoint Validation

Validation requirements:

1. the evaluator reads canonical `scientificFacts`, not a legacy flat `experimental_system_context` fact;
2. current context is resolved by stable ref;
3. at least one real completed same-system observation exists;
4. `evaluate_process_context()` remains the single scientific same-system comparison rule;
5. first-round/non-reentry selection fails closed;
6. candidate generation is never invoked during applicability evaluation;
7. no shared/runtime PVD special case is required.
