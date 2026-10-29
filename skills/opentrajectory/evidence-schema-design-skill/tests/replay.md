# P0.7f Replay Notes

Offline fixtures cover:

- a condition-sensitive lithium-metal-battery electrolyte-design task;
- a computer-science visual-retrieval task used as a domain-leakage guard;
- a sparse-input task used to verify minimal-schema fallback;
- zero / one / many `evidence_targets[]` array stability.

The materials fixture explicitly checks decision-bearing coverage for:

- formulation composition;
- cell configuration and cycling protocol;
- reversible capacity, cycle life and coulombic efficiency;
- solvation and interphase mechanism evidence;
- characterization observations;
- explanatory calculations such as binding energy or electrostatic potential;
- baseline and comparability requirements.

The computer-science fixture verifies dynamic adaptation to datasets, splits, evaluation protocols, task metrics, baselines / ablations and limitations.

This P0.7f slice does not implement `GapMap`, acquisition actions, budget control or certification policies.


## Content hardening v2

- Every explicit decision-bearing constraint and sample-scan signal must be represented or disclosed as a warning.
- Formulation or configuration design tasks with candidate components must emit a dedicated `composition` target.
- Comparison-sensitive tasks must emit a dedicated `baseline` or `comparability` target.
- Mechanism-oriented tasks that ask why a candidate works must emit a dedicated `mechanism` target.
- Live smoke writes `*.raw.json` evidence before content assertions, so failed runs remain reviewable.
