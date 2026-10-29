# Replay

## Scope
Single-skill validation for `candidate-tiering` only.

## Run

```bash
python skills/opentrajectory/candidate-tiering/tests/p0_validation.py
```

## Expected
- Input/output schemas are valid Draft-07
- examples/request.json passes input schema
- examples/response.json passes output schema
- source_tiers non-empty
- all tiers in legal enum
- tier_counts consistent with actual source_tiers
- degraded input flags reflected in tiering_report risk_flags
- no #11 fields (`topk_selection`, `selected_for_p03`, `p03_acquisition_plan`)
