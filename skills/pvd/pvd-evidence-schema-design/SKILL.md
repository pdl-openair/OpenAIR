---
name: pvd-evidence-schema-design
description: Designs the evidence targets and coverage contract required to judge the current PVD decision, using canonical M1 facts and the formal retrieval design.
---

# PVD EvidenceSchema Design

Return one JSON object. The final message does not include a provider schema.

- `evidence_targets`: at least one object. Each has `target_type` (`experimental_condition` / `performance_metric` / `mechanism_evidence` / `characterization_observation` / `comparison_baseline` / `limitation` / `provenance` / `other`), `scientific_role` (`condition` / `metric` / `mechanism` / `characterization` / `baseline` / `comparability` / `limitation` / `provenance` / `other`), `description`, `requiredness` (`required` / `preferred` / `optional`), `completion_criteria`, `covered_parameter_keys`, `comparison_dimensions`, and `provenance_requirements`.
- `gap_taxonomy`: array drawn only from `missing_mechanism` / `missing_condition` / `missing_metric` / `missing_baseline` / `conflict` / `not_comparable`. Empty when no gap class applies.
- `uncertainty_rules`: strings. Empty when there is no extra uncertainty rule.
- `design_warnings`: strings. Empty when there is no non-blocking warning.

Design evidence targets for the current PVD decision. Use the formal retrieval design (core or deterministically merged core + supplemental) and canonical M1 facts. Every formal parameter and every `pending_evidence` parameter must appear in `covered_parameter_keys` of at least one target. Pending parameters remain evidence-only identities and must not be promoted into the recommendation scope.

Specify what evidence counts as sufficient, which condition/comparison dimensions must be retained, and what provenance is required. Do not retrieve papers, claim that evidence exists, judge direction or feasibility, or recommend parameter values.
