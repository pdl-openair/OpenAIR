# Migration Notes: candidate-source-scoring

## Source
- Read-only reference repo:
  - `OpenTrajectory_s02_03a_clean/04_backend/app/services/p025_topk_selector.py`

## Migrated
- `_resolve_facets()`
- `_compute_subscores()`
- `_final_score()`
- text utilities: `_normalize_terms`, `_phrase_hits`, `_score_from_hits`, `_negative_score`, `_safe_int`

## Not Migrated (intentionally)
- `_gate_and_pool()`
- `build_p025_topk_selection()`
- `_apply_title_diversity()`
- `persist_p025_topk_selection()`

## OpenAIR #9 contract alignment
- Input requires `deduped_candidates` + `facets`
- Optional contract input: `research_evidence_contract` / `evidence_contract` / `contract` (P08B-1E-c)
- `facets` inner fields are optional (degrade mode supported)
- Output includes `scoring_report.facets_incomplete/missing_facets/degraded_scoring/risk_flags`
- P08B-1E-c adds `off_domain_penalty`, `slot_coverage_bonus`, `matched_facets.contract_slots/required_slots/off_domain`
- No tier/pool/topk-related output fields
