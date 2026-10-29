# Replay

## Scope

Single-skill validation for `candidate-source-scoring` only.

## Engineering / direct handoff

```bash
python tests/p0_validation.py
```

Expected:

- public schemas valid
- current `screened_candidates[]` accepted directly with research scoring context
- old `deduped_candidates + facets` input rejected
- production implementation output validates
- `candidate_scores[]` validates against current `candidate-tiering` input contract
- no tier / pool / top-k fields

## Business regression

```bash
python tests/business_validation.py
```

Expected:

- materials / computing-AI / biomed representative cases pass
- missing abstract, degraded access, and incomplete critical evidence requirements are explicit degraded states
- high citation count does not override research relevance
- upstream `exclude` is honored without scorer re-screening
- missing identity is reported rather than fabricated

## Code quality

```bash
python tests/code_validation.py
python -m compileall -q adapter tests
```

Expected:

- no bridge / mapper / compatibility / fallback logic in production files
- no old public input keys
- no external runtime dependencies
- no tiering / Top-K output authority
- production and tests compile
