---
name: mechanism-chain-synthesis-skill
description: >-
  Synthesize auditable mechanism edges and chains from grounded ResearchRecords.
  Use after evidence-node-extraction-skill and before findings, research-lead, or hypothesis generation.
---

# Mechanism Chain Synthesis

Convert finalized `research_records[]` into compact, auditable `mechanism_edges[]` and `mechanism_chains[]`.

This is a **mechanism organization** step. It must not generate hypotheses, recommendations, research leads or proposed experiments.

## Hard traceability rule

Every mechanism edge must cite one or more existing `record_id` values from the input `research_records[]`. For the existing downstream output contract, place those exact `record_id` values in `evidence_ids[]`. The runtime rejects unknown IDs. Conflict edges require at least two distinct ResearchRecord IDs. Gap edges must be tied to supplied ResearchRecords and use `reveals_gap`.

## Allowed edge statuses

- `support`: one or more ResearchRecords support a mechanism relation.
- `conflict`: at least two ResearchRecords make incompatible claims or observations.
- `gap`: grounded ResearchRecords expose a missing link, missing validation condition or unresolved causal step.

## Allowed predicates

Use only: `promotes`, `suppresses`, `increases`, `decreases`, `enables`, `limits`, `associated_with`, `contradicts`, `reveals_gap`.

## Node types

Use only: `composition`, `condition`, `structure`, `interphase`, `property`, `performance`, `observation`, `calculation`, `limitation`, `unknown`.

## Precision rules

1. Use `support` only when the cited ResearchRecords justify the relation.
2. Use `conflict` only when two or more cited ResearchRecords are genuinely incompatible.
3. Use `gap` only when cited ResearchRecords expose a missing mechanism link or missing validation condition. Do not invent generic future-work gaps.
4. Preserve a compact causal sequence. Prefer a few decision-relevant edges over exhaustive summaries.
5. `rationale` must state why the cited ResearchRecord IDs justify the edge.
6. Chains must reference local edge keys produced in the same response.
7. Do not emit hypotheses, recommendations, candidate formulations or experimental plans.
8. **Self-loop edges are invalid**: Every mechanism edge must satisfy `source_node != target_node`. Compare normalized `node_type + label` (or `node_id` if available in future payloads). Do not emit edges like `electrolyte -> electrolyte`, `method -> method`, `benchmark -> benchmark`. If a relation would create a self-loop, omit it or convert it to a gap/diagnostic warning if the ResearchRecord support is grounded.

## Output requirements

Return exactly:

- `mechanism_edges`: grounded edges. Set `edge_id` to an empty string; runtime injects the canonical ID.
- `mechanism_chains`: compact chains. Set `chain_id` and `edge_ids` to placeholders; runtime injects canonical values.
- `conflicts`: `[]` placeholder; runtime derives it from validated edges.
- `gaps`: `[]` placeholder; runtime derives it from validated edges.
- `summary`: `{}` placeholder; runtime fills canonical counts.
- `audit`: `{}` placeholder; runtime fills traceability metadata.
- `synthesis_warnings`: short strings only when useful.
