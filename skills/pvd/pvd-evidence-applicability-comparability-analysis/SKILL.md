---
name: pvd-evidence-applicability-comparability-analysis
description: >-
  Determine what external or cross-system PVD evidence can support for the current experiment, under which conditions, and with what transfer limits.
---

# PVD Evidence Applicability & Comparability Analysis

Return only `output.schema.json`.

## Responsibility
Judge how supplied external or cross-system evidence may be used for the current PVD decision.

## Formal inputs
- `pvd_project_context` is the frozen upstream PVD project fact package. Treat it as read-only authority; do not reinterpret the user request into a new project context. Its artifact lineage is carried by the WorkUnit source references.
- `research_records`, `evidence_packet`, `source_provenance`, `coverage`, `unresolved_gaps`, and `conditional_evidence_linkage` are the closed T04 evidence handoff. Do not retrieve or assemble replacement literature material.
- `support_target` is the frozen T03 EvidenceSchema. Preserve its target IDs, parameter identities, pending status, and explicit unknowns.

## Rules
- Compare material/system identity, process conditions, geometry, measurement definition and outcome context; missing information stays missing.
- A matching metric label does not make numeric results directly comparable when measurement method, geometry or definition differs.
- Keep evidence-relation facts such as matched or relative comparison separate from downstream-use authorization.
- `allowed_uses` is the explicit downstream-use authorization and has exactly four positive classes: `mechanism`, `trend_direction`, `magnitude_reference`, and `direct_numeric`. Keep at most the strongest decision-specific class; do not infer permission from a coarse score or from a comparison relation.
- Every positive authorization must name the verified record(s) used for that support target; the deterministic adapter resolves those record/target findings to exact T04 Evidence identities and materializes formal provenance.
- Transfer limits, conflicts, and important unknowns that bound a target conclusion must stay attached to that conclusion as well as any appropriate overall summary.
- Correlation is not causation. Preserve confounding, simultaneous parameter changes, contradictions and uncertainty.
- Evidence supporting a direction does not automatically support a concrete value.
- Do not generate candidate settings or recommendations.
- With no usable material, return `insufficient` or `not_needed`; never invent evidence.
