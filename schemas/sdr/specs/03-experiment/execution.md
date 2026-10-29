# Experiment — Execution

## Execution Summary

<!-- Brief overview of what was executed, when, and overall status -->

---

## Phase 4a — Exploratory Run

<!-- Quick validation before full-scale commitment.
     This phase is mandatory — do NOT skip to 4b directly. -->

### Exploratory Objectives

<!-- What this exploratory run aims to validate:
     Type M: 1-2 strongest baselines, error analysis, method prototype
     Type D: preprocessing + quality control, one core analysis pipeline
     Type C: 1 benchmark vs 1-2 competitors, installation test -->

### Exploratory Records

#### M-xxx: <!-- Step Name -->

- **Started at:** <!-- timestamp -->
- **Completed at:** <!-- timestamp -->
- **Environment:** <!-- actual environment details -->
- **Parameters used:** <!-- actual parameter values -->
- **Observations:** <!-- anything notable during execution -->
- **Issues encountered:** <!-- any problems and how they were resolved -->

<!-- Repeat for each exploratory task -->

### Exploratory Findings

- **Expected vs actual:** <!-- Did results match Phase 3 predictions? -->
- **Surprises:** <!-- Anything unexpected? -->
- **Plan assessment:** <!-- Is the method/analysis plan still appropriate? -->

### Exploration Review

<!-- MANDATORY STOP POINT — User must choose one: -->

| Option | Description |
|--------|-------------|
| 1. Proceed to 4b | Results match expectations |
| 2. Local adjustment | Minor changes needed, no redesign |
| 3. Targeted redesign | Specific issues need method-stage discussion |
| 4. Return to method stage | Fundamental design problems |
| 5. Return to problem stage | Research question itself is flawed (rare) |

**User decision:** <!-- 1 / 2 / 3 / 4 / 5 -->
**Date:** <!-- -->
**Notes:** <!-- -->

---

## Phase 4b — Full-Scale Execution

### Execution Records

#### M-xxx: <!-- Step Name -->

- **Started at:** <!-- timestamp -->
- **Completed at:** <!-- timestamp -->
- **Environment:** <!-- actual environment details -->
- **Parameters used:** <!-- actual parameter values -->
- **Observations:** <!-- anything notable during execution -->
- **Issues encountered:** <!-- any problems and how they were resolved -->

<!-- Repeat for each task step -->

### Deviation from Plan

<!-- Any deviations from the tasks defined in 02-method/task.md -->

| Deviation | Planned (M-xxx) | Actual | Reason | Impact Assessment |
|-----------|-----------------|--------|--------|-------------------|
| <!-- --> | <!-- --> | <!-- --> | <!-- --> | High / Medium / Low / None |

### Baseline Execution

<!-- Type M/H: record baseline reproduction results -->

| Baseline | Source | Dataset | Primary Metric | Seeds | Status | Notes |
|----------|--------|---------|---------------|-------|--------|-------|
| <!-- --> | accepted / reference_only / reproduced | <!-- --> | <!-- --> | <!-- --> | complete / failed / partial | <!-- --> |

### Experiment Matrix Completion

<!-- Track completion of all runs declared in 02-method/task.md experiment matrix -->

| Run ID | Status | Result Summary | Notes |
|--------|--------|---------------|-------|
| R-001 | complete / failed / skipped | <!-- --> | <!-- --> |
| R-002 | complete / failed / skipped | <!-- --> | <!-- --> |

---

## Negative Results

<!-- All failed experiments MUST be recorded here.
     Negative results are DATA — they prevent repeating the same mistakes.
     No silent drops allowed. -->

| # | Experiment | Expected Result | Actual Result | Failure Hypothesis | Action Taken |
|---|-----------|----------------|--------------|-------------------|-------------|
| 1 | <!-- description --> | <!-- what we expected --> | <!-- what actually happened --> | <!-- why we think it failed --> | <!-- what we changed next --> |
| 2 | <!-- --> | <!-- --> | <!-- --> | <!-- --> | <!-- --> |

---

## Notes

<!-- Additional observations, informal notes, ideas for follow-up -->
