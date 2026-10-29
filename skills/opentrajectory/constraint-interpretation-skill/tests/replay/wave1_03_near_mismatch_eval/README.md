# Wave1-03 Near-Mismatch Eval

Fixed before the Wave1-03 prompt change. It supplements, rather than replaces, the frozen historical `p0_1_realistic_cn_eval` (12 cases) and `p0_effect_eval` (9 cases).

The eight cases target the task-required near-mismatch risks: same phrase across domains, numeric value with unclear direction, conflicting constraints, explicit unknown vs explicit unrestricted, equivalent units, non-material research, and soft source preferences.

Production-provider outputs are intentionally not fabricated. If the target model/provider is unavailable, keep this set as the next comparable After replay and do not claim 97+ from prompt inspection alone.
