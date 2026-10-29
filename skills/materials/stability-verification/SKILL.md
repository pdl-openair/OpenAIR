---
name: stability-verification
description: 对候选化合物执行 DFT 热力学稳定性验证：生成竞争相计算输入并提交 HPC 计算，构建凸包相图，给出 ehull 与形成能，判定候选是否值得进入下游制备。
---

# Stability Verification（L2 编排）

## 职责

对 `candidate_generation` 产出的新候选（或 `database_search` 拉回的种子）做 DFT 级热力学稳定性验证：查询化学体系内全部竞争相、生成统一参数的 VASP 输入、在 HPC 跑弛豫+静态、提取能量构建凸包相图,给出 ehull 与形成能,按 spec 产出 `stability_report.md`。

本 L2 是**域无关**编排：第一性原理计算后端（VASP / 其他）、HPC 调度器、相图构建库由 L3 实现。L2 只负责：上游候选消费、计算流程编排、稳定性判据格式化、不达标候选的回退建议。

## 判据

- ehull ≤ 0.05 eV/atom 且形成能 ≤ 0 eV/atom → 通过,进入下游 `parameter_recommendation`
- 否则 → 标记不稳定/亚稳,给出回退建议（换候选/调约束/重生成）

## 执行步骤

1. 读取上游 `novel_candidates.md`,取待验证候选清单（化学式 + CIF）
2. 与用户确认验证范围（全候选 / Top-K / 指定子集）与 HPC 资源
3. 调用域特定 L3（materials pack 调用 `vasp-thermodynamic-stability`）跑竞争相查询 + VASP 计算 + 凸包分析
4. 按 spec（`schemas/materials/specs/stability-verification.md`）产出 `stability_report.md`
5. 不达标候选写入 recommended_actions（回退至 `candidate_generation` 或换种子）

## 产出

见 spec。output_file = `stability_report.md`（materials pack 约定）。
