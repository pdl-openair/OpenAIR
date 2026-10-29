# Migration Notes: candidate-dedup-and-provenance

## Source

**Repository**: OpenTrajectory  
**File**: `literature_merge_filter.py`  
**Function**: `merge_candidates()` (Lines 25-57)  
**Migration Date**: 2026-05-26

## Core Logic Extracted

### Original Implementation

```python
def merge_candidates(groups: list[list[LiteratureCandidate]]) -> list[LiteratureCandidate]:
    """按 dedupe_key 去重，保留引用更多或年份更新的一条；同时把每条文献被多少个来源命中
    写入 extra["matched_source_count"]，供后续质量打分使用。
    """
    best: dict[str, LiteratureCandidate] = {}
    sources_by_key: dict[str, set[str]] = {}
    
    for group in groups:
        for c in group:
            k = c.dedupe_key.lower()
            sources_by_key.setdefault(k, set()).add(c.source_backend)
            
            if k not in best:
                best[k] = c
                continue
            
            o = best[k]
            oc, nc = o.citation_count or 0, c.citation_count or 0
            oy, ny = o.year or 0, c.year or 0
            
            if nc > oc or (nc == oc and ny > oy):
                best[k] = c
            elif nc == oc and ny == oy and "arxiv.org/pdf" in c.pdf_url and "arxiv.org/pdf" not in o.pdf_url:
                best[k] = c

    annotated: list[LiteratureCandidate] = []
    for k, c in best.items():
        n = len(sources_by_key.get(k, {c.source_backend}))
        new_extra = dict(c.extra or {})
        new_extra["matched_source_count"] = str(n)
        annotated.append(replace(c, extra=new_extra))

    ranked = sorted(
        annotated,
        key=lambda x: (-(x.citation_count or 0), -(x.year or 0), x.title),
    )
    return ranked
```

## Adaptations for OpenAIR

### 1. Input/Output Schema Changes

**Original**:
- Input: `List[List[LiteratureCandidate]]` (grouped by source)
- Output: `List[LiteratureCandidate]` with `extra["matched_source_count"]`

**OpenAIR**:
- Input: `{"metadata_candidates": List[MetadataCandidate]}` (flat list, compatible with academic-source-retrieval-skill)
- Output: `{"deduped_candidates": List[DedupedCandidate], "dedup_report": DedupReport}`

### 2. Provenance Handling

**Original**: Tracked source count in `extra` dict  
**OpenAIR**: 
- Merge all `provenance` arrays from duplicate candidates
- Add top-level `matched_source_count` field
- Add `dedup_reason` field for transparency

### 3. Deduplication Key Priority

**Original**: Used `dedupe_key` directly (assumed pre-computed)  
**OpenAIR**: Explicit priority chain:
```
dedupe_key = DOI > arXiv ID > OpenAlex ID > normalized_title hash
```

### 4. Version Selection Logic

**Preserved from original**:
1. Higher `citation_count` wins
2. If tied, more recent `year` wins
3. If still tied, prefer arXiv PDF version
4. If all equal, first-seen wins

**Added**:
- `dedup_reason` field documents why each version was selected:
  - `"unique"`: No duplicates found
  - `"higher_citations"`: Won by citation count
  - `"more_recent"`: Won by year
  - `"arxiv_pdf_preferred"`: Won by having arXiv PDF
  - `"first_seen"`: Arbitrary tie-break

### 5. Reporting

**Original**: No explicit report  
**OpenAIR**: Added `dedup_report` with:
- `input_count`: Total candidates before dedup
- `output_count`: Unique candidates after dedup
- `duplicates_removed`: Number merged
- `multi_source_candidates`: Count of candidates found by 2+ sources
- `dedup_keys_used`: Breakdown by key type (DOI/arXiv/OpenAlex/title)

## Not Migrated

The following functions from `literature_merge_filter.py` are **NOT** part of this Skill:

- `score_and_rank_candidates()` (L122-189): Multi-dimensional scoring → will be part of:
  - **#9 candidate-source-scoring**: Quality scoring
  - **#10 candidate-tiering**: Tier assignment
  - **#11 topk-source-selection**: Top-K selection
- `heuristic_literature_filter()` (L191-237): Year/citation/overlap filtering → may become separate filter Skill

## Dependencies

**None**. This Skill is pure logic with no external dependencies:
- No LLM calls
- No API calls
- No database access
- No file system access (beyond reading input JSON)

## Testing Strategy

### Fixture Coverage

The `examples/request.json` covers:

1. **DOI duplicate** (candidates 1-2): Same DOI from OpenAlex and Semantic Scholar
   - S2 version has higher citations (48 vs 45) → S2 wins
   - Provenance merged from both sources
   - `matched_source_count = 2`

2. **Normalized title duplicate** (candidates 4-5): No DOI, matched by title
   - S2 version has higher citations (31 vs 28) → S2 wins
   - Provenance merged from both sources
   - `matched_source_count = 2`

3. **arXiv + DOI duplicate** (candidates 6-7): Same paper, arXiv preprint + published version
   - Both have same citations (8) and year (2024)
   - arXiv version has PDF → arXiv wins
   - Provenance merged from both sources
   - `matched_source_count = 2`
   - `dedup_reason = "arxiv_pdf_preferred"`

4. **Unique candidate** (candidate 3): No duplicates
   - `matched_source_count = 1`
   - `dedup_reason = "unique"`

### Expected Output

- Input: 7 candidates
- Output: 4 unique candidates
- Duplicates removed: 3
- Multi-source candidates: 3 (all except candidate 3)

## Future Enhancements

1. **Fuzzy title matching**: Current `normalized_title` requires exact match after normalization. Could add Levenshtein distance threshold.
2. **Author disambiguation**: Could merge candidates with same title but different author name formats.
3. **Venue normalization**: Could recognize "J. Power Sources" = "Journal of Power Sources".
4. **Conflict resolution UI**: For ambiguous duplicates, could flag for manual review.

## Validation Checklist

- [x] Input schema compatible with academic-source-retrieval-skill output
- [x] Output schema includes all required fields
- [x] Provenance arrays merged correctly
- [x] `matched_source_count` computed from unique source names
- [x] `dedup_reason` documents selection logic
- [x] Dedup report includes all statistics
- [x] No external dependencies
- [x] Fixture covers all dedup scenarios
- [x] SKILL.md documents capability boundaries
