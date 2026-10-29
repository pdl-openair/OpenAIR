# PVD Capability 6 Integration Handoff

## Frozen responsibility
Capability 6 now accepts the formal parameter directions as its primary scientific input and concrete candidates only as an optional validation input. It never owns direction selection or candidate-value generation.

## Cap5 → Cap6
Cap5 must hand off:
- `current_context.recommendation_parameter_set.parameters[]` as the formal dynamic scope;
- `direction_analysis.directions[]`, exactly one identified direction per formal `parameter_key`;
- optional `candidate_analysis.candidates[]` only when concrete candidates already exist upstream.

Cap6 requires exact direction coverage of the formal scope. It does not promote pending parameters or invent missing directions.

## Cap6 → Cap7
Cap6 emits:
- `availability`: `available | unavailable`;
- `validation_status`: `complete | degraded | partial`;
- `recommendation_gate`: `allow | allow_with_conditions | block`;
- immutable joined direction assessments;
- optional immutable joined candidate assessments;
- cross-parameter assessment, risks, unknowns and workflow disposition.

Cap7 must treat:
- `allow` as admitted;
- `allow_with_conditions` as admitted only with the stated conditions/unknowns carried forward;
- `block` as a scientific stop requiring upstream revision/reanalysis;
- `availability=unavailable` as a technical-unavailability/degraded result, **not** as scientific infeasibility.

## Runtime fallback wiring still required
This WIP does not modify shared runtime or Workflow wiring. The Skill module exposes:

`build_unavailable_result(input_payload, failure_kind)`

The eventual shared execution integration should call that helper for timeout/provider/runtime-unavailable outcomes after a valid Cap6 input has been materialized. Do not translate those failures to `block` or `infeasible`.

## No ownership transfer
- Cap5 owns direction semantics.
- Upstream candidate generation owns concrete values.
- Cap6 only admits/validates and deterministically passes upstream objects through unchanged.
- Cap7 owns the final recommendation/portfolio.
