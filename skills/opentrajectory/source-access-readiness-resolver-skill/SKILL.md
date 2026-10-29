---
name: source-access-readiness-resolver-skill
description: Resolve source access readiness for deduplicated OpenTrajectory candidates without downloading, parsing, scoring, ranking, or screening.
---

# source-access-readiness-resolver-skill

## Boundary

This skill resolves access readiness for deduplicated literature candidates from `candidate-dedup-and-provenance`.

It outputs `CandidateWithAccessStatus[]` and marks whether each candidate has structured fulltext, PDF, landing page, metadata-only, blocked, or unknown access state.

## Does

- Inspect candidate metadata and known identifiers.
- Classify available access types: `html`, `xml`, `tei`, `pdf`, `landing_page`, `metadata_only`.
- Prefer structured fulltext over PDF.
- Preserve upstream provenance and append one resolver provenance record.
- Produce warnings, errors, retry hints, and summary counts.

## Does not

- Download PDF.
- Fetch or parse fulltext.
- Parse HTML/XML/TEI/PDF.
- Perform title-abstract screening.
- Score, tier, rank, or select candidates.
- Extract Evidence, SourceAnchor, Lead, or graph data.

## Output file

`source_access_readiness.json`
