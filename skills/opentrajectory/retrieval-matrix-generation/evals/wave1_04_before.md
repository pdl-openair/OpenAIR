# Wave1-04 Before

- Source Truth: `test@38b28df0a93a49df87821dd7c8dd736782ed5106`
- Historical replay: 12 real upstream cases, average `89.8/100`, no critical failure under the then-current rubric.
- New boundary eval was fixed before editing `SKILL.md`: 6 cases in `wave1_04_cases.json`.
- Current `SKILL.md`: 13696 bytes, 275 lines.
- `input.schema.json` / `output.schema.json`: Draft-07 valid; example request/response validate.
- Current 12 raw replay inputs: 12/12 validate against current input schema.
- Historical output snapshots are not current-contract After evidence: they predate the current required `assembled_facets`, and contain 10-12 variants while the current schema requires exactly 8.
- `tests/p0_validation.py` cannot run in this slim pack because `services/api/app.execution.retrieval_matrix_sanitizer` is absent, matching the task's stated environment limitation.

## Highest-value root cause before editing

The instructions conflate a fixed seven-row structural contract with a requirement to populate every intent, including fallback expansion when evidence is sparse. Combined with domain-specific battery adjacent-term examples and permission to add “domain-relevant” mechanisms, this creates three coupled risks: unsupported expansion, filler/near-duplicate queries, and cross-domain prompt contamination. A second concrete contract mismatch says “at least 8” variants although the current schema permits exactly 8.

This is treated as one root cause: **the prompt does not cleanly separate evidence-backed retrieval semantics from fixed output scaffolding**.
