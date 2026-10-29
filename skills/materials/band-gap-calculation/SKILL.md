---
name: band-gap-calculation
description: 把候选晶体结构（CIF）转为 VASP 计算输入并提交 DFT 带隙计算，回收结果给出带隙数值与直接/间接带隙性质判定，产出带隙计算报告。
---

# Band Gap Calculation（L2 编排）

## 职责

对已确认的正式候选，使用 VASP 对候选的电子结构进行更高成本的计算，得到带隙以及在实际完成时的 VBM、CBM、直接/间接性质或能带图。按规范产出带隙计算报告（output_file: `band_gap_summary.md`）。

对应材料发现四阶段流程的 3.1「准备 DFT 输入结构」和 3.2「计算带隙和能带相关结果」。

## 执行步骤

1. 读取上游候选 CIF（`design/candidates/` 或任务工作区中的候选文件），确认候选编号与结构版本
2. 调用 `cif-to-vasp` L3 把候选 CIF 转成 VASP 的 POSCAR 等文件，检查晶胞、原子坐标、元素顺序和输入版本；需要预先弛豫时可调用 `pymatgen`
3. 调用 `vasp-band-gap` L3（内部经 `jcc` 提交 HPC）：组织结构弛豫、静态计算和后续能带步骤；只需要带隙时可用已验证的静态快速路径
4. 管理 HPC 任务：记录任务名、对应候选、计算阶段、提交时间、状态、结果位置；断点恢复时先按原任务名查询状态再从第一个未完成步骤继续
5. 按 spec（`schemas/materials/specs/band-gap-calculation.md`）产出带隙计算报告

## 编排说明

- L3 选择：输入转换 `cif-to-vasp`；计算执行 `vasp-band-gap`；HPC 提交 `jcc`
- DFT 输入使用的结构版本必须与 ML 预测和候选文件区分清楚；弛豫前后结构不能混写
- 传给 L3 的上下文：候选编号与 CIF 路径、输出目录（`dft/<candidate_id>/band_gap/`）、是否需要完整能带图

## 产出

见 spec。output_file = `band_gap_summary.md`（materials pack 约定）。DFT 明细输出在 `dft/<candidate_id>/band_gap/` 下（输入、任务结果和 `final/band_gap.json`）。

## 验证要点

- HPC 任务完成 ≠ VASP 结果有效：已检查收敛、输出文件和性质解析
- 若只获得静态带隙，明确写「静态计算得到的带隙」，不声称已完成完整能带图分析（当前 HPC 环境非自洽能带步骤可能失败）
- 结果与候选编号一一对应
