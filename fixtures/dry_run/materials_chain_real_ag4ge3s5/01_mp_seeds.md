# MP Seeds — Stage 1 (problem_modeling / mp_constraint_search)

> 生成时间:2026-07-03(真跑)
> 来源:`mp_candidates_real/passing_candidates.json`(mode=partial,16 passing)
> 下游:`02_novel_candidates.md`(作 mattergen evolution 种子)

## constraint_set

| 约束 | 阈值 | 来源 |
|---|---|---|
| bandgap_eV | [0.2, 3.5] | MP |
| formation_energy_eV | ≤ 0.0 | MP |
| ehull_eV | ≤ 1.0 | MP |
| cl_score | ≥ 0.50 | cppred |
| f_max_eV_A | ≤ 2.0 | cppred |
| exfoliation_energy_meV | [-5, 350] | cppred |
| num_elements | = 3 | MP |
| element_set.banned | Hg/Tl/Na/K/F | MP |
| sg_number.banned | 1 | client |

## candidates[](top strict-passing,作 stage 2 种子)

| mp_id | formula | chemsys | Eg | E_form | ehull | CL | f_max | cppred_status |
|---|---|---|---|---|---|---|---|---|
| **mp-9900** | Ag2GeS3 | Ag-Ge-S | 0.46 | -0.47 | 0.00 | **0.886** | 0.095 | ok |
| mp-554873 | Ag2B8O13 | Ag-B-O | 3.27 | -2.51 | 0.00 | 0.868 | 0.134 | ok |
| mp-560276 | Ag2Mo2O7 | Ag-Mo-O | 1.88 | -1.51 | 0.00 | 0.809 | 0.177 | ok |
| mp-4691 | Ag2CO3 | Ag-C-O | 0.47 | -1.09 | 0.00 | 0.763 | 0.123 | ok |
| mp-554988 | Ag2Ge2O5 | Ag-Ge-O | 0.75 | — | — | 0.734 | 0.256 | ok |

## verified_by_cppred

- mode: **partial**(8 ok / 9 fail)
- 9 条 degraded(大 cell penalty + 部分 timeout)
- 客户端降级:degraded 候选保留,cppred 字段为 null,Stage 2 应重验

## coverage_summary

- 覆盖化学系统:10 个
- 16 条 passing(7 strict + 9 degraded)
- mp-9900 Ag2GeS3 选为 stage 2 主种子(CL 最高,与 stage 2 演化出的 Ag4Ge3S5 同 chemsys)

## 给下游的引导

- 主推 Ag-Ge-S 子空间(mp-9900 CL=0.886,演化潜力大)
- Ag-Ge-O(mp-554988)可作备选,但 B/CO 系助熔剂选择不同
- degraded 候选(cell > ~40 atoms)在 stage 2 演化中应避免直接当种子(mattergen 也对大 cell 处理慢)
