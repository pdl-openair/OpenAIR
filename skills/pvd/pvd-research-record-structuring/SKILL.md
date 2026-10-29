---
name: pvd-research-record-structuring
description: Structure finalized, traceable PVD Evidence items into Research Records that downstream PVD research can consume without record/provenance reshaping.
---

# PVD Research Record Structuring

## Purpose

Convert **already validated PVD Evidence items** into research records whose unit is a real study/sample/run/measurement record rather than a single evidence claim.

Return one JSON object with `record_groups` only. The final message does not include a provider schema. Each group has `group_id`, `grouping_status` (`explicit_record` when the Evidence Nodes explicitly share one record identity, otherwise `ambiguous_singleton`), `evidence_refs`, and `grouping_basis_evidence_refs`. The registered finalizer materializes `evidence_records` and `source_provenance`; do not emit those formal containers or invent `record_id`.

## Inputs

Canonical finalization inputs:

- `evidence_packet`: exact finalized `evidence_node_extraction` output.
- `source_material`: exact finalized `deepxiv_progressive_reading` output used to establish source identity and source anchors.

Model-visible grouping inputs are the bounded 407b views derived deterministically from those canonical inputs:

- `evidence_index`: Evidence identity / source / role metadata only;
- `atomic_grounding_catalog`: every selectable scientific string bound to exactly one `evidence_id`;
- `source_index`: source identity only, without full paper text.

The Provider must reason only from these compact grouping views. Full Evidence/source material remains available to deterministic finalization and provenance checks, not as duplicate model context.

Do not re-read raw paper text to discover new facts. Scientific facts in records must be supported by the finalized Evidence Nodes supplied here.

## Scientific reasoning owned by the model

For each source, decide which Evidence Nodes can be grouped into the same real record.

A multi-node group is allowed only when the Evidence Nodes themselves explicitly establish a common record identity such as the same sample, run, or explicitly identified experiment/study record. Do **not** merge nodes merely because they come from the same paper.

When record identity is not explicit, emit an `ambiguous_singleton` group containing exactly one Evidence Node and `record_role = unresolved_record_identity`. A whole-paper or generic study-level bucket is not a valid substitute for a real research record.

For each group:

1. Preserve explicitly stated `study_id`, `series_id`, or `sample_id` only when the identity text is grounded in the listed grouping-basis Evidence Nodes.
2. Structure explicitly reported PVD facts into the formal PVD `material`, `process`, `outcomes`, and `other_context` vocabulary.
3. Every `known` fact must cite one or more `evidence_refs` from the same record group.
4. Preserve source-literal values and units. Do not normalize, calculate, infer, or fill missing values from domain knowledge.
5. For canonical numeric process fields `power`, `n2_flow`, `ar_flow`, and `deposition_time`, emit a numeric value only when the cited Evidence contains an explicit numeric quantity. A prose Methods sentence may remain grounded Evidence, but it must never become the value of one of these numeric process fields.
6. Use `unknown` for unresolved absence by default. Use `not_reported`, `ambiguous`, `unavailable`, or `not_applicable` only when that status is itself supported by cited Evidence Nodes. A missing value is never silently promoted to `known`.
7. An Evidence Node may support more than one explicitly identified record only when that Evidence Node itself names each reused record identity; each use remains traceable to the same Evidence Node.

## Deterministic finalization

The Skill-local finalizer owns only machine-checkable invariants:

- validate exact upstream Evidence Node and source-material contracts;
- verify every Evidence Node anchor against the supplied source material;
- validate grouping identity and evidence references;
- reject multi-node groups without explicit grounded identity;
- reject `known` facts or measurements whose values are not grounded in their cited Evidence Nodes;
- assign deterministic `record_id` values;
- reuse the upstream `candidate_id` as the stable `source_id`;
- fill absent canonical PVD material/process facts as `unknown` (not `not_reported`);
- if the grouping call is unavailable, deterministically preserve each finalized Evidence Node as one ambiguous singleton record, carrying its claim/quote/conditions through `other_context` and any structured metric through `outcomes`; this degradation must not invent canonical process values;
- emit only the formal `evidence_records` and `source_provenance` shapes.

The finalizer must never decide comparability or create scientific facts.

## Hard boundaries

Do not:

- judge whether two records are comparable;
- assign comparability levels or transferability;
- recommend parameter directions or values;
- judge experiment feasibility or operation boundaries;
- modify Evidence Node scientific meaning;
- infer missing process facts from common PVD knowledge;
- merge all evidence from one paper into one record by default.

## Handoff

The output is the evidence portion consumed by downstream PVD research:

- `evidence_records` → downstream `evidence_records` unchanged;
- `source_provenance` → downstream `source_provenance` unchanged.

Upstream `structured_context` remains a separate authority and is not copied or rewritten by this Skill.
