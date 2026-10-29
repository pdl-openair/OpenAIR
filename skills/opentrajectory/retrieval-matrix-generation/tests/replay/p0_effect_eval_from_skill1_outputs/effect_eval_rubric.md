# Skill 2 P0 Effect Evaluation Rubric

## Goal

Evaluate whether `retrieval-matrix-generation` can convert real Skill 1 outputs into useful English search matrices and Boolean query variants.

This evaluates query generation quality, not actual database retrieval results.

## Use order

1. First run `replay_cases_raw.json`.
2. If raw input fails schema validation because `downstream_filter_rules` are objects, record this as a Skill 2 P0 input contract issue.
3. After schema compatibility is fixed, rerun raw cases.
4. `replay_cases_schema_compatible.json` is only a temporary diagnostic set, not the final proof.

## Score per case: 100 points

### 1. Input contract and schema validity: 15 pts
- accepts real Skill 1 output shape
- output validates against output.schema.json

### 2. Intent matrix coverage: 15 pts
- exactly 7 rows
- all 7 intents present once
- priority is correct and useful

### 3. Query variant structure: 15 pts
- at least 8 variants
- each has `variant_type`
- strict/broad are both present
- broad syntax has AND before optional OR block

### 4. English and syntax quality: 15 pts
- no CJK
- no full Chinese query translated as one long query
- no internal labels
- Boolean syntax is valid enough for generic database use

### 5. Relevance and recall strategy: 20 pts
- core topic is covered
- constraints are reflected
- mechanisms/methods/metrics are represented where useful
- fuzzy cases stay broad enough
- precise cases preserve explicit constraints

### 6. Deduplication and diversity: 10 pts
- variants are not just minor rewordings
- adjacent query is relevant but not drifting
- query roles differ meaningfully

### 7. Safety and noise control: 10 pts
- risk_flags are not used as NOT/exclude terms
- no invented material, benchmark, threshold, or database
- noise_exclusion is empty or based only on explicit exclusions/out_of_scope

## Critical failures

Any of the following should trigger manual review:

1. Any CJK in query terms or query variants.
2. Output is invalid JSON or schema-invalid.
3. risk_flags become NOT/exclude terms.
4. Methods/mechanisms are treated as material hard filters.
5. A fuzzy query gets invented exact thresholds or specific systems.
6. All variants are near duplicates.
7. Query variants are so narrow that likely relevant papers would be systematically missed.

## Suggested thresholds

- Single case pass: >= 80
- Fuzzy/underspecified case acceptable: >= 75 if no critical failure
- Total average >= 82 and no critical failure: P0 effect pass
- Total average >= 85 and no critical failure: strong pass
