# Replay

## Scope
Single-skill validation for `topk-source-selection` only.

## Run

```bash
python skills/opentrajectory/topk-source-selection/tests/p0_validation.py
```

## Expected
- input/output schema valid Draft-07
- example request/response pass schema
- selected_for_p03 count <= dynamic_cap
- selected_for_p03 basic descending by final_score
- selected_for_p03 excludes `tier=excluded`
- empty source_tiers returns degraded=true
- no #9/#10 boundary fields in output
