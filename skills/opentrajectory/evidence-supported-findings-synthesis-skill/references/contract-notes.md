# Contract Notes

- Formal node: `findings_synthesis`.
- `mechanism_packet` is optional because the frozen handoff contract permits `mechanism_explanation` to be skipped; absence never creates synthetic mechanism content.
- Claim layer: evidence-supported finding only. A finding is a synthesis-level conclusion, not a one-to-one rename of a mechanism edge.
- The model owns scientific semantic judgment; deterministic code owns schema, provenance invariants, IDs, deduplication, limits, summary/audit, and degraded state.
- Every `basis_mechanism_edge_id` must contribute at least one of its own `evidence_ids` to the finding's `provenance_evidence_ids`.
- `gap` evidence may supplement provenance but may never substitute for evidence from a basis edge.
- `max_findings` is a real deterministic limit; default is 12.
- Related edges should be combined when they jointly support one coherent scientific conclusion. Single-edge findings remain valid only when the edge is already a complete useful conclusion and related edges do not materially change its meaning.
- Chain-level statements must cite every relevant basis edge; unrelated edges must not be added merely to simulate synthesis.
- Output is intentionally consumable by `research-lead-generation-skill`; it does not generate that downstream layer itself.
