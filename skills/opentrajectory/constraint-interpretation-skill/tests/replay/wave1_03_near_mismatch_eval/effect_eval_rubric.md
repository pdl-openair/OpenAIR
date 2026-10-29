# Wave1-03 Near-Mismatch Eval Rubric

This eval was fixed before changing `SKILL.md`.

## Score (100)

- User-constraint fidelity: 25
- Ambiguous / unknown / candidate-value boundary: 20
- Confidence calibration: 15
- `downstream_search_terms` quality: 15
- Filter-rule safety: 15
- Cross-domain robustness and concision: 10

## Critical failure

Any critical failure caps the case below 97 and prevents the overall result from being called 97+:

- inventing a numeric threshold, material family/subclass, benchmark, or user preference;
- promoting ambiguity to a confirmed hard constraint;
- converting a soft preference to a hard filter;
- CJK or explanatory prose in `downstream_search_terms`;
- using `risk_flags` as exclusion criteria;
- adding case-specific domain hard-coding.

## Evidence rule

A static prompt inspection can prove removal of an unsafe instruction, but it cannot prove runtime quality. A 97+ claim requires comparable model outputs for the fixed cases, no critical failures, and evidence that the historical 12 + 9 regression set does not materially regress.
