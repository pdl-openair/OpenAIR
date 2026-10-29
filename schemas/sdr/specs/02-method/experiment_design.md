# Method — Experiment Design

<!-- Experiment planning: data, analysis, baselines, ablation, reproducibility, risks.
     Type M/H: all sections required.
     Type C: use Baselines section for competing tools, Analysis Plan for tool evaluation.
     Type D: Data Strategy and Analysis Plan are primary; Baselines/Ablation may be minimal. -->

## Data Strategy

### Data Source

<!-- Where data comes from: collection, API, public dataset, simulation, etc. -->

### Data Collection Method

<!-- Tools, scripts, instruments used -->

### Sample Size and Strategy

<!-- Power analysis result, sampling method, inclusion/exclusion criteria -->

### Storage Format

<!-- File formats, directory structure, naming conventions -->

## Analysis Plan (Pre-registration)

<!-- Declare analysis methods BEFORE seeing results. This prevents HARKING and p-hacking. -->

### Planned Analyses

| Analysis | Target Hypothesis | Method | Tool / Library | Expected Output |
|----------|-------------------|--------|----------------|-----------------|
| <!-- name --> | H-xxx | <!-- e.g. t-test, regression, thematic analysis --> | <!-- e.g. scipy, R --> | <!-- e.g. p-value, effect size --> |

### Significance Criteria

<!-- Alpha level, multiple comparison correction, confidence intervals -->

## Reproducibility Assurance

- **Environment:** <!-- OS, runtime version, dependency versions -->
- **Random seed:** <!-- Fixed seed value, if applicable -->
- **Version control:** <!-- Git commit or tag for analysis code -->

## Baselines

<!-- What methods/tools are compared against. Each baseline should map to a hypothesis. -->

### Baseline Selection

| # | Method / Tool | Priority | Source | Reference | Maps to Hypothesis |
|---|---------------|----------|--------|-----------|-------------------|
| 1 | <!-- name --> | must_include / recommended | official_repo / our_implementation / paper_reported | <!-- citation --> | H-xxx |
| 2 | <!-- name --> | must_include / recommended | official_repo / our_implementation / paper_reported | <!-- citation --> | H-xxx |

### Pre-Computed Results

<!-- If using existing baseline results, document compatibility here. -->

| Method | Dataset | Metric | Value (mean ± std) | Seeds | Source | Compatibility |
|--------|---------|--------|---------------------|-------|--------|---------------|
| <!-- --> | <!-- --> | <!-- --> | <!-- --> | <!-- --> | user_own_run / paper_reported / leaderboard | accepted / reference_only / incompatible |

### Type C — Competing Tools

<!-- Type C only: competing tools for comparison. -->

| # | Tool | Version | Install Method | Configuration | Repo URL |
|---|------|---------|----------------|---------------|----------|
| 1 | <!-- --> | <!-- --> | pip / conda / source / Docker | <!-- --> | <!-- --> |

## Ablation Plan

<!-- Type M/H only: plan which components to remove/replace/simplify.
     Each ablation removes or replaces ONE component at a time.
     All ablations use the same seed set from Reproducibility Assurance. -->

| # | Component | Ablation Method | Expected Impact on Primary Metric | Rationale | Maps to Hypothesis |
|---|-----------|----------------|-----------------------------------|-----------|-------------------|
| 1 | <!-- component name --> | remove / replace / simplify | <!-- predicted delta --> | <!-- why --> | H-xxx |
| 2 | <!-- component name --> | remove / replace / simplify | <!-- predicted delta --> | <!-- why --> | H-xxx |

## Risks and Contingencies

| Risk | Likelihood | Impact | Mitigation / Plan B |
|------|-----------|--------|---------------------|
| <!-- risk description --> | High / Medium / Low | High / Medium / Low | <!-- --> |

## Notes

- Baselines must be run before the proposed method (no exceptions)
- Ablation results feed into analysis.md ablation findings section
- Any deviation from planned baselines or ablations must be documented in execution.md
