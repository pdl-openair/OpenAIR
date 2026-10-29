# Final synthesis quality evals

This directory separates the pre-change representative set from the post-change boundary expansion.

## Core set

`quality_cases.json` contains the 10 cases fixed before the Skill prompt was changed:

1. high-consistency evidence;
2. conflicting evidence;
3. one weak record;
4. author interpretation only;
5. quantitatively comparable records;
6. non-comparable conditions;
7. complete mechanism packet;
8. missing mechanism packet;
9. gap-heavy evidence;
10. insufficient evidence that still requires a bounded direct answer.

Each case contains a schema-valid input and a semantic oracle (`must`, `must_not`, `expected_layers`). The oracle is for scientific grading, not for keyword matching in production code.

## Boundary expansion

`boundary_cases.json` adds two post-change checks:

- association must not be upgraded to causation;
- a mechanism `gap` edge must not be used as support for a mechanism.

## Scoring

Grade model outputs with the task rubric:

- direct answer to the original question: 20;
- fidelity to ResearchRecords: 25;
- conflict, uncertainty, and boundary handling: 15;
- conclusion / lead / hypothesis separation: 15;
- hypothesis falsifiability: 10;
- research-lead scientific value: 10;
- concision and performance: 5.

Any critical failure defined in `TASK.md` disqualifies a 97+ claim regardless of numeric subtotal.

Use the same model, provider, structured-output path, temperature, and equivalent runtime conditions for Before and After. Do not claim a live quality score from fixture validation alone.
