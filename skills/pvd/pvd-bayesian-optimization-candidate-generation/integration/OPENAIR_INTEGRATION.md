# OpenAIR Integration Boundary

This package exposes a clean deterministic BO capability contract.

## Formal source

```text
ScientificState.scientificFacts
→ pvd_bayesian_optimization_candidate_generation.scientific_state_facts
```

The value is delivered unchanged. Shared execution must not construct `project_observations`, split the current process context, or map canonical history into BO-specific state.

The BO Skill itself resolves the canonical current context, applies the existing capability-owned same-system rule, filters current-system real completed observations, and constructs the numeric optimizer matrix internally.

## Frozen workflow boundary

```text
R0: BO not selected
R1+ real feedback + same-system eligible:
Node8 feedback/state update
→ Node6 optional BO
→ Node7 feasibility
→ Node9 final recommendation
```

BO never bypasses Node7 and never becomes the final recommendation authority.

Registry, Binding, shared runtime, ScientificState persistence, and scheduling are outside this package and are not modified by this alignment.
