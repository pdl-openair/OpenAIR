# Skill 1 P0.1 Realistic Chinese Replay

This package is designed for realistic effect validation after the Skill 1 P0.1 patch.

## Why this set exists

The earlier P0 replay set was useful for controlled validation, but real users often write Chinese, mixed, vague, incomplete, or judgment-heavy research questions.

This set tests that reality.

## Files

- `replay_cases.json`: 12 Chinese user-query cases.
- `gold_expectations.json`: expected anchors and forbidden behaviors.
- `effect_eval_rubric.md`: scoring rules.
- `claude_code_runner_prompt.md`: prompt for real Claude Code runtime evaluation.

## Placement

Recommended path:

```text
constraint-interpretation-skill/
  tests/
    replay/
      p0_1_realistic_cn_eval/
        replay_cases.json
        gold_expectations.json
        effect_eval_rubric.md
        claude_code_runner_prompt.md
        README.md
```

## Important

This is not designed to produce a beautiful pass. It is designed to expose whether the skill works under realistic Chinese input.
