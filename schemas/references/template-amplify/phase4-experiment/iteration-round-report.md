# Iteration Round Report

> Used during Phase 4b — Type M Stage D Mandatory Iteration Loop
> Location: Presented to user each round, key outcomes recorded in experiment-log.md

## Template

```
=== Iteration Round N Report ===
Hypothesis: [what was wrong]
Change: [what was modified]
Result:
  This round:  XX.X ± Y.Y (primary metric)
  Last round:  XX.X ± Y.Y
  Best baseline: ZZ.Z ± W.W
  Gap to best baseline: ±N.N%
Status: [improving / stagnating / regressing]
Recommendation: [continue iterating / ready for full-scale / consider pivot]
```

## Rules

- Minimum 3 iteration rounds before declaring a method "doesn't work"
- Each round follows: diagnose → hypothesize → fix (ONE change) → measure → report
- User must acknowledge each report before the next round begins
- After 3 consecutive failures → trigger `pivot-or-kill`
- Minimum performance bar before Stage E: primary metric within 80% of best baseline
