---
name: pvd-recommendation-mode-resolution
description: >-
  Deterministically resolves initial versus next-round recommendation mode from canonical PVD ScientificState without inferring round identity.
---

# PVD Recommendation Mode Resolution

Resolve the existing Node9 execution `mode` from canonical PVD ScientificState.

- `initial`: no prior canonical `system_recommendation` exists in `experiment_history`.
- `next_round`: at least one prior canonical `system_recommendation` exists.

Historical experiments without a prior OpenAIR system recommendation remain valid baseline evidence but do not turn a new project into a next-round recommendation. The Skill never creates or infers `round_ref`.
