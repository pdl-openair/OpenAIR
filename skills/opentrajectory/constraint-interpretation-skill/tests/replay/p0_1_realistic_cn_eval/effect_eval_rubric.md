# Skill 1 P0.1 Realistic Chinese Replay Rubric

## Evaluation goal

This set tests real-world Chinese user inputs, including precise, mixed, fuzzy, and underspecified queries.

It is intentionally harder than the earlier P0 replay set. A lower average is acceptable if no critical failure occurs and failure attribution is clear.

## Case scoring, 100 points

1. Constraint coverage: 30
2. Constraint typing: 15
   - P0.1 should use `method` / `mechanism` where appropriate.
3. Confidence calibration: 15
   - explicit numerical constraints: 0.85–0.92
   - qualitative domain consensus: 0.55–0.70
   - vague / evaluative / underspecified: 0.30–0.50
4. Downstream search term quality: 15
   - English only
   - no CJK
   - no literal translation of vague Chinese phrasing such as "real problem"
   - no far-drift expansion
5. Filter-rule safety: 15
   - no hard filters for vague conditions
   - no material filters from methods/mechanisms
   - no invented exact thresholds
6. Risk flag quality: 10
   - missing material system, missing metric, vague condition, broad workflow, or unclear evaluation target should be surfaced.

## Critical failures

Any critical failure means the case requires manual review regardless of score:

- Chinese appears in downstream_search_terms.
- Full user_query becomes one pseudo-constraint.
- Vague condition becomes hard filter.
- Method/mechanism becomes material hard filter.
- Output is invalid JSON or schema-invalid.
- Specific material, compound, benchmark, or numeric threshold is invented without user evidence.

## Suggested thresholds

For this realistic stress set:

- Single case >= 75: acceptable for fuzzy/underspecified case
- Single case >= 80: normal pass
- Average >= 82 with no critical failure: P0.1 realistic regression pass
- Average >= 85 with no critical failure: strong pass

The purpose is not to force pass. Use failures to decide whether Skill 1 needs P0.2 or whether the issue should be handled by Skill 2.
