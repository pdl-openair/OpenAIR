# title-abstract-screening-skill replay

## Scope

P0 replay validates only contract compatibility and deterministic metadata-only behavior.

## Steps

1. Prepare input from `examples/request.json`.
2. Run adapter function `screen_title_abstract_candidates`.
3. Validate output against `output.schema.json`.
4. Confirm no network requests and no Evidence/Lead outputs.

## Expected

- Decision set is limited to `include | maybe | exclude`.
- Candidates with title+abstract and ready access are included first.
- Warnings are emitted for metadata-only / missing abstract / blocked/unknown access.
