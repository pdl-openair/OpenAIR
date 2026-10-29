---
name: title-abstract-screening-skill
description: Deterministic metadata-only title/abstract screening without network access.
---

# title-abstract-screening-skill

## Boundary

This skill performs minimal deterministic screening using title/abstract and access readiness metadata from `source-access-readiness-resolver-skill` output.

## Does

- Consume candidate metadata and resolver access fields.
- Produce `screened_candidates[]` with `include` / `exclude` / `maybe` decisions.
- Preserve upstream provenance and append screening provenance.
- Emit summary, warnings, and errors.

## Does not

- Access network or download content.
- Parse full text or perform evidence extraction.
- Perform complex ranking, source scoring, or tiering.
- Connect to #9/#10/#11/#12 downstream stages.
- Produce Evidence / SourceAnchor / Lead artifacts.

## Output file

`title_abstract_screening.json`
