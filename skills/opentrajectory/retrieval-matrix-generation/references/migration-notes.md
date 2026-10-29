# Migration Notes: retrieval-matrix-generation

## P0 contract

This Skill receives output from `constraint-interpretation-skill` and generates a stable search matrix plus generic Boolean query variants.

## Input mapping

Required:

| Field | Source |
|---|---|
| `user_query` | original user research question |
| `interpreted_constraints` | Skill 1 `interpreted_constraints` |

Each interpreted constraint should include at least:

- `constraint_name`
- `constraint_type`
- `confidence`
- `downstream_search_terms`
- `risk_flags`

The schema allows additional Skill 1 fields such as `original_phrase`, `candidate_values`, `domain_rationale`, and `needs_user_confirmation`.

## Output mapping

`search_matrix` keeps the 7-row contract for P0.

`expanded_query_variants` now includes `variant_type`:

- `strict`
- `broad`
- `exclusion`
- `fallback`

## Boundaries

Included:
- search matrix generation
- generic Boolean query generation
- query variant typing
- CJK and intent enum constraints
- conservative fallback

Not included:
- database execution
- database-specific syntax adaptation
- paper ranking
- paper deduplication
- source scoring

## Known P0 limitation

The output is generic Boolean syntax, not final Web of Science / Scopus / PubMed-specific syntax. Database adaptation should be handled by a downstream query execution/adaptation step.


## Historical P0.1 compatibility patch — deprecated

Older Skill 1 runtime outputs could carry object-shaped `downstream_filter_rules`. The current production contract has removed this field and Retrieval Strategy now decides must-have, optional, exclusion, and query structure from the complete formal constraint semantics.

Legacy direct inputs may still carry the old property because their item boundary permits additional fields. The registered finalizer checks only whether the key exists, ignores the value wholesale, and emits `deprecated_field_ignored`; it must never inspect or restore the old content as retrieval Authority.

The historical shape was:

```json
"downstream_filter_rules": {
  "type": "array",
  "items": {
    "anyOf": [
      { "type": "string" },
      { "type": "object" }
    ]
  }
}
```

This shape is retained here only as migration history, not as a current input requirement or semantic contract.
