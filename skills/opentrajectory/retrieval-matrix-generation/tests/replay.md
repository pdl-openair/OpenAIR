# Replay Tests: retrieval-matrix-generation P0

## Global checks

- input validates against `input.schema.json`
- output validates against `output.schema.json`
- `search_matrix` has exactly 7 rows
- all 7 `search_intent` values are present exactly once
- priority is only 1, 2, or 3
- `expanded_query_variants` has at least 8 entries
- every query variant has `variant_type`
- `variant_type` is one of: `strict`, `broad`, `fallback`, `exclusion`
- all query terms and query variants are English-only, no CJK
- broad variants must include `AND` before optional OR blocks
- `risk_flags` are not converted into `exclude_terms`

## P0 cases to run later

### Case 1: complete Skill 1 output
Expected:
- constraint names appear in `constraints_utilized`
- strict and broad variants exist
- no CJK query terms

### Case 2: interpreted_constraints is empty
Expected:
- output still has 7 matrix rows
- `validation_flags` includes `input_constraints_empty`
- fallback is clearly marked

### Case 3: risk flags present
Expected:
- risk flags do not appear in `exclude_terms`
- `validation_flags` includes `risk_flags_not_used_as_exclude_terms`

### Case 4: method/mechanism typed as condition
Expected:
- DFT/RAG/ARPES/tool use can still be routed into method/mechanism-oriented intents based on terms/rationale
