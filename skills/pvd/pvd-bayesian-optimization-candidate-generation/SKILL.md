---
name: pvd-bayesian-optimization-candidate-generation
description: >-
  PVD Bayesian Optimization Candidate Generation
---

# PVD Bayesian Optimization Candidate Generation

## Role

This deterministic Skill is an **optional later-round candidate generator** inside the frozen PVD path:

```text
real experiment feedback in ScientificState
→ same-system BO applicability
→ optional BO candidates
→ Node7 feasibility validation
→ Node9 final four-parameter recommendation
```

It does not participate in R0, does not own ScientificState, and does not produce the final recommendation.

## Public input

The public input contains one formal scientific source only:

```text
scientific_state_facts
```

It is the exact canonical PVD `ScientificState.scientificFacts` subtree defined by the frozen PVD scientific-state fact contract. The caller does **not** prebuild `process_context` or `project_observations`, and it does not select a model profile.

The model profile is frozen capability-owned configuration in `applicability_evaluator.json`.

## Skill-local preparation

Inside this Skill only, deterministic code:

1. resolves `current_experimental_system_context_ref` to the canonical context registry;
2. checks the current context with the existing `evaluate_process_context()` same-system rule;
3. filters `experiment_history` to the current experimental system;
4. accepts only real `actual_execution` with `status=executed` and `validity=valid`;
5. accepts only real final valid measurements matching the profile target metric;
6. extracts the four canonical PVD parameters and builds the numeric observation matrix required by the optimizer.

Recommendation values, planned execution, invalid/partial execution, invalid/provisional measurements, and cross-system history are never promoted into BO training observations.

This internal numeric preparation is not a public contract and is not a Shared Binding mapper.

## Output boundary

Output candidates are model-generated candidate points with predictions/uncertainty. They are not observed facts and not approved experiment plans. Every candidate must still pass Node7 before Node9 can use it.
