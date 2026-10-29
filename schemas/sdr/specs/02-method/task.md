# Method — Task

## Task List

<!-- Concrete, executable steps derived from method_design.md and experiment_design.md.
     Each step MUST have an M- ID for traceability.
     Ordered by execution dependency. -->

## 1. <!-- Task Group Name -->

- [ ] M-001: <!-- task description -->
- [ ] M-002: <!-- task description -->

## 2. <!-- Task Group Name -->

- [ ] M-003: <!-- task description -->
- [ ] M-004: <!-- task description -->

<!-- Additional task groups as needed -->

## Task-to-Hypothesis Mapping

<!-- Which tasks address which hypotheses. -->

| Task | Hypothesis | Purpose |
|------|-----------|---------|
| M-xxx | H-xxx | <!-- what this task validates --> |

## Experiment Matrix

<!-- Machine-checkable run matrix: what MUST happen during execution.
     Declared here, verified at experiment checklist. -->

| Run ID | Role | Method | Dataset / Input | Metrics | Required | Failure Policy | Expected Artifacts |
|--------|------|--------|-----------------|---------|----------|----------------|-------------------|
| R-001 | proposed | <!-- --> | <!-- --> | <!-- --> | yes | debug_then_degrade | <!-- e.g., results/metrics.json --> |
| R-002 | baseline | <!-- --> | <!-- --> | <!-- --> | yes | debug_then_report | <!-- --> |
| R-003 | ablation | <!-- --> | <!-- --> | <!-- --> | yes | debug_then_report | <!-- --> |

### Role Legend

| Role | Meaning |
|------|---------|
| `proposed` | The proposed method / approach |
| `baseline` | Baseline or competing method |
| `ablation` | Ablation variant (one component removed/replaced) |
| `sensitivity` | Parameter sensitivity analysis |
| `exploration` | Exploratory analysis |

### Failure Policy Legend

| Policy | Meaning |
|--------|---------|
| `debug_then_degrade` | Debug, then downgrade to simpler variant if needed |
| `debug_then_report` | Debug, then report partial results |
| `skip` | Skip this run if prerequisite fails |
