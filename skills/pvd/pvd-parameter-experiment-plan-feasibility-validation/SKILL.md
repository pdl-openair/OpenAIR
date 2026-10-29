---
name: pvd-parameter-experiment-plan-feasibility-validation
description: Evaluate the formal Cap5 PVD Direction Artifact for feasibility and experimental admission without changing direction or generating concrete values.
---
# PVD Parameter & Experiment Plan Feasibility Validation

Capability 6 directly consumes the complete Cap5 `direction_analysis` Artifact. `factor`, `candidate_ref`, and `candidate_direction` are preserved unchanged.

It owns only `feasible / feasible_with_conditions / insufficient_information / infeasible` and the derived `release / conditional_release / defer / block` gate. `candidate_exploration` is optional auxiliary context only; direction-only admission must work without it.

Unknown facts remain unknown. Runtime/provider `unavailable` is not scientific `infeasible`. This capability does not change direction, invent concrete values, create a portfolio, or require a concrete candidate before the chain may continue.
