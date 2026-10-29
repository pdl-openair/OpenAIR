# Migration Notes: topk-source-selection

## Source
- Read-only reference repo:
  - `OpenTrajectory_s02_03a_clean/04_backend/app/services/p025_topk_selector.py`

## Migrated (#11 scope)
- Top-K selection logic from `build_p025_topk_selection()`
- dynamic thresholds:
  - `core_min`
  - `dynamic_target`
  - `dynamic_cap`
  - `score_min`
  - `extended_score_min`
- selected outputs assembly:
  - `selected_for_p03`
  - `extended_selected`
  - `supporting_papers`
  - `method_inspiration_papers`
  - `excluded_examples`
- plan/stats/risk assembly:
  - `p03_acquisition_plan`
  - `risk_summary`
  - `selection_stats`
- lightweight optional title diversity adjustment

## Not Migrated (intentionally)
- `_compute_subscores()`
- `_final_score()`
- `_gate_and_pool()`
- `persist_p025_topk_selection()`
- any file writing logic

## Contract boundary
- Input only from #10 `source_tiers[]`
- No #9/#10 output fields emitted (`candidate_scores`, `source_tiers`, `tiering_report`)
