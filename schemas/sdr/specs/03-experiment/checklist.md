# Checklist: Experiment

## Review Checklist

### execution.md

- [ ] Phase 4a exploratory run completed
- [ ] User has made exploration review decision (1-5)
- [ ] Phase 4b full-scale execution completed
- [ ] All tasks (M-xxx) from method have been executed or explicitly marked as skipped
- [ ] All runs in experiment matrix have been completed (R-xxx)
- [ ] Deviations from plan are documented with reasons
- [ ] Negative results are recorded (no silent drops)

### analysis.md

- [ ] Each finding has an F- ID assigned
- [ ] Every finding maps to at least one artifact (A-xxx) as evidence
- [ ] Every hypothesis (H-xxx) has been evaluated against findings
- [ ] Confidence level is stated for each finding
- [ ] Deviations from pre-registered analysis plan are documented
- [ ] Traceability matrix is complete: G → Q → H → M → A → F

### argument-blueprint.md

- [ ] Elevator pitch is specific and non-trivial
- [ ] Core argument is 3-5 sentences with clear thesis
- [ ] Minimum 4 content points, each with CLAIM + EVIDENCE + INTERPRETATION
- [ ] Narrative arc is coherent (opening → build-up → insight → resolution)
- [ ] Limitations are honestly stated
- [ ] Acknowledged gaps are documented

### Stage-level

- [ ] No orphan IDs (findings without artifacts, hypotheses without findings, etc.)
- [ ] Experiment matrix from task.md is fully resolved
- [ ] Minimum success criteria from research-config.yaml are met

### Type-specific

**Type M (Method):**
- [ ] All methods ran to completion (not partial/prematurely stopped)
- [ ] Method outperforms at least one baseline on primary metric
- [ ] Ablation study complete — every key component tested
- [ ] Baselines are actually run (not just copied from papers with different settings)
- [ ] Statistical significance reported (p-values, confidence intervals)

**Type D (Discovery):**
- [ ] Story line evidence chain complete — no narrative gaps
- [ ] Alternative explanations addressed with evidence
- [ ] Domain plausibility check passed
- [ ] All planned analyses from experiment_design.md executed

**Type C (Computational-tool):**
- [ ] Correctness verified against gold standard
- [ ] Performance benchmarks completed
- [ ] Scalability tested at multiple scale points
- [ ] Case studies completed (2-3)

**Type H (Hybrid):**
- [ ] Both M-type and D-type checklist items satisfied
- [ ] Cross-track findings are integrated

## Reviewer Sign-off

| Role | Name | Date | Result |
|------|------|------|--------|
| Researcher | <!-- --> | <!-- --> | PASS / REVISE |
| Reviewer (if applicable) | <!-- --> | <!-- --> | PASS / REVISE |

## Revision Notes

<!-- If REVISE: what needs to change, why, and by when -->
