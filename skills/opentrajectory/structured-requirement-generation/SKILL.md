---
name: structured-requirement-generation
description: >-
  Convert a natural-language research need into a compact StructuredRequirement object
  for downstream retrieval, constraint interpretation, evidence analysis, and research lead generation.
  Use this skill when a research question or literature-analysis request must enter the OpenTrajectory pipeline.
---

# Structured Requirement Generation

Convert the user's research query into one StructuredRequirement JSON object.

This skill is a lightweight front-door parser. It must preserve enough facets for downstream literature retrieval, but it must not answer the research question.

## Do NOT use when

- The input is already a valid StructuredRequirement JSON object.
- The user only wants direct chat explanation and no pipeline processing.
- The user explicitly asks to skip requirement structuring.

## Output fields

Return exactly these fields and no others:

| Field | Meaning | If unclear |
|---|---|---|
| `original_user_query` | Original user query, allowing whitespace cleanup only | — |
| `research_object` | Core research object or problem in one concise sentence | Use the broadest faithful description |
| `target_metrics` | Evaluation metrics, optimization goals, or observed outcomes useful for retrieval | `[]` |
| `boundary_conditions` | Scenario limits, experimental conditions, material/system scope, or constraints | `[]` |
| `comparison_baseline` | Baseline, control, comparator, or default option | `null` |
| `output_intent` | One of `comparison`, `summary`, `decision_support`, `literature_review`, `hypothesis_generation` | `null` |
| `requested_source_scope` | Source/time/language/type preference explicitly requested by the user | `null` |
| `ambiguities` | Critical missing details that may affect retrieval or downstream judgment | `[]` |
| `requirement_confidence` | Completeness confidence from `0` to `1` | — |

## Writing rules

- Be recall-oriented but concise: keep enough terms for retrieval, avoid explanatory paragraphs.
- Use short noun phrases or short clauses. Do not write long mechanism explanations.
- Do not invent values, materials, mechanisms, source scopes, thresholds, or baselines not implied by the user.
- Keep related but distinct retrieval facets; remove near-duplicates.
- Prefer domain-neutral wording that works across materials science, chemistry, biology, devices, AI-for-science, and engineering research.
- `target_metrics`, `boundary_conditions`, and `ambiguities` may contain multiple items when useful for retrieval, but each item must be compact.
- Put missing concrete values in `ambiguities`, not in `boundary_conditions`.
- `requested_source_scope` is only for explicit source preferences such as time range, paper type, language, reproducibility, citation quality, or review/original article scope.

### Output budget guidance

- Keep `research_object` as one concise sentence, normally `<= 240` characters.
- Keep each `target_metrics[]` item as a compact metric phrase, normally `<= 160` characters.
- Keep each `boundary_conditions[]` and `ambiguities[]` item as one compact clause, normally `<= 200` characters.
- Keep `requested_source_scope` concise, normally `<= 240` characters.
- The schema hard limits intentionally keep engineering headroom for scientific terminology. Do not use the extra budget for report-style explanations.

### Fidelity-first guardrails (must follow)

- `target_metrics` and `boundary_conditions` may include **only** information explicitly stated by the user.
- Domain common sense, plausible assumptions, or likely experimental setups must **not** be rewritten as confirmed user requirements.
- Any model-inferred missing detail must go to `ambiguities`, not to `target_metrics` or `boundary_conditions`.
- Do not default-fill any of the following unless explicitly present in user text:
  - temperature ranges (e.g., room temperature to battery operating range);
  - cathode choices (e.g., NMC);
  - safety / cost / energy density weighting;
  - numeric thresholds or test protocols.
- Potentially relevant exploration directions that are not explicitly requested should not be emitted in Phase1 output; leave them to downstream low-priority exploration.
## Intent selection

- Use `comparison` when the user asks to compare objects, methods, baselines, materials, or conditions.
- Use `decision_support` when the user asks for selection, ranking, recommendation, trade-off judgment, or project choice.
- Use `literature_review` when the user mainly asks to find, summarize, or review literature.
- Use `hypothesis_generation` when the user asks how to design, improve, explain possible routes, or generate research ideas.
- Use `summary` when the user asks to condense or structure an existing research topic without needing comparison or hypothesis generation.
- Use `null` only when intent is genuinely unclear.

## Confidence rules

- `>= 0.8`: object, intent, main metrics, and core boundaries are mostly clear.
- `0.6–0.79`: object and intent are clear, but key experimental/source/baseline details are missing.
- `< 0.6`: research object, metrics, or target scenario are too vague; `ambiguities` must be non-empty.
