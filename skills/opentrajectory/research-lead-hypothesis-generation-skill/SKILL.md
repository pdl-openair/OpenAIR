---
name: research-lead-hypothesis-generation-skill
description: Produce the final OpenTrajectory evidence synthesis from supplied ResearchRecords, with optional mechanism context. Use it to answer the current research question, separate supported conclusions from conflicts, unknowns, research leads, and unvalidated hypotheses, and return a bounded answer when evidence is incomplete.
---

# OpenTrajectory Final Evidence Synthesis

## Purpose

Produce one final scientific synthesis from `research_records`.

Answer the most explicit research question or decision intent in `research_context`. If no explicit question is present, answer only the objective implied by `research_object`, `target_metrics`, `boundary_conditions`, and `output_intent`. Do not expand scope.

Treat ResearchRecords as the evidence authority. Use `mechanism_packet` only as optional explanatory context.

## Synthesis procedure

### 1. Determine what each record can support

Read the record's result, conditions, `information_identity`, `assertion_mode`, qualifiers, uncertainty, and unknown fields before using it.

Keep each claim within the record's scientific and experimental boundary.

- Direct observations, quantitative results, and explicit comparisons may support conclusions within their reported conditions.
- An `author_interpretation` supports only an attributed interpretation; it does not establish the interpreted mechanism as fact.
- A reported causal or mechanism statement must not be strengthened beyond the source wording or conditions.
- Uncertainty, limitations, unknown fields, and missing evidence never support an effect direction.
- Topical relevance alone is not support for a proposition.

Compare quantitative results only when material conditions are sufficiently aligned and reported. When a material condition differs or is unknown, preserve the individual results but do not turn them into a matched effect, ranking, or transferable quantitative conclusion. Do not invent statistical significance, variance, effect size, or missing conditions.

### 2. Write `direct_answer` first

Answer the original question directly with the strongest statement the supplied records justify.

- For consistent evidence, state the supported result and its boundary.
- For conflicting evidence, state that the evidence conflicts; do not choose a winner without supplied grounds.
- For indirect or insufficient evidence, state what is not established, then state the nearest relevant supported fact.
- For author-interpreted mechanisms, say the mechanism is proposed or interpreted, not established.
- Keep caveats to conditions, conflicts, uncertainty, or gaps that materially change the answer.
- Cite only ResearchRecord IDs that genuinely support statements in the answer. An empty supporting list is valid when the requested effect is not established.

Never present a research lead or hypothesis as part of the established answer.

### 3. Separate the current evidence state

Use:

- `evidence_supported_conclusions` for propositions actually supported by supplied ResearchRecords;
- `conflicts` for materially incompatible records about the same claim under sufficiently comparable conditions;
- `uncertainties` for unresolved uncertainty that changes interpretation;
- `evidence_gaps` for missing evidence needed to answer the research question more strongly.

For every conclusion, cite only records that support that exact proposition. Preserve scope and uncertainty with `epistemic_status`; do not hide a real conflict by averaging or merging opposing records.

Use `epistemic_status: conflicting` only when the conclusion statement itself expresses an unresolved conflict and the cited records together establish that conflict. Do not label a single-record observation `conflicting` merely because another record disagrees with it. If an individual observation is worth retaining, give it the status warranted by that record and represent the cross-record disagreement in `conflicts` and relevant uncertainty. Prefer one clear user-facing conflict synthesis over duplicating opposing observations when separate rows do not improve the user's decision.

### 4. Derive research leads only after the evidence state is clear

Generate only high-value unanswered research questions that follow from supplied evidence, conflicts, or gaps.

A research lead is a direction to investigate, not a prediction, recommendation, novelty claim, or established effect. Phrase `research_question` as a genuine question and explain why resolving it would materially improve the current answer.

Return no lead when the records do not support a useful next scientific question.

### 5. Derive hypotheses only when a defensible inference exists

Keep every hypothesis explicitly unvalidated. Define the independent variable, dependent variable, expected direction, boundary conditions, validation conditions, and falsification conditions so that a negative result could genuinely refute it.

Use supporting records as the basis for proposing the hypothesis, never as proof that it is true. Do not restate an already reported result as a hypothesis. Return no hypothesis when a meaningful falsifiable inference cannot be formed.

## Mechanism boundary

When `mechanism_packet` is present, use only supplied edge IDs. Use support edges as explanatory context, preserve conflict edges as conflicts, and treat gap edges only as gaps. Mechanism context never substitutes for ResearchRecord support for a conclusion.

When `mechanism_packet` is absent, do not invent mechanism edges, causal chains, or mechanism certainty. Keep mechanism reasoning null or non-mechanistic when no mechanism evidence exists.

## Grounding

- Never cite a `record_id` absent from `research_records` or an edge absent from `mechanism_packet`.
- Never create a source, experiment, measurement, result, observation, condition, DOI, or URL absent from the input.
- Never use external knowledge to strengthen the supplied evidence.
- Never convert a gap into evidence, a lead into a conclusion, or a hypothesis into a validated result.

Return one JSON object matching the provided structured output contract. If runtime-owned identity or audit fields are exposed, use only permitted placeholders rather than inventing canonical IDs or audit facts.

Prefer a concise answer and use the structured layers for detail instead of repeating the same claim across fields.
