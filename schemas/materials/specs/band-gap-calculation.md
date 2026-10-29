# Spec — Band Gap Calculation（DFT 带隙计算）

<!-- Substage: band_gap_calculation
     Stage: execution_validation
     L2 skill: band-gap-calculation
     L3 skills: cif-to-vasp, vasp-band-gap, jcc -->

## 目标

对已确认的正式候选，用 VASP 对候选的电子结构进行更高成本的计算，得到带隙以及在实际完成时的 VBM、CBM、直接/间接性质或能带图。

## Input

- `candidate`（候选编号 + CIF 路径）
- `structure_version`（DFT 输入使用的结构版本说明；弛豫前后结构区分）

## 产出物（output_file: `band_gap_summary.md`）

| 字段 | 必填 | 说明 |
|---|---|---|
| candidate_refs | ✅ | 计算候选编号、化学式、使用的 CIF/POSCAR 路径与版本 |
| calculation_setup | ✅ | 计算路径（完整能带 / 静态快速路径）、关键参数（ENCUT、KPOINTS 等）、提交的任务名与 HPC 记录 |
| band_gap_ev | ✅ | 带隙数值（eV）+ 计算类型（静态/非自洽能带）；未收敛或失败时明确标注状态而非留数值 |
| vbm_cbm | ⭑ | VBM/CBM 位置与直接/间接判定（仅完整能带路径完成时） |
| convergence_status | ✅ | 收敛性检查结论；HPC 任务完成 ≠ 结果有效，须检查输出文件 |
| dft_output_paths | ✅ | 明细输出位置（`dft/<candidate_id>/band_gap/`，含 `final/band_gap.json`） |

## 验收标准

- [ ] 结果与候选编号一一对应；DFT 输入结构版本与 ML 预测、候选文件版本区分清楚
- [ ] 只获得静态带隙时写明「静态计算得到的带隙」，不声称完成完整能带图分析
- [ ] 未收敛/失败的任务如实记录状态与原因，不用旧值或推测值填充
- [ ] 带隙数值带单位，计算方法（静态/能带）注明
- [ ] HPC 断点恢复时按原任务名查询后从第一个未完成步骤继续，未重复提交已完成任务

## 与上下游的衔接

- **上游**：`candidate_generation`（正式候选）、`stability_verification`（可并行的 DFT 验证）
- **下游**：`report_generation`（带隙结果纳入报告与三方对比）
