# Skill 1 P0 Effect Replay Evaluation Rubric

## Purpose

This replay set evaluates whether `constraint-interpretation-skill` correctly interprets research constraints before retrieval.

It does **not** evaluate final literature retrieval quality. That requires Skill 2 and real search execution.

## Coverage

- Battery materials: 3 cases
- Agent systems: 3 cases
- Theoretical materials: 3 cases

## Scoring

Total: 100 points per case.

### 1. Constraint coverage: 30 pts
- 30: all expected constraints detected
- 20: one minor expected constraint missed
- 10: one major expected constraint missed
- 0: main constraint structure wrong

### 2. Constraint typing: 15 pts
- material system, method, condition, metric, evidence requirement should not be confused.

### 3. Confidence calibration: 15 pts
- Explicit numerical constraints with unit: 0.85–0.92
- Qualitative consensus constraints: 0.55–0.70
- Ambiguous/subjective constraints: 0.30–0.50

### 4. Downstream search term quality: 15 pts
- English only
- no CJK
- includes expected core English terms
- no far-drift expansion

### 5. Filter-rule safety: 15 pts
- ambiguous conditions must not become hard filters
- methods/mechanisms must not become material filters
- the full user query must not become one fake constraint

### 6. Risk flag quality: 10 pts
- key ambiguity/conflict/unit/scope risks identified

## Suggested pass criteria

- Single case pass: >= 80
- Direction pass: average >= 82 across 3 cases
- Skill 1 P0 effect pass: average >= 85 and no critical failure

## Critical failures

Any of the following should trigger manual review even if the numeric score looks acceptable:

1. downstream_search_terms contain Chinese.
2. full user_query is converted into one constraint.
3. vague conditions become hard filters.
4. methods such as DFT/ARPES/tool use are treated as material filters.
5. Skill output is not valid JSON.
