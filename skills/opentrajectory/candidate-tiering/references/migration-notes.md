# Migration Notes: candidate-tiering

## Source
- Read-only reference repo:
  - `OpenTrajectory_s02_03a_clean/04_backend/app/services/p025_topk_selector.py`

## Migrated
- `_gate_and_pool()` logic
- Gating helpers/threshold rules necessary for tier assignment

## Not Migrated (intentionally)
- `_compute_subscores()`
- `_final_score()`
- `build_p025_topk_selection()`
- `_apply_title_diversity()`
- `persist_p025_topk_selection()`

## Tier enum
- `core`
- `mechanism_support`
- `method_inspiration`
- `benchmark_or_evaluation`
- `background`
- `excluded`

## Contract boundaries
- Input: `candidate_scores[]` from #9
- Output: `source_tiers[]` + `tiering_report`
- No #11 fields: `topk_selection` / `selected_for_p03` / `p03_acquisition_plan`
