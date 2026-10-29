# PVD M8 / Capability 8 integration handoff — R2

This migration intentionally does **not** modify shared Binding, Workflow, Registry, enabled state, other Capabilities,
or public runtime. Final serial integration only needs to project already-formal upstream products into this Skill input.

| Cap8 input | Source authority | Integration requirement |
|---|---|---|
| `experiment_ref` | feedback admission / caller-selected experiment identity | Required. Preserve exact identity; never replace with latest history entry. |
| `scientific_state_facts` | canonical PVD `ScientificState.scientificFacts` | Required. Pass the canonical fact slice containing experiment history. |
| `evidence_comparability` | current Cap4 `pvd_evidence_applicability_comparability_analysis` Artifact | Optional. Pass the Artifact **unchanged in shape**, including `support_conclusions[].allowed_uses`; no mapper or schema translation. |
| `factor_analysis` | Cap5 factor/direction Artifact | Optional. Pass finalized Artifact as prior scientific context; Cap8 must not recompute it. |
| `recommendation` | Cap7 recommendation Artifact | Optional. Pass finalized Artifact as prior decision context; never promote it to actual execution. |
| `current_project_context` | current PVD project context | Optional stable context only. |

## Authorization rule
Cap8 consumes Cap4's current enum exactly: `mechanism`, `trend_direction`, `magnitude_reference`, `direct_numeric`.
It does not accept `relative_comparison` and does not infer authorization from `support_level` or any other field.

Per 407b Node8 scientific scope, positive diagnosis basis is limited to `mechanism` and `magnitude_reference`.
`trend_direction` is not a Cap8 diagnosis use. Because 407b gives no explicit Cap8 rule for the newer
`direct_numeric` class, it remains fail-closed as positive diagnosis basis rather than being expanded by inference.
Empty `allowed_uses` grants no positive use.

## Runtime closure
The Skill-local helpers are deterministic contract assets:
- `scripts.prepare_input.prepare_model_input` resolves the requested experiment, validates the current Cap4 authorization enum,
  and preserves the real Cap4 Artifact shape for model context.
- `scripts.finalize.validate_final_result` guards experiment identity, basis-ref closure, Cap4 positive-use authorization,
  and the no-next-round-recommendation boundary.

During final serial integration, use the repository's existing supported Skill execution/finalization mechanism. Do not add
a shared runtime layer or intermediate comparability mapper solely for this Capability.

## Integration acceptance
1. Binding supplies the six fields above from formal Artifacts/facts, not prose guesses.
2. The requested `experiment_ref` resolves exactly once in canonical `experiment_history`.
3. Cap4 is projected as its real `support_conclusions[]` Artifact, with no shadow `evidence_analyses/support_assessments` structure.
4. Only Cap4 refs with an existing Cap8-permitted `allowed_uses` can become positive scientific basis.
5. Cap8 output validates against `output.schema.json` and finalization basis-ref checks.
6. Cap8 never writes ScientificState or produces a next-round concrete recommendation.
