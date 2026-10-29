# Skill 2 P0 Effect Eval From Skill 1 Outputs

## Purpose

This package evaluates `retrieval-matrix-generation` using real outputs from `constraint-interpretation-skill`.

This is the correct validation setup because Skill 2 must consume Skill 1 output in the actual pipeline.

## Files

- `replay_cases_raw.json`
  - Preferred test set.
  - Uses actual Skill 1 outputs exactly as provided.

- `replay_cases_schema_compatible.json`
  - Temporary diagnostic set.
  - Converts object-shaped `downstream_filter_rules` into strings.
  - Use only if you need to isolate query-generation behavior before schema compatibility is fixed.

- `gold_expectations.json`
  - Case-level query expectations and forbidden behaviors.

- `effect_eval_rubric.md`
  - Scoring rubric.

- `claude_code_runner_prompt.md`
  - Prompt for Claude Code runtime.

- `precheck_report.md`
  - Findings from inspecting the uploaded Skill 1 outputs.

## Placement

Recommended path:

```text
retrieval-matrix-generation/
  tests/
    replay/
      p0_effect_eval_from_skill1_outputs/
        replay_cases_raw.json
        replay_cases_schema_compatible.json
        gold_expectations.json
        effect_eval_rubric.md
        claude_code_runner_prompt.md
        precheck_report.md
        README.md
```

## Key finding

The uploaded Skill 1 outputs use object-shaped `downstream_filter_rules`.

Skill 2 should ideally accept these raw outputs directly. If the current input schema only accepts string filter rules, fix the schema instead of normalizing the evidence away.
