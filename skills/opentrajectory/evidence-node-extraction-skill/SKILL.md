---
name: evidence-node-extraction-skill
description: >-
  Form source-grounded ResearchRecords from confirmed progressive-reading material.
  Use after progressive reading and before mechanism explanation or findings synthesis.
---

# ResearchRecord Extraction

## Purpose

Turn confirmed source material into **source-local, auditable ResearchRecords**.

A formal record answers:

> research object → conditions → result → source

A ResearchRecord is a scientific fact-layer object. It is **not Evidence merely because it was extracted**. Evidence identity belongs downstream, when a concrete scientific judgment uses a ResearchRecord to support, refute, or limit that judgment.

This Skill does not retrieve or screen sources, synthesize mechanisms across sources, form Findings, create Research Conclusions, generate hypotheses, recommend actions, or decide Workflow state.

## Inputs

The Skill consumes only the formal material needed for this substage:

- `paper_packets` from progressive reading, including source anchors and confirmed source text;
- the current `evidence_schema` as attention context for what objects, conditions, results, comparisons, provenance, and gaps matter for this research task.

`evidence_schema` guides attention. It never authorizes content that is absent from the source. A required target may remain uncovered.

## Model responsibility

The model owns scientific semantic judgment. For each candidate record it must decide:

- the concrete `research_object`;
- each applicable condition dimension and whether its value is `reported` or `unknown`;
- whether conditions genuinely do not apply, expressed only by an empty `conditions[]`;
- the source-reported `result` and its record type;
- the information identity and assertion mode;
- the exact contiguous `source_quote` that grounds the record;
- relevant EvidenceSchema target IDs, only when the source actually contains the requested information;
- uncertainty, qualifiers, and non-critical unknown fields that remain unresolved.

If the research object, applicable condition details, result, or source identity cannot be confirmed, omit the record rather than guess.

## Formal record admission

A row may enter `research_records[]` only when all four identities are confirmable:

1. **Research object** — the model identifies the concrete material, sample, system, experiment, computation, comparison subject, or other studied object from the source context. This is a semantic description and may paraphrase the source; deterministic finalization does not require this description to occur verbatim in `source_quote`.
2. **Conditions** — emit each applicable condition dimension in `conditions[]`, using `status=reported` for a source-grounded value and `status=unknown` when the source identifies the dimension but does not report its value. Emit an empty array only when conditions do not meaningfully apply. Unknown conditions are never omitted to imply non-applicability. Do not emit aggregate `condition_status`; deterministic finalization derives it from these details.
3. **Result** — the source reports a concrete observation, measurement, computation, comparison, mechanism statement, limitation, composition, condition, or qualitative result.
4. **Source** — one supplied source anchor identifies the exact source section and locator.

`unknown_fields` is only for non-critical details that remain unresolved after the record itself is still identifiable. It must not be used to bypass a missing research object, condition identity, result identity, or source identity.

## Record types

Use the narrowest source-faithful type:

- `material_composition`
- `experimental_condition`
- `quantitative_result`
- `characterization_observation`
- `computation_result`
- `comparison`
- `mechanism_statement`
- `limitation`
- `qualitative_result`

`quantitative_result` and `computation_result` require source-reported raw values in `result.values[]`; comparison, characterization, and other records also preserve every material raw value/unit that appears in the recorded result. Do not convert units, calculate derived values, or normalize precision.

A `comparison` preserves the compared subject, baseline, dimension, direction, and source-stated conditions needed to interpret it. Do not merge records from different sources into a new comparison or synthesized judgment.

## Information identity and assertion boundary

Keep source information identity explicit:

- `direct_source_fact` — a directly reported observation, measurement, calculation result, condition, or other source fact;
- `author_interpretation` — the source authors' explanatory interpretation or inference;
- `other_source_information` — source-stated association, causal assertion, limitation, contextual statement, or other information that is not a direct observation.

Assertion modes remain separate:

- `direct_observation`
- `association`
- `causal_claim`
- `author_interpretation`
- `source_statement`

Never upgrade association to causality. A tentative author explanation stays `author_interpretation`; it does not become a direct observation or an established mechanism. A source-explicit causal statement may be recorded as `causal_claim`, but this node does not decide that the mechanism is scientifically established.

## Exact source grounding

Each record selects one supplied `source_anchor_id` and copies one contiguous `source_quote` from that anchor's source section.

Whitespace may be normalized by deterministic code. Punctuation, numbers, units, symbols, casing, hyphenation, and token order must not be changed. Do not concatenate separate spans.

The quote must contain enough local context to identify the research object and support the recorded result. Every reported result value, unit, reported condition value, named unknown condition dimension, comparison subject/baseline, and mechanism cause/effect stored in structured fields must be present in the exact quote.

If one source contains multiple distinct results, emit multiple ResearchRecords. If multiple sources contain related results, keep source-local records separate. Cross-source synthesis belongs downstream.

## EvidenceSchema use

`relevant_target_ids` may contain only supplied target IDs and may be empty.

Do not create filler records to satisfy target coverage. Missing required targets remain uncovered in deterministic summary metadata. Target relevance does not make a ResearchRecord into Evidence.

## Deterministic finalization

`scripts/finalize.py` owns only deterministic invariants:

- retain the existing required-key checks for model and final output;
- attach source identity and provenance from the supplied packet/anchor;
- derive aggregate `condition_status` from `conditions[]` (`[]` -> `not_applicable`, all `reported` -> `reported`, any `unknown` -> `partially_unknown`);
- assign stable `record_id` values;
- deduplicate identical records;
- compute target gaps, counts, status, audit metadata, and warnings.

The finalizer must not invent, reinterpret, or repair scientific semantics after model output.

## Result semantics

The finalized artifact contains exactly one formal research-record collection: `research_records[]`. There is no parallel `evidence_items[]` output.

- `succeeded`: the artifact is trustworthy; zero records is allowed when the source contains no confirmable record.
- `partial`: records exist, but required EvidenceSchema targets remain uncovered.
- `degraded`: trustworthy records exist, but the upstream source scope is weaker, such as abstract-only or raw-fallback material.
- `failed`: necessary model or output keys are absent, or execution fails; no finalized artifact is returned.

## Final model checks

Before emitting a ResearchRecord, verify:

1. Can the research object be identified from the exact quote?
2. Are reported conditions source-grounded, are source-stated unknown condition values kept unknown, and is an empty `conditions[]` used only when conditions truly do not apply?
3. Is the result source-reported without invented values, units, relations, or certainty?
4. Is source identity anchored to one supplied section?
5. Is direct fact vs author interpretation vs other source information correct?
6. Is association kept non-causal and tentative mechanism language kept tentative?
7. Is each target link genuinely supported, with no coverage filler?
8. Does the row remain a ResearchRecord rather than a downstream Evidence/Finding/Conclusion object?

If any required answer is no, omit the row.
