# Skill 1 P0 Replay Cases

This package contains replay cases for effect validation of `constraint-interpretation-skill`.

## Files

- `replay_cases.json`: 9 executable input cases.
- `gold_expectations.json`: expected detection anchors, forbidden behaviors, and risk flags.
- `effect_eval_rubric.md`: scoring rubric.
- `case_index.md`: quick index by domain.

## How to use

1. Run each `input` object through `constraint-interpretation-skill` in Claude Code runtime.
2. Save each skill output as `outputs/{case_id}.json`.
3. Compare output against `gold_expectations.json`.
4. Score with `effect_eval_rubric.md`.

## What this validates

This validates Skill 1's ability to:
- detect constraints,
- classify constraints,
- calibrate confidence,
- keep search terms English,
- avoid unsafe hard filters,
- flag ambiguity.

It does not validate final retrieval quality. That requires Skill 2 and real search result evaluation.
