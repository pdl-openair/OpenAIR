# Generated Output Eval Drop Zone

This directory contains reviewed recorded-fixture outputs for the 4-Agent
scenario eval.

Each JSON file must use this shape:

```json
{
  "scenarioName": "evidence_messy_results_captions",
  "artifact": {}
}
```

`tests/test_research_agent_scenario_artifacts_v2.py` will load every `*.json`
file in this directory through
`app.modules.research_agent_artifact_eval.evaluate_generated_output_dir`.

The runner reports:

- `no_outputs` when this directory has no JSON outputs
- `passed` when every checked-in output passes artifact validation
- `failed` when any output has missing traceability, fail-closed violations, or
  execution/result overclaim

It also rejects generated outputs that copy the reference artifact verbatim.

Current contents:

- 12 JSON outputs, one for each 4-Agent scenario.
- Each output includes `provenance.liveAgentRun: false`.
- Each output includes a human `review` block with v2 rubric score, cap, and
  recommendation.

Interpretation:

- These files are stronger than pure reference contracts because they are
  independently reviewed artifacts and must pass the generated-output validator.
- They are still not live Agent outputs. They must not be counted as empirical
  90+ evidence or used to promote an Agent to Featured by themselves.
