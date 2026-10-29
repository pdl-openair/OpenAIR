---
name: pvd-evidence-applicability-global-planning
description: Select the smallest scientifically necessary deep-verification scope from the frozen T05 inputs.
---

# PVD Evidence Applicability Global Planning

Return one JSON object. The final message does not include an output schema. Do not decide `allowed_uses`.

- `planning_status`: `planned` or `insufficient`. Use `insufficient` when the frozen inputs cannot support a deep-verification scope.
- `selected_records`: at most 10 objects. Each copies a supplied `record_id`, lists one or more frozen T03 `support_target_ids`, a `review_focus` string array, and `relation_questions` objects with `factor`, `response`, and `question`. Both arrays may be empty. Do not invent record or target ids.
- `global_review_focus`: strings. Empty when no focus can be justified.
- `uncertainty`: strings. Empty when no material uncertainty has to be preserved.

## Responsibility
Plan **what must be deeply verified** before any evidence-use authorization is possible. Select supplied research records, frozen T03 support-target IDs, and factor-response relation questions that materially affect the current PVD judgment.

## Authority boundary
- Use only the frozen `pvd_project_context`, T04 research records/provenance/coverage/gaps, and T03 `support_target` supplied in the input.
- Do not retrieve new literature, reinterpret the original user request into a new project context, or generate experiment settings.
- Preserve unknowns. Missing conditions are reasons to request deep verification or retain uncertainty, not permission to guess.

## Planning rules
- Prefer records that can materially change the next scientific judgment, experiment design, control strategy, falsification path, or search region.
- Cover every source: when the supplied research records span more than one source, select at least one record per source (within the 10-record bound) unless every record from that source lacks facts relevant to the support targets. Record any such source-level exclusion in `uncertainty` with its reason. "Smallest scientifically necessary" is bounded below by source-level coverage, not by convenience; selecting a single record when multiple informative sources exist is under-selection.
- Identify the exact support targets and factor-response relations that need record-level verification.
- Do not decide `allowed_uses`, final transferability, or final scientific authorization.
- Do not exclude a record merely because it differs from the current system; differences may still matter for mechanism, trend, or transfer-boundary reasoning.
- Keep the deep scope bounded. The runtime may execute at most 10 selected records in this T05 chain, and it deterministically backfills uncovered sources when the plan leaves them out, marking backfilled rows in `review_focus`.
