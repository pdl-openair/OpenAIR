# OpenTrajectory Gap-to-Action Product Module

## Capability

This is an importable **product module** for converting explicit research-lead gaps into targeted next scientific actions. It creates planning annotations only. It does not retrieve evidence, execute validation, close gaps, or change scientific status.

Stable public Python API:

- `build_research_lead_package(...)` — forwards to the sibling production `skills.opentrajectory.research_lead_package.builder.build_research_lead_package`.
- `plan_gap_to_actions(suite, ...)` — plans only the canonical gaps actually present in product input.
- `run_gap_to_action_pipeline(research_lead_package, ...)` — accepts the real `research_lead_package_v0.1` product object directly and returns targeted actions plus explicit upstream mapping/unsupported records.
- `run_gap_to_action_adapter({"research_lead_package": ...})` — deterministic adapter function only; Track B does not register it in any execution runtime.

The legacy `run_planner(...)` API uses the same planner assembly and existing required action keys.

## Import graph

```text
skills.opentrajectory.research_lead_package.builder
                 ^
                 | public builder forwarding
                 |
skills.opentrajectory.gap_to_action.__init__
                 |
                 +--> planner.py
                 |
                 +--> pipeline.py
                          ^
                          |
                      adapter.py  (deterministic function only)

Track F authentic normalizer ---- explicit optional injection ----> pipeline.py
```

No Workbench, `execute_skill`, `python_adapter_runner`, or substage-map wiring is claimed in this Track.

## Real ResearchLeadPackage composition contract

The normal product path is the sibling `research_lead_package_v0.1` contract. Track B reads `proposal.gap_records[*].type` only through explicit mappings; it does not infer canonical gap types from message text, free text, or field-name keywords.

| Upstream `proposal.gap_records[*].type` | Track B result | Basis |
|---|---|---|
| `missing_evidence_refs` | `retrieval_gap` | The upstream record explicitly means traceable evidence references are missing; canonical `retrieval_gap` covers a needed source/artifact missing from the evidence trail. |
| `missing_reproducibility_protocol` | `protocol_gap` | The upstream record explicitly states reproducibility protocol metadata is incomplete. |
| `missing_measured_results` | unsupported / non-actionable | Absence of measured results does not by itself establish an `independent_validation_gap`. |
| `source_mapping_low_confidence` | unsupported / non-actionable | Mapping confidence alone does not establish a stable-source-lock failure. |
| `unmapped_source_fields` | unsupported / non-actionable | Track B cannot infer the scientific meaning of unmapped fields. |
| `offline_proposal_traceability_limited` | unsupported / non-actionable | This is an evaluation-mode boundary, not one of the eight action gaps. |
| any of the eight canonical Track B gap types | canonical passthrough | The upstream object has already supplied an explicit Track B taxonomy value. |
| any other type | unsupported | No silent fallback or default taxonomy mapping. |

A package with at least one mappable gap is planned even if other upstream gaps are unsupported. Unsupported rows remain visible in `unsupported_upstream_gaps` and produce no action. A package containing only unsupported gaps returns no planned actions and preserves the unsupported rows.

## Canonical action vocabulary

- `retrieval_gap`
- `source_lock_gap`
- `protocol_gap`
- `threshold_gap`
- `baseline_control_gap`
- `decision_rule_gap`
- `independent_validation_gap`
- `domain_evidence_contract_gap`

Every planned action preserves `post_action_status == current_status`. If the real upstream gap record has no status field, Track B preserves that absence as `None`; it does not invent `PARTIAL`, `NOT_FOUND`, `VALIDATED`, or any other scientific state.

## Evidence / status / provenance honesty

For a real product package, the treatment copy may add only top-level planning annotations:

- `gap_to_action_planning`
- `gap_to_action_targeted_actions`

For the legacy decision-oriented compatibility shape, the historical per-lead `gap_to_action_targeted_actions` annotation remains supported.

After removing those annotations, the treatment package must be deeply equal to the input package. Track B never fills evidence references, provenance, source identity, source mapping, result status, validation status, reproduced status, decision readiness, or research-lead content.

Existing trace fields are propagated when present:

- `source_gap_pointer`
- `source_gap_id`
- `source_signal`
- `source_value`
- `source_slot_id`
- `trace_id`
- `source_pointer`
- `evidence_pointer`

If these fields are absent upstream, they remain absent. The `gap_record_index` contained in `upstream_gap_mapping` is planning-only structural metadata and is not an evidence/provenance pointer.

## Hard fails / non-claims

Existing necessary key and assembly requirements remain:

- the real package fails its sibling `validate_research_lead_package` contract;
- `proposal.gap_records` is not a list;
- an explicit gap type is unknown;
- an authentic Track-F package is supplied without its explicit normalizer hook.

A successful run means only that explicit gaps were converted into auditable next-action plans. It does **not** mean validation was executed, a result was reproduced, a scientific effect was established, or a decision is ready.

## Track F hook

`run_gap_to_action_pipeline(..., normalizer=<object>)` still supports the special `authentic_research_lead_package` contract by explicit dependency injection. The ordinary `research_lead_package_v0.1` path does not require Track F.

## Registry visibility

This directory intentionally does not contain `SKILL.md` or `skill.yaml` in Track B. It is a Python product module, not yet a scanner-visible executable Skill. Track C may add execution-facing metadata only when the real workflow/runtime wiring exists.
