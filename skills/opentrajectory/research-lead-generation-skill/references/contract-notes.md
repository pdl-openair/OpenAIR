# Contract notes

`mechanism_packet` is optional because the frozen handoff contract permits `mechanism_explanation` to be skipped; findings remain the required upstream basis.

`research_lead_generation` converts upstream evidence state into investigable questions only.

The model draft and final Skill output are separate contracts:

```text
input.schema.json
→ LLM draft.schema.json
→ strict draft validation
→ model-declared semantic-boundary check + deterministic provenance validation
→ deterministic identity/finalization
→ output.schema.json
```

No stage repairs invalid model values. Duplicate upstream `finding_id` or `edge_id` values are rejected rather than silently overwritten. Contract violations raise `ContractRejected`.

`max_research_leads` is a final-output cap after validation and deduplication. A configured value of `0` intentionally returns no leads and is not degraded. With a positive limit, an empty result is degraded rather than filled with invented content.

A gap is a reason to investigate, not evidence that an effect exists. The model is the authority for deciding whether a proposed lead has crossed into a hypothesis, prediction, expected direction, recommendation, experiment plan, or causal conclusion. It records that decision in `semantic_boundary_judgment`. Code validates the enum and rejects violation judgments; it never infers semantics from the lead text.

## Integration note

The current OpenAIR registration shape represented by this baseline does not define a formal YAML field for a Skill-local finalizer hook. This Skill therefore does not invent `finalizer`, `runtime_guard`, adapter, or compatibility metadata. The Skill-local contract is complete; wiring `scripts/runtime_guard.py` into the canonical shared runtime remains a total-control integration responsibility (`SHARED_RUNTIME_INTEGRATION_REQUIRED`).
