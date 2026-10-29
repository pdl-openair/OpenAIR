---
name: pvd-evidence-record-deep-verification
description: Independently verify one selected PVD research record without issuing downstream-use authorization.
---

# PVD Evidence Record Deep Verification

Return one JSON object. The final message does not include an output schema. Do not emit or decide `allowed_uses`.

- `decision_value`: `decision_relevant` / `context_only` / `no_material_value` / `uncertain_value`.
- `decision_summary`: one string.
- `key_matches`, `important_differences`, `transfer_limits`, and `conflicts`: string arrays. Empty when none apply.
- `measurement_compatibility`: `compatible` / `partially_compatible` / `incompatible` / `unknown`.
- `uncertainty`: object with `level` (`low` / `moderate` / `high` / `unknown`) and `reasons`. Do not replace unknown with a low-uncertainty claim.
- `support_findings`: one object per supplied `support_target_slots` entry, in that order. Each has `support_level` (`direct` / `directional` / `mechanism_only` / `background_only` / `not_transferable` / `insufficient`), `support_basis`, `conditions`, `key_matches`, `important_differences`, `measurement_compatibility`, `transfer_limits`, `uncertainty`, and `evidence_refs_used` taken only from supplied `evidence_items`.
- `verified_relations`: empty when no relation is actually verified. Each item has `relation_type`, `factor`, `factor_change`, `response`, `response_change`, `objective_alignment`, `conditions`, `context_factors`, `confounders`, and `evidence_refs_used`.

## Responsibility
Perform **one-record-at-a-time** scientific verification. Judge whether this record actually supports the selected support targets and relation questions once material/system identity, process conditions, geometry, measurement definition, confounding, and transfer boundaries are considered.

## Scientific rules
- Treat this call as independent from sibling records. Do not infer facts from records that are absent from this input.
- Verify factor, factor change, response, response change, contrast quality, measurement compatibility, confounding/context factors, and missing dependencies before claiming transferability.
- A shared material label or metric name is insufficient for numeric comparability.
- Correlation, co-varying factors, and multi-factor recipes do not establish an isolated causal effect.
- Preserve contradictory or incomplete evidence and explicit uncertainty.
- Use only `evidence_refs_used` that are supplied in `evidence_items`; never invent provenance.
- The order of `support_findings` must exactly match `support_target_slots`.

## Hard authorization boundary
**Do not emit or decide `allowed_uses`.** Deep Verification may judge scientific support and transfer limits, but Final Synthesis alone owns downstream evidence-use authorization.
