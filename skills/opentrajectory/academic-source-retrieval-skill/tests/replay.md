# Replay Tests: academic-source-retrieval-skill P0

## Global schema checks

- input validates against `input.schema.json`
- output validates against `output.schema.json`
- `metadata_candidates` is an array (may be empty)
- every candidate has all required fields; missing metadata fields are `null`, not absent
- `candidate_id` is non-empty string
- every candidate has `dedupe_key`, `normalized_title`, `external_ids`
- `external_ids` contains keys: doi / arxiv_id / openalex_id / semantic_scholar_id / corpus_id / pmid / pmcid
- `source_name` is one of: `openalex`, `semantic_scholar`, `arxiv`, `deepxiv`, `agentic_data`
- `query_variant_type` is one of: `strict`, `broad`, `fallback`, `exclusion`, or `null`
- `provenance` is a non-empty array for every candidate
- every provenance item has: query_index / source_result_index / source_rank / retrieved_at / retrieval_mode
- `retrieval_report` has all required fields
- `retrieval_report.total_candidates` equals `len(metadata_candidates)`
- `retrieval_report.source_counts` keys match actual sources used

## Skill boundary checks

- no PDF download attempted (no local file paths in output)
- no dedup performed (same paper from multiple sources appears as separate candidates)
- no scoring fields in candidates (no `score`, `relevance_score`, `rank`)
- no OA resolver fields (no `oa_status`, `html_available`, `tei_available`)
- no LLM calls to judge relevance
- `source_url` / `raw_metadata` may contain PDF URLs — these are metadata only, not downloaded

## Default source checks

- default `target_sources` does NOT include `deepxiv` or `agentic_data`
- `deepxiv` / `agentic_data` only appear when explicitly listed in `retrieval_config.target_sources`

---

## Case 1: normal expanded_query_variants input (Skill 2 output style)

**Input:** `examples/request.json` (3 English query variants, 3 default sources)

**Expected:**
- `metadata_candidates` non-empty
- each candidate has `matched_query` matching one of the input query strings
- each candidate has `search_intent` matching the variant's `search_intent`
- every candidate has `dedupe_key` / `normalized_title` / `external_ids`
- every provenance item has `query_index` / `source_result_index` / `source_rank` / `retrieved_at` / `retrieval_mode`
- `retrieval_mode` is `expanded_query_variant` for all candidates
- `fallback_used: false`

---

## Case 2: search_matrix fallback (empty expanded_query_variants)

**Input:**
```json
{
  "user_query": "anode fast charging cycle stability",
  "expanded_query_variants": [],
  "search_matrix": [
    {
      "search_intent": "core_topic",
      "query_terms": ["anode material", "fast charging"],
      "must_include_terms": ["anode material", "fast charging"],
      "optional_terms": ["cycle stability"],
      "exclude_terms": [],
      "priority": 1
    }
  ]
}
```

**Expected:**
- `fallback_used: true`
- query built from `search_matrix.must_include_terms`
- `retrieval_mode` is `search_matrix_fallback` in provenance
- `retrieval_report.risk_flags` includes `empty_query_variants_fallback_to_matrix`

---

## Case 3: Chinese user_query only — no English query variants (P0 critical)

**Input:**
```json
{
  "user_query": "比较不同负极材料在快充条件下循环稳定性"
}
```

**Expected:**
- `metadata_candidates: []`
- `fallback_used: true`
- `retrieval_report.risk_flags` includes `missing_english_query_variants`
- NO retrieval attempted — Chinese user_query must NOT be used as search query
- output is valid JSON

---

## Case 4: partial source failure (optional source in target_sources)

**Input:** same as Case 1, but `target_sources` includes `deepxiv` (SDK not installed)

**Expected:**
- `deepxiv` appears in `retrieval_report.failed_sources` with a non-empty `error`
- `partial_success: true`
- candidates from other sources still present
- no exception thrown

---

## Case 5: optional source not in target_sources — auto-skipped

**Input:** `examples/request.json` (default sources: openalex, semantic_scholar, arxiv)

**Expected:**
- `deepxiv` and `agentic_data` do NOT appear in `retrieval_report.source_counts`
- they do NOT appear in `failed_sources` (they were never attempted)
- no error thrown

---

## Case 6: duplicate candidates not deduped

**Input:** 2 query variants that are semantically similar (likely to return same papers)

**Expected:**
- same paper (same DOI or arxiv_id) may appear multiple times with different `candidate_id`
- each occurrence has its own `provenance` entry with its own `query_index`
- `dedupe_key` and `external_ids` are populated for each occurrence (same values)
- `retrieval_report.total_candidates` counts all occurrences, not unique papers

---

## Case 7: source returns incomplete metadata

**Input:** any valid input; simulate source returning a candidate with no abstract

**Expected:**
- candidate is retained (not dropped)
- `abstract: null`
- `retrieval_warnings` includes `"abstract_missing"`
- `dedupe_key` / `normalized_title` / `external_ids` still present

---

## Case 8: all sources fail

**Input:** valid English query, but all sources configured to fail

**Expected:**
- `metadata_candidates: []`
- `retrieval_report.partial_success: false`
- `retrieval_report.failed_sources` lists all attempted sources
- `retrieval_report.total_candidates: 0`
- no exception thrown, valid JSON returned

---

## Case 9: source returns PDF URL in raw_metadata

**Input:** any valid input; source returns a candidate with a PDF URL

**Expected:**
- `source_url` may contain the PDF URL (metadata only)
- no local file path in output (no download attempted)
- no `oa_status` / `html_available` / `tei_available` fields in candidate
- `retrieval_warnings` does NOT include any OA-related warning (OA check is out of scope)

---

## Case 10: Chinese user_query only — no English query variants (P0.1)

**Input:** `examples/chinese_only_request.json`
- `user_query` is Chinese
- no `expanded_query_variants`
- no `search_matrix`

**Expected:** `examples/chinese_only_response.json`
- `metadata_candidates: []`
- `retrieval_report.total_candidates: 0`
- `retrieval_report.fallback_used: true`
- `retrieval_report.partial_success: false`
- `retrieval_report.risk_flags` includes `missing_english_query_variants`
- `retrieval_report.risk_flags` includes `chinese_user_query_not_used_as_search_query`
- Chinese `user_query` does NOT appear in any `matched_query` or provenance field

---

## Case 11: Non-English query variants rejected (P0.1)

**Input:**
```json
{
  "user_query": "我想检索锂金属电池快充电解液",
  "expanded_query_variants": [
    {
      "query": "锂金属电池 快充 电解液",
      "variant_type": "broad",
      "search_intent": "core_topic",
      "priority": 1
    }
  ]
}
```

**Expected:**
- Skill must not execute retrieval
- `metadata_candidates: []`
- `retrieval_report.fallback_used: true`
- `retrieval_report.total_candidates: 0`
- `retrieval_report.risk_flags` includes `non_english_query_terms`
- `check_input_invariants()` must reject this input (CJK detected in `expanded_query_variants[].query`)
