---
name: falsifiable-hypothesis-generation-skill
description: Convert accepted research leads into explicit unvalidated, falsifiable scientific hypotheses with variables, expected relations, boundaries, mechanism reasoning, predictions, falsification conditions, evidence boundaries, uncertainty, and confidence. Use only for the OpenTrajectory hypothesis_update substage.
---

# Falsifiable Hypothesis Generation

## Responsibility

Convert accepted upstream research leads into **unvalidated falsifiable scientific hypotheses**.

This Skill owns only `hypothesis_update`.

A hypothesis produced here is a proposition to test. It is not an evidence-supported finding, experiment plan, parameter recommendation, final conclusion, workflow decision, or round-management instruction.

## Input contract

`input.schema.json` is authoritative.

Required:

- `task_id`;
- `research_context`;
- the accepted upstream `findings_packet`;
- the accepted upstream `research_lead_packet`.

The Skill consumes finalized research leads. Each upstream lead must already carry its deterministic `research_lead_id`, `claim_type`, provenance, accepted semantic-boundary judgment, and research-lead novelty boundary.

Optional:

- the accepted upstream `mechanism_packet`, supplied only when mechanism explanation ran. Its absence is accepted without fabricating mechanism content; any supplied packet must satisfy `input.schema.json`.
- `generation_config`. If omitted, `max_hypotheses` defaults to `8`.
- If `generation_config` is present, `max_hypotheses` is required and must be from `0` through `8`.
- `focus_questions` may focus model reasoning but never relax provenance or contract rules.

`max_hypotheses` is enforced by deterministic finalization:

- `0` → zero finalized hypotheses;
- `1` → at most one unique validated hypothesis;
- `N` → at most `N` unique validated hypotheses.

## Model responsibility

The model owns all scientific-semantic work. For each proposed hypothesis it must:

1. interpret one or more accepted research leads;
2. define an independent variable or comparison and a primary dependent variable or observation;
3. state the expected scientific relation;
4. state the boundary conditions under which the proposition is intended to hold;
5. explain the mechanism reasoning that motivates testing it;
6. provide a positive falsifiable prediction and a concrete falsification condition;
7. explain what the existing evidence does and does not already establish;
8. judge the evidence context as `supported`, `uncertain`, `conflicting`, `boundary_limited`, or `gap_limited`;
9. explain uncertainties and give calibrated confidence;
10. explicitly set `semantic_hypothesis_judgment`.

Only `valid_unvalidated_falsifiable_hypothesis` is eligible for final success. The other enum values identify semantic failure modes such as restating an existing finding, presenting an already-established claim, failing falsifiability or boundary requirements, leaving the research-lead basis, producing experiment design or parameter recommendations, or crossing into final conclusions/workflow management.

The model must not generate system identity, audit, final status, or deterministic provenance metadata.

## Deterministic code responsibility

`scripts/runtime_guard.py` does only rule-based validation and finalization:

1. validate input schema;
2. validate `draft.schema.json` strictly;
3. accept/reject the model-declared semantic judgment without re-reading scientific prose heuristically;
4. verify referenced research-lead, finding, mechanism-edge, and evidence IDs exist and are contained in the selected upstream leads;
5. verify cited evidence is grounded by the cited findings/mechanism edges;
6. canonicalize set-like references;
7. create `hypothesis_id`, `claim_type`, novelty boundary, provenance-basis metadata, summary, audit, and input fingerprint;
8. deduplicate by deterministic hypothesis identity;
9. enforce `max_hypotheses`;
10. validate the final object against `output.schema.json`.

The code must not use keywords, regex, lexical similarity, Jaccard scores, embedding similarity, or other heuristics to decide scientific meaning.

## Draft versus final output

The LLM emits only `draft.schema.json`.

The draft must not contain:

- `local_hypothesis_key`;
- `hypothesis_id`;
- `claim_type`;
- `novelty_boundary`;
- `provenance_basis`;
- `summary`;
- `audit`;
- `input_fingerprint`;
- final success/degraded state.

Those are deterministic runtime responsibilities.

Wrong JSON types, unknown fields, missing required fields, invalid references, or a rejected semantic judgment cause `ContractRejected`. They are not coerced, repaired, or silently converted into success.

## Scientific boundaries

Do not:

- restate an existing finding as if it were a new hypothesis;
- claim that evidence motivating the hypothesis already validates the hypothesis;
- turn the hypothesis into an experiment protocol, procedural plan, parameter recommendation, final research conclusion, or decision;
- invent upstream IDs;
- hide conflicts, uncertainty, boundary limitations, or evidence gaps.

A gap may motivate a hypothesis, but a gap is not supporting evidence for the predicted effect.

## Empty, degraded, and failed behavior

- `max_hypotheses = 0`: intentional successful empty output; `summary.degraded = false`.
- No accepted research leads with a positive limit: empty degraded output with a warning.
- Accepted research leads but no valid hypothesis in the model draft: empty degraded output with a warning.
- Invalid input, invalid model draft, invalid provenance, rejected semantic judgment, or invalid final schema: fail with `ContractRejected`; no successful result is fabricated.

## Deterministic identity

`scripts/identity.py` owns:

- `hypothesis_id`, based on canonical scientific identity fields and sorted set-like references;
- `input_fingerprint`, based on the full semantic input that can affect hypothesis generation, excluding invocation-only `task_id`.

Reordering an unordered reference/boundary list does not create a second hypothesis.
