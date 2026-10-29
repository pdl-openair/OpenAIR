---
name: research-lead-generation-skill
description: Generate traceable research directions from existing findings, mechanism chains, conflicts, and evidence gaps without turning them into hypotheses, predictions, experiment plans, or recommendations. Use for the OpenTrajectory research_lead_generation substage after findings_synthesis.
---

# Research Lead Generation

## Responsibility

Generate **research leads**: unresolved questions or directions worth investigating next, grounded in existing findings, mechanism evidence, conflicts, or explicit gaps.

This Skill owns only `research_lead_generation`.

A research lead is not a finding, hypothesis, prediction, experiment plan, or final recommendation.

## Input contract

`input.schema.json` is authoritative.

Required:

- `task_id`: non-empty invocation identifier. It is copied to `audit.task_id` and included in `input_fingerprint`; it never changes `research_lead_id`.
- `research_context`.
- `findings_packet`.

Optional:

- `mechanism_packet`, supplied only when mechanism explanation ran. Its absence is accepted without fabricating mechanism content; any supplied packet must satisfy `input.schema.json`.
- `generation_config`. If omitted, `max_research_leads` is deterministically `8`.
- If `generation_config` is present, `max_research_leads` is required and must be an integer from `0` through `8`.
- `focus_questions` may guide the model but cannot relax provenance or claim-layer rules.

`max_research_leads` is executed by finalization:

- `0` → final output contains zero leads;
- `1` → at most one unique validated lead;
- `N` → at most `N` unique validated leads.

## LLM draft contract

The LLM must emit `draft.schema.json`, not the final output schema.

The draft contains only scientific-semantic content:

- lead kind, title, research question, investigation target, rationale;
- finding / mechanism / gap / evidence references;
- boundary conditions, scientific significance, uncertainties, confidence;
- `semantic_boundary_judgment`, where the model explicitly classifies the proposed content as either `within_research_lead_boundary` or a forbidden downstream claim type;
- generation warnings.

The LLM must not generate `research_lead_id`, `local_lead_key`, `claim_type`, `novelty_boundary`, `summary`, `audit`, or `input_fingerprint`.

Wrong JSON types, unknown fields, missing required fields, malformed references, and duplicate upstream `finding_id` / `edge_id` values are rejected. They are never coerced, overwritten, or repaired.

## Scientific boundaries

The **model** owns this semantic judgment. For every proposed lead it must set `semantic_boundary_judgment` to one of:

- `within_research_lead_boundary`;
- `hypothesis`;
- `prediction`;
- `expected_direction`;
- `recommendation`;
- `experiment_plan`;
- `causal_conclusion`.

Only `within_research_lead_boundary` may enter a successful final output. The deterministic runtime checks the declared enum and rejects violation judgments; it does **not** scan the scientific text with keywords, regex, or other semantic heuristics.

The model must classify content as a violation when it has become an expected outcome/direction, hypothesis, prediction, experiment plan, parameterized instruction, recommendation, or causal conclusion. Gap edges may motivate a lead, but they do not support an established scientific effect.

## Finalization pipeline

`scripts/runtime_guard.py` executes one straight contract path:

1. validate the input schema;
2. validate the LLM draft schema strictly;
3. validate the model-declared semantic boundary judgment and deterministic provenance;
4. deterministically create identity and metadata;
5. deduplicate, apply `max_research_leads`, and build summary/audit;
6. validate the final object against `output.schema.json`.

`scripts/identity.py` owns only deterministic `research_lead_id` and `input_fingerprint` generation.

## Empty, degraded, and failed behavior

- `max_research_leads = 0`: successful empty output; `summary.degraded = false`.
- No usable finding/mechanism/gap basis with a positive limit: empty degraded output; `summary.degraded = true`.
- A valid draft may also produce no leads; with a positive limit this is degraded, not fabricated into success content.
- Invalid input, invalid draft, invalid provenance, or claim-layer leakage raises `ContractRejected`; no successful final output is returned.

## Deterministic fields

The runtime, never the LLM, creates:

- `local_lead_key`;
- `research_lead_id`;
- `claim_type`;
- `novelty_boundary`;
- `summary`;
- `audit.task_id`;
- `audit.input_fingerprint`.
