# Spec — Thermodynamic Stability Verification（VASP 热力学稳定性验证）

<!-- Substage: stability_verification
     Stage: execution_validation
     L2 skill: stability-verification
     L3 skills: vasp-thermodynamic-stability -->

## 用户价值

用可追溯计算与竞争相证据判断候选稳定性，避免把缺失计算当成稳定。

## Input
- `material_candidates`
- `stability_protocol`

## Output
- `stability_verification`

## Acceptance
- `candidate_and_competing_phase_identity_traceable`
- `compute_run_and_energy_inputs_traceable`
- `stability_threshold_source_explicit`
- `formation_energy_ehull_and_verdict_consistent`
- `partial_or_blocked_compute_not_treated_as_stable`
- `recommended_action_matches_verdict_and_evidence`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 可选输入

`verification_scope` 为可选输入；缺失时保持缺口，不通过猜测补值。

## 失败与不足

计算失败、成本不足或竞争相不完整时输出 partial / blocked；不得把缺失计算解释为稳定。

## 来源与重入边界

候选→竞争相→计算 run→energy→phase diagram→verdict 保持全链 lineage；候选、协议或计算结果变化时重入。

## 目标

对 `candidate_generation` 产出的新候选（或 `database_search` 拉回的种子）跑 DFT 级热力学稳定性验证：查询化学体系内全部竞争相、生成统一参数的 VASP 输入、在 HPC 跑弛豫+静态、提取能量构建凸包相图,给出 ehull 与形成能,判定候选是否值得进入下游 `parameter_recommendation`。

## 判据

- ehull = 0 → 热力学稳定（在凸包上）
- ehull ≤ 0.05 eV/atom 且 formation_energy ≤ 0 → metastable,可进入下游
- ehull ≤ 0.1 → 中度亚稳,需评估
- ehull > 0.1 → 不稳定,回退

## 与上下游的衔接

- **上游**：`candidate_generation`（新候选）/ `database_search`（已有种子）
- **下游**：`parameter_recommendation`（达标的候选进入制备参数推荐）
