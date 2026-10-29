# Replay Test: candidate-dedup-and-provenance

## Test Status

**Status**: ✅ Executable single-Skill validation
**Created**: 2026-05-26
**Last Run**: covered by `tests/p0_validation.py`

## Test Scope

This is a **single-Skill smoke test** that validates:
- Input schema compliance
- Deduplication logic correctness
- Provenance merging
- Output schema compliance
- Dedup report statistics

**Not tested**:
- Live online retrieval API behavior
- Exhaustive production-scale validation

## Test Data

**Input**: `examples/request.json` (7 candidates)

### Scenarios Covered

1. **DOI duplicate** (2 candidates → 1 output)
   - `10.1016/j.jpowsour.2023.233456` from OpenAlex (45 cites) and Semantic Scholar (48 cites)
   - Expected: S2 version wins (higher citations)
   - Expected `matched_source_count`: 2

2. **Normalized title duplicate** (2 candidates → 1 output)
   - "Thermal Management for Fast Charging Electric Vehicles" from OpenAlex (28 cites) and S2 (31 cites)
   - Expected: S2 version wins (higher citations)
   - Expected `matched_source_count`: 2

3. **arXiv + DOI duplicate** (2 candidates → 1 output)
   - `10.1149/1945-7111/ace123` from arXiv (with PDF) and OpenAlex (no PDF)
   - Both have 8 citations, year 2024
   - Expected: arXiv version wins (PDF preferred)
   - Expected `dedup_reason`: "arxiv_pdf_preferred"
   - Expected `matched_source_count`: 2

4. **Unique candidate** (1 candidate → 1 output)
   - `arxiv:2305.12345` only from arXiv
   - Expected: Pass through unchanged
   - Expected `dedup_reason`: "unique"
   - Expected `matched_source_count`: 1

## Expected Output

**Output**: `examples/response.json` (4 candidates)

### Dedup Report Validation

```json
{
  "input_count": 7,
  "output_count": 4,
  "duplicates_removed": 3,
  "multi_source_candidates": 3,
  "dedup_keys_used": {
    "doi": 2,
    "arxiv_id": 1,
    "openalex_id": 0,
    "normalized_title": 1
  }
}
```

### Provenance Merging Validation

Each deduplicated candidate should have:
- Merged `provenance` array from all duplicate sources
- Correct `matched_source_count` (count of unique `source_name` in provenance)
- Appropriate `dedup_reason`

## How to Run

### Manual Validation

```bash
# 1. Validate input against schema
python -m jsonschema -i skills/opentrajectory/candidate-dedup-and-provenance/examples/request.json \
  skills/opentrajectory/candidate-dedup-and-provenance/input.schema.json

# 2. Validate output against schema
python -m jsonschema -i skills/opentrajectory/candidate-dedup-and-provenance/examples/response.json \
  skills/opentrajectory/candidate-dedup-and-provenance/output.schema.json
```

### Automated Test

```python
# When adapter implementation is ready
from skills.opentrajectory.candidate_dedup_and_provenance.adapter import deduplicate_candidates

with open("examples/request.json") as f:
    input_data = json.load(f)

output = deduplicate_candidates(input_data)

# Assertions
assert output["dedup_report"]["input_count"] == 7
assert output["dedup_report"]["output_count"] == 4
assert output["dedup_report"]["duplicates_removed"] == 3
assert output["dedup_report"]["multi_source_candidates"] == 3

# Validate DOI duplicate resolution
doi_candidate = next(c for c in output["deduped_candidates"]
                     if c["dedupe_key"] == "doi:10.1016/j.jpowsour.2023.233456")
assert doi_candidate["citation_count"] == 48  # S2 version
assert doi_candidate["matched_source_count"] == 2
assert doi_candidate["dedup_reason"] == "higher_citations"
assert len(doi_candidate["provenance"]) == 2

# Validate arXiv PDF preference
arxiv_candidate = next(c for c in output["deduped_candidates"]
                       if c["dedupe_key"] == "doi:10.1149/1945-7111/ace123")
assert "arxiv.org/pdf" in arxiv_candidate["source_url"]
assert arxiv_candidate["dedup_reason"] == "arxiv_pdf_preferred"
assert arxiv_candidate["matched_source_count"] == 2
```

## Known Limitations

1. **No online retrieval**: this validation is intentionally single-Skill and deterministic
2. **Not exhaustive**: additional cases should be added only when they represent reusable production risks

## Next Steps

1. Keep `examples/request.json` and `examples/response.json` aligned with the current single-Skill contract
2. Add edge cases only when they represent a reusable production risk
3. Re-run `python skills/opentrajectory/candidate-dedup-and-provenance/tests/p0_validation.py` before commit
