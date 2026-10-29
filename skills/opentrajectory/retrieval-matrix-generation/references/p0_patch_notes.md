# P0 Patch Notes

## Scope

This patch fixes P0 contract and safety issues in `retrieval-matrix-generation`.

## Changes

1. Input schema
   - `user_query` now requires `minLength: 1`.
   - `interpreted_constraints[]` now requires key Skill 1 fields.
   - `downstream_search_terms` must not contain CJK.

2. Output schema
   - `search_intent` is constrained to the 7 supported intents.
   - `priority` is constrained to 1, 2, or 3.
   - `expanded_query_variants[]` now requires `variant_type`.
   - Query strings and query term arrays reject CJK.
   - `quality_report.validation_flags` added.

3. SKILL.md
   - Clarified this skill generates generic Boolean queries, not database-specific final syntax.
   - Clarified 7 intents are a P0 contract / default template.
   - Clarified `risk_flags` must not become `exclude_terms`.
   - Clarified empty `user_query` is invalid input.
   - Added strict/broad/exclusion/fallback variant semantics.

4. references
   - Fixed broad Boolean syntax: optional OR block must be preceded by AND.
   - Removed use of `risk_flags` as noise-exclusion source.

5. examples
   - Updated request to resemble Skill 1 P0.1 output.
   - Updated response to include `variant_type` and validation flags.

## Non-goals

- No end-to-end retrieval execution.
- No database-specific query syntax.
- No source scoring.
- No paper deduplication.
- No P1 `search_phase` or semantic dedup keys yet.
