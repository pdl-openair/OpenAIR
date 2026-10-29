# MP Seeds — Dry-Run 产物(Dry-Run Stub)

> 来源:`fixtures/skill_io/materials_database_search/response.json` 中 `passing_candidates` 前 3 条(storyline 统一为 CrSiTe3 系列)
> 生成方式:L2 skill `database-search` 消费预录 fixture,**未调用外部 API**

## constraint_set

| 约束 | 范围 |
|---|---|
| bandgap_eV | [0.2, 3.5] |
| formation_energy_eV | ≤ 0 |
| ehull_eV | ≤ 1.0 |
| num_elements | = 3 |
| sg_number | ≠ 1 |
| banned elements | Hg, Tl, Na, K, F +(默认 22 种放射性/稀有) |
| cppred 验证 | cl_score ≥ 0.5, f_max ≤ 2.0 eV/Å, exfoliation ∈ [-5, 350] meV |

## candidates

| mp_id | 化学式 | chemsys | 空间群 | bandgap | formation | ehull | cl_score | f_max | exfoliation | CIF |
|---|---|---|---|---|---|---|---|---|---|---|
| mp-1197575 | Ag2AsS2 | Ag-As-S | Pc (#7) | 0.538 | -0.146 | 0.003 | 0.809 | 0.170 | 28.8 | `cifs/mp-1197575_Ag2AsS2.cif` |
| mp-5671 | Cu2ZnSnS4 | Cu-Sn-S-Zn | I-42m (#121) | 1.42 | -0.082 | 0.0 | 0.921 | 0.042 | 0.0 | `cifs/mp-5671_Cu2ZnSnS4.cif` |
| **mp-676** | **CrSiTe3** | **Cr-Si-Te** | **R-3 (#148)** | **0.596** | **-0.128** | **0.012** | **0.786** | **0.464** | **2.9** | `cifs/mp-676_CrSiTe3.cif` |

> ⭐ CrSiTe3 为本 dry-run 故事线的种子选材(下游演化/制备/表征将围绕 CrSiTe3 衍生物展开)

## verified_by_cppred

✅ 全部 9 项约束经 crystal-property-prediction MCP 交叉验证(`capcall_cppred_verify_001`,~6.5min/15 候选)

## coverage_summary

- 总候选 15 条 / cppred 通过 12 条 / 全约束通过 9 条 / 独立 chemsys 9 个
- 命中率 60%
- **下游演化引导**:
  - Cr-Si-Te 化学空间值得进一步探索(CrSiTe3 同族可生成 CrGeTe3 / CrSnSe3 等)
  - Ag-As-S 与 Cu-Sn-S 体系备选
  - 若需扩展种子池,可放宽 ehull ≤ 1.5 或关闭 cppred 验证

## spec 字段自检

- [x] constraint_set 完整复述
- [x] candidates 含 mp_id + 化学式 + 空间群 + 性能值
- [x] verified_by_cppred 标注
- [x] coverage_summary 含命中率 + 演化引导
