# Evaluation record

## Before

- Skill size: 116 lines, 528 words, 4,115 UTF-8 bytes.
- No 10-case multi-scenario quality eval existed in the supplied target.
- `examples/request.json` failed the current input schema with 2 errors.
- `examples/response.draft.json` failed the current formal output schema with 51 errors; the draft is pre-runtime, but it also used obsolete field names.
- `examples/response.json` failed the current formal output schema with 42 errors.
- The bundled P0 test could not run in this handoff package because the runtime guard file is outside the supplied package.
- No live model run was possible because `LLM_PROVIDER_API_KEY` and `LLM_PROVIDER_BASE_URL` are unavailable in the execution environment.

Highest-value root cause: the Skill stated important prohibitions but did not give a sufficiently explicit positive synthesis procedure from ResearchRecord semantics to direct answer, evidence state, research lead, and hypothesis. This was most exposed by author-interpretation, comparability, conflict, and insufficient-evidence cases.

## After

- Skill size: 85 lines, 799 words, 5,834 UTF-8 bytes (+271 words / +1,719 bytes).
- Added the fixed 10-case representative eval set plus 2 post-change boundary cases.
- All 12 eval inputs validate against the unchanged input schema.
- Example request validates against the unchanged input schema.
- Formal example response validates against the unchanged output schema and uses only supplied ResearchRecord / mechanism-edge references.
- Offline P0 validation passes; runtime-dependent assertions are explicitly skipped when the runtime guard is absent from the handoff package.
- Live smoke was attempted but cannot execute without the provider credentials.

The additional Skill text is limited to scientific decisions that materially change user-visible correctness: evidence identity, comparability, direct-answer behavior, conflict/unknown treatment, layer separation, falsifiability, and the mechanism boundary. Runtime-owned structure and historical workflow details were removed rather than expanded.

## 97+ status

Not established in this environment. Deterministic contract and eval-fixture evidence is positive, but the required same-scenario live model quality evidence is unavailable. A 97+ claim must wait for the core 10 cases (and preferably the 2 boundary cases) to run through the real structured-output path under comparable Before/After model conditions.
