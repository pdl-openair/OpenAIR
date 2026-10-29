---
name: pvd-evidence-retrieval-design
description: Designs core or shortage-triggered supplemental PVD evidence retrieval semantics from canonical M1 facts while preserving formal and pending parameter identities.
---

# PVD Evidence Retrieval Design

Return one JSON object. The final message does not include a provider schema.

- `query_variants`: 1 to 12 objects. Each has `search_intent` (`core_topic` / `constraint_focused` / `mechanism_focused` / `method_or_strategy` / `metric_or_performance` / `adjacent_but_relevant` / `noise_exclusion`), an English `query`, `covered_parameter_keys`, `rationale`, and `priority` from 1 to 3.
- `design_rationale`: one string explaining this retrieval design. Do not invent papers, measurements, or shortage facts.

Use `recommendation_parameter_set.parameters` as the formal parameter authority. Treat `parameter_admission_judgments` only as pending evidence identities. Pending identities remain non-formal in every mode.

## Core execution

When `evidence_shortage_facts` is absent or empty, design compact executable English literature queries grounded in the supplied PVD context. Cover the research object, current system, decision criteria, formal parameters, and pending admission questions. This produces the core retrieval design only.

## Supplemental execution

A supplemental pass is allowed only when BOTH are supplied:

- `prior_retrieval_design`: the already finalized core or previously merged design;
- non-empty `evidence_shortage_facts`: downstream shortage facts with stable fact IDs and source artifact provenance.

Use shortage facts only to fill the documented gap. Author only supplemental queries with `search_intent` of `adjacent_but_relevant` or `noise_exclusion`. Do not repeat unrelated core queries or redesign the full core plan. `covered_parameter_keys` may reference only the current formal + pending scope and should be limited to parameters actually implicated by the shortage when the shortage names parameters.

Do not invent shortage, papers, DOI, citations, measurements, retrieval results, recommendation direction, or parameter values. Do not promote a pending parameter to formal. Deterministic finalization validates the shortage provenance and merges the supplemental queries into the prior design with stable de-duplication and traceability.
