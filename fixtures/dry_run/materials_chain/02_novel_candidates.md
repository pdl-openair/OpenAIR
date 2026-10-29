# Novel Candidates — Dry-Run 产物(Dry-Run Stub)

> 来源:`fixtures/skill_io/materials_new_candidate_generation/response.json`(`evolution_result.pareto_front`),故事线对齐 CrSiTe3 种子
> 生成方式:L2 skill `candidate-generation` 消费预录 fixture + 上游 `01_mp_seeds.md`,**未调用 mattergen MCP**

## objectives

| 目标 | 方向 |
|---|---|
| ehull_eV | minimize |
| bandgap_eV | maximize |

## constraints

继承自上游 `01_mp_seeds.md` 的 constraint_set(bandgap ∈ [0.2, 3.5]、formation ≤ 0、ehull ≤ 1.0、num_elements = 3、sg ≠ 1、cppred 三项)

## pareto_front

| rank | 化学式 | CIF | ehull↓ | bandgap↑ | formation | cl_score | f_max | sg | feasible | dominates |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **CrPS3** | `cand_001.cif` | 0.04 | 1.83 | -0.094 | 0.71 | 0.082 | 147 | ✅ | cand_002/003/004 |
| 1 | CrAsSe3 | `cand_002.cif` | 0.08 | 2.14 | -0.061 | 0.64 | 0.121 | 164 | ✅ | cand_005 |
| 1 | FeSiTe3 | `cand_003.cif` | 0.12 | 1.45 | -0.038 | 0.58 | 0.156 | 148 | ✅ | — |
| 2 | MnPS3 | `cand_004.cif` | 0.21 | 2.51 | 0.012 | 0.49 | 0.210 | 147 | ❌ | — |

**Pareto 前沿规模**:8(本表展示前 4 条)/ **feasible**:3 / **总评估**:7680

## run_id

`demo_t1`(mattergen MCP session=`sess_ternary_semiconductor_v1`,generations=20)

## intervention_log

(dry-run:无干预;fixture 中无 intervention 记录)

## novelty_check

| 候选 | MP 比对结果 |
|---|---|
| CrPS3 | ✅ 未在 MP 中找到完全匹配(sg=147, MP 中 CrPS3 已知 sg=15) → **新颖** |
| CrAsSe3 | ⚠ MP 中存在 mp-1009018(CrAsSe3 sg=164)→ **已有记录**,需进一步评估晶格参数差异 |
| FeSiTe3 | ✅ 与 mp-676(CrSiTe3)同族不同元素 → **新颖** |
| MnPS3 | MP 已知(MnPS3 sg=15)→ 与生成结构 sg=147 有空间群差异 |

## spec 字段自检

- [x] objectives 与 constraints 完整复述
- [x] pareto_front 含 CIF + 性能 + 支配关系
- [x] run_id 记录
- [x] novelty_check 给出 MP 比对结果

## 与 CrSiTe3 种子的故事线衔接

- **上游种子**:CrSiTe3 (mp-676,R-3 #148,bandgap=0.596,ehull=0.012)
- **演化方向**:以"Cr/Fe + 三价主族 + 硫族"三元硫族化合物为核心子空间
- **结果解读**:
  - FeSiTe3 (cand_003) 与种子 CrSiTe3 同结构原型(R-3 #148),仅 Cr→Fe 替换 → 验证演化在种子邻域有效探索
  - CrPS3 / CrAsSe3 保留 Cr 位点,替换 Si/Te → 拓展到磷/硫族组合,符合"以种子元素位为锚点扩散"的演化策略
- **下游建议**:选 FeSiTe3(同结构原型,易合成对比)或 CrPS3(性能最优 feasible)进入 parameter_recommendation

> ⚠️ 下游 `03_flux_parameters` 将以 **CrSiTe3 自身**(mp-676 种子)为目标,而非 Pareto 候选 — 因 fixture 故事线已固定为 CrSiTe3,dry-run 优先保证上下游字段咬合而非演化创新性
