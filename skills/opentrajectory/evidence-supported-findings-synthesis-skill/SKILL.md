---
name: evidence-supported-findings-synthesis-skill
description: Synthesize only findings already supported, limited, uncertain, or conflicted by supplied evidence and available mechanism edges. Use for the OpenTrajectory findings_synthesis substage whether mechanism explanation ran or was legally skipped; never generate research leads, hypotheses, experiments, or recommendations.
---

# Evidence-Supported Findings Synthesis

## Purpose

Turn available validated evidence and, when present, its mechanism packet into a compact set of **what the supplied evidence currently allows us to say**.

A finding is a **synthesis-level scientific conclusion**, not a renamed mechanism edge. When several related edges and evidence items jointly support one coherent conclusion, combine them into the smallest useful finding that preserves the evidence boundary. Do not emit one finding per edge merely because the upstream packet is edge-shaped.

This Skill owns one scientific responsibility only: `findings_synthesis`.

It keeps four epistemic states explicit:

- `supported`: the stated finding is directly backed by support edges within the stated scope.
- `uncertain`: there is some support, but evidence quality, completeness, or transferability prevents a firm conclusion.
- `conflicting`: the supplied evidence contains material conflict; state the conflict rather than forcing a single direction.
- `boundary_limited`: the finding is supported only inside a narrow population, material, method, or condition boundary.

## Model responsibility

The model owns scientific semantic judgment only:

- decide what synthesis-level finding the supplied evidence licenses;
- group related mechanism edges and evidence into coherent conclusions when they jointly answer the same scientific point;
- choose the appropriate epistemic status;
- explain scope boundaries and uncertainties;
- cite every mechanism edge and evidence item that materially supports the synthesized statement.

The model returns the contract in `draft.schema.json`. It does **not** create `finding_id`, `claim_type`, `summary`, `audit`, fingerprints, or final status metadata.

## Deterministic code responsibility

`scripts/runtime_guard.py` owns rules that must be enforced identically on every run:

1. validate input and LLM draft schemas strictly;
2. reject unknown or invalid mechanism/evidence references;
3. require every basis mechanism edge to contribute at least one evidence ID to `provenance_evidence_ids`;
4. allow gap evidence only as supplementary provenance, never as a replacement for basis evidence;
5. deduplicate findings;
6. enforce `generation_config.max_findings` deterministically (`0`, `1`, or `N`);
7. generate `finding_id`, `claim_type`, `summary`, `audit`, fingerprint, warnings, and degraded state;
8. validate the final object against `output.schema.json` before returning it.

Invalid model output is rejected. The code does not repair incorrect types, guess missing fields, or convert malformed values into strings.

## Inputs

`research_context` is required. The exact upstream `mechanism_packet` is optional and is supplied only when mechanism explanation ran. Its absence is accepted without fabricating mechanism content; any supplied packet must still satisfy `input.schema.json`.

`generation_config.max_findings` is optional. When omitted, the deterministic default is `12`. When set to `0`, no findings are finalized. When set to `N`, at most `N` unique valid findings are finalized.

## Required reasoning

1. Identify the scientific conclusions licensed by supplied `support` or `conflict` mechanism edges. Use `mechanism_chains` as an upstream synthesis aid, but do not treat a chain summary as permission to exceed its edge-level evidence.
2. **Synthesize before emitting.** Group edges that describe successive parts of one mechanism, convergent evidence for the same claim, or a shared bounded conclusion. Prefer one compact finding that captures the scientifically useful combined meaning over several edge-by-edge paraphrases.
3. Emit a single-edge finding only when that edge is itself a complete, useful scientific conclusion **and** no related supplied edge materially changes, strengthens, limits, or contextualizes it. Do not mirror the upstream edge count.
4. Apply an anti-transcription check before returning each finding: if its statement is essentially a one-to-one rewrite of a mechanism relation while related edges could support a more useful bounded conclusion, merge, rewrite, or omit it.
5. Include all basis edges that materially support the finding. If the statement spans a mechanism chain, its basis must span the relevant chain edges; do not claim chain-level meaning while citing only one convenient edge. Conversely, do not add unrelated edges merely to make a finding look synthesized.
6. Keep `gap` edges separate. A gap can explain uncertainty or missing knowledge, but **cannot support a scientific effect**.
7. For each emitted finding, state scope boundary and uncertainty explicitly. Synthesis may compress evidence; it may not strengthen causality, universality, or certainty beyond the supplied basis.
8. If multiple edges support genuinely independent scientific conclusions, separate findings are appropriate. The goal is **meaningful synthesis**, not mechanically maximizing the number of edges per finding.
9. If no defensible finding can be formed, return an empty draft. The deterministic layer returns `findings = []`, a warning, and `degraded = true`. Setting `max_findings = 0` suppresses final findings but does not hide an evidence-empty degraded state.

## Hard boundaries

Do not output:

- research directions or what to study next;
- hypotheses, predictions, independent/dependent variables, or falsification criteria;
- experimental designs, validation plans, parameter recommendations, or actions;
- claims that cite unknown evidence or mechanism-edge IDs;
- edge-by-edge restatements presented as synthesis when related supplied evidence supports a coherent higher-level finding.

A paper-reported result may appear here **only as an already-known finding**, never as novelty.

## Final output contract

Each finalized finding contains:

- deterministic `local_finding_key`, `finding_id`, and `claim_type`;
- model-authored `statement` and `epistemic_status`;
- traceable `provenance_evidence_ids`;
- non-gap `basis_mechanism_edge_ids`;
- optional `gap_mechanism_edge_ids` used only to express missing evidence;
- `scope_boundary`, `uncertainties`, and calibrated `confidence`.

`summary` and `audit` are deterministic runtime outputs, not model-authored placeholders.
