# Historical P0.1 Patch Notes — deprecated

This note describes an earlier compatibility shape. The current production contract no longer generates `downstream_filter_rules`; legacy direct input is accepted only so the registered finalizer can ignore the entire field without reading its value.

## Scope

Small compatibility patch after inspecting real Skill 1 outputs.

## Change

`input.schema.json` now accepts `downstream_filter_rules[]` items as either:

- string
- object

This was required by the historical runtime output. It is not current Retrieval Authority.

## Non-goals

- No change to search intent logic.
- No change to output schema.
- No database-specific query generation.
- No retrieval execution.
- No P1 search phase / dedup key design.

## Validation

Current compatibility tests verify that a legacy field can cross the direct-input boundary while its content cannot affect finalized behavior.
