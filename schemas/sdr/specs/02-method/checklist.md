# Checklist: Method

## Review Checklist

### method_design.md

- [ ] Methodology is appropriate for the research questions (Q-xxx)
- [ ] Variables are clearly defined and measurable

### experiment_design.md

- [ ] Data source and collection method are specified
- [ ] Sample size is justified (e.g., power analysis)
- [ ] Analysis plan is declared upfront (pre-registration principle)
- [ ] Each planned analysis maps to a hypothesis (H-xxx)
- [ ] Significance criteria are stated
- [ ] Reproducibility requirements are addressed (environment, seed, versioning)
- [ ] Risks identified with mitigation plans

### experiment_design.md

- [ ] Baselines are selected with sources documented
- [ ] Pre-computed results (if any) have compatibility assessment
- [ ] Ablation plan removes/replaces ONE component at a time (Type M/H)
- [ ] Baselines and ablations map to hypotheses

### task.md

- [ ] Each task has an M- ID assigned
- [ ] Tasks are ordered by execution dependency
- [ ] Experiment matrix lists all required runs
- [ ] Each run has a failure policy

### Stage-level

- [ ] Tasks in task.md are derivable from method_design.md and experiment_design.md
- [ ] Task-to-hypothesis mapping is complete
- [ ] Experiment matrix covers all baselines and ablations from experiment_design.md

### Type-specific

**Type M (Method):**
- [ ] Evaluation metrics are appropriate for performance claims
- [ ] Baselines include at least one strong published method
- [ ] Ablation plan covers each key component

**Type D (Discovery):**
- [ ] Analysis framework covers descriptive → main → mechanism → robustness
- [ ] Alternative explanations are pre-identified
- [ ] Sufficiency criteria are defined

**Type C (Computational-tool):**
- [ ] Utility claim stated with quantitative targets (≥2)
- [ ] Evaluation covers correctness, performance, and scalability
- [ ] Competing tools identified (≥2)

**Type H (Hybrid):**
- [ ] Primary track identified (method or discovery)
- [ ] Both M-type and D-type checklist items satisfied

## Reviewer Sign-off

| Role | Name | Date | Result |
|------|------|------|--------|
| Researcher | <!-- --> | <!-- --> | PASS / REVISE |
| Reviewer (if applicable) | <!-- --> | <!-- --> | PASS / REVISE |

## Revision Notes

<!-- If REVISE: what needs to change, why, and by when -->
