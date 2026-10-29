---
name: pvd-evidence-applicability-final-synthesis
description: Synthesize verified record analyses and exclusively decide downstream evidence-use authorization.
---

# PVD Evidence Applicability Final Synthesis

Return one JSON object. The final message does not include an output schema. This stage alone may emit `allowed_uses`.

- `analysis_status`: `complete` / `partial` / `insufficient` / `not_needed`.
- `evidence_assessments`: objects for verified records actually used, or an empty array when none succeeded. Each has `record_id`, `key_matches`, `important_differences`, `measurement_compatibility` (`compatible` / `partially_compatible` / `incompatible` / `unknown`), `transfer_limits`, and `uncertainty` strings.
- `support_conclusions`: each has `judgment_target`, `evidence_record_refs`, `support_level` (`direct` / `directional` / `mechanism_only` / `background_only` / `not_transferable` / `insufficient`), `allowed_uses` (at most one of `mechanism` / `trend_direction` / `magnitude_reference` / `direct_numeric`; empty means no positive authorization), `conditions`, `transfer_limits`, `conflicts`, `important_unknowns`, and `rationale`.
- `conflicts` and `important_unknowns`: string arrays. Empty when none.
- `summary`: one bounded string. Do not hide a target-specific limit only here.

## Responsibility
Form the final T05 scientific judgment from **verified record analyses only**. This is the sole scientific stage allowed to emit `allowed_uses`.

## Authorization rules
- `judgment_target` must be one of the supplied frozen T03 `target_id` values.
- Never authorize a target that lacks a successful Deep Verification result in `verified_record_analyses`.
- `allowed_uses` has exactly four positive classes: `mechanism`, `trend_direction`, `magnitude_reference`, and `direct_numeric`. Evidence-relation facts such as matched/relative comparison are not permissions. Keep at most the strongest decision-specific class for each support target.
- `direct_numeric` requires materially aligned system context, measurement definition/calibration, critical process conditions, and a defensible observed value-setting relationship; similarity alone is insufficient.
- A record may remain useful for mechanism or trend reasoning even when direct numeric transfer is unsafe.
- Preserve conflicts, missing dependencies, failed record verifications, and uncertainty. Unknown stays unknown.
- Do not generate candidate parameter settings or recommendations.
- For every support conclusion, explicitly select `evidence_record_refs` from successful Deep Verification results for that same support target. Positive authorization requires at least one such verified record; never cite an unverified sibling record.
- Preserve target-specific `transfer_limits`, `conflicts`, and `important_unknowns`; these bound the authorization and must not be hidden only in a global summary.
- If evidence is insufficient, use empty `allowed_uses`; never invent authorization to make the output look complete.

## Deterministic provenance boundary
`evidence_record_refs` is a scientific selection made by Final Synthesis. Stable `conclusion_id` and exact T04 `evidence_refs` are code-owned: the parent adapter validates the selected record/target pairs and deterministically materializes their already-verified `evidence_refs_used`. Do not invent IDs or raw Evidence references.

## Inputs
`verified_record_analyses` are the only record-level scientific judgments admitted to authorization. `raw_evidence_items` are exact cited evidence rows carried for traceability; do not reinterpret uncited raw material as if it had passed Deep Verification.
