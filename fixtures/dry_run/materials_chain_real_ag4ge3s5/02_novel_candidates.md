# Novel Candidates — Stage 2 演化产出

> 生成时间:2026-07-03
> run_id: `task_20260703_153008_37b3f76d` / session `evo_20260703_153008_08a2249a`
> 上游:`01_mp_seeds.md`(top-3 strict-passing Ag-* 种子)
> 下游:`flux_parameter_recommendation`

## objectives

| 字段 | 方向 |
|---|---|
| `ehull_eV` | minimize(稳定性优先) |
| `dielectric_epsx` | maximize(介电性能优先) |

## constraints(与 stage 1 一致)

| 约束 | 阈值 |
|---|---|
| `bandgap_eV` | [0.2, 3.5] |
| `exfoliation_energy_meV` | [-5, 350] |
| `formation_energy_eV` | ≤ 0.0 |
| `cl_score` | ≥ 0.50 |
| `f_max_eV_A` | ≤ 2.0 |
| `ehull_eV` | ≤ 1.0 |
| `num_elements` | = 3 |
| `element_set.banned` | Hg/Tl/Na/K/F |

## 演化配置

| 参数 | 值 |
|---|---|
| emo_engine | TriplePopCMOEA |
| n_pops | 8 |
| max_generations | 5 |
| n_offspring | 8 |
| noise_level | 0.3 |
| initial_pool_mode | random(hybrid bug workaround) |
| 种子来源 | mp-9900 Ag2GeS3, mp-554873 Ag2B8O13, mp-560276 Ag2Mo2O7 |
| 实际耗时 | ~3 分钟(initializing→running→completed) |

## pareto_front

Pareto 前沿 4 条(去重后 2 个唯一 crystal_id,均为 Ag4Ge3S5),全部 constraint_violation=0.0:

| crystal_id | formula | ehull_eV | bandgap_eV | epsx | E_form | cl_score | f_max | exf_meV |
|---|---|---|---|---|---|---|---|---|
| gen002_ind_001 | Ag4Ge3S5 | 0.878 | 0.711 | 12.06 | -0.114 | 0.858 | 0.431 | 137.1 |
| gen004_ind_000 | Ag4Ge3S5 | 0.933 | 0.306 | 13.02 | -0.165 | 0.852 | 0.515 | 138.5 |

CIF 路径:
- `cifs/gen002_ind_001_Ag4Ge3S5.cif`(12 sites, sg=1 P1)
- `cifs/gen004_ind_000_Ag4Ge3S5.cif`(12 sites, sg=1 P1)

### 支配关系

两候选互相非支配:
- gen002_ind_001 在 ehull / bandgap / cl_score / f_max 上更优(稳定性、带隙、合成可行性更佳)
- gen004_ind_000 在 epsx / formation_energy 上更优(介电性、形成能更深)

## intervention_log

无中途干预。本次为自动演化,未触发 pause_at / inject_evolution_knowledge。

> 注:5 代 × 8 pop 规模偏小,Pareto 前沿集中到 Ag4Ge3S5 单一化学系统。生产场景建议 max_generations≥25、n_pops≥64 以提升化学多样性。

## novelty_check

### 与 MP 对比

| 候选 | 是否命中 MP 已知 | 说明 |
|---|---|---|
| Ag4Ge3S5 | ❌ 未命中 | MP 数据库无此计量比(Ag-Ge-S 系统已知有 Ag2GeS3 / Ag8GeS6 / Ag4GeS4 等,无 Ag4Ge3S5) |
| 结构 sg=1 | 🟡 注意 | 演化输出 P1 结构,实际可能属于更高对称性但未做 spglib 精修 |

### 故事线评价

- 种子 Ag2GeS3 (mp-9900, CL=0.886) → 演化 Ag4Ge3S5 (CL=0.858) 的路径合理
- 演化保留了 Ag-Ge-S 化学系统,但调整了计量比
- cl_score 仍 ≥0.85,说明 ALIGNN/JACS 仍认为高合成可行性
- ehull ~0.9 eV 接近约束上限(≤1.0),实际合成需谨慎评估

## 给下游的建议

- 选 gen002_ind_001 作主推(综合性能更稳)
- Stage 3 应针对 Ag4Ge3S5 推荐 flux 法参数(原料/助熔剂/温度曲线)
- 若 stage 3 走 CrSiTe3 故事线,可参考 `fixtures/skill_io/materials_parameter_recommend_flux/response.json` 的 AlInSe3→CrSiTe3 改写模式
- Stage 3 应保留主推 + 备选两条方案,以备 stage 4 选择
