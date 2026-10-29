# Flux Parameters — Stage 3 推荐方案

> 生成时间:2026-07-04
> 目标材料:Ag4Ge3S5 (来自 stage 2 `02_novel_candidates.md` Pareto 前沿 gen002_ind_001)
> 上游 CIF:`cifs/gen002_ind_001_Ag4Ge3S5.cif` (12 sites, sg=1 P1)
> 下游:`flux_synthesis_execution`(stage 4)
> 推荐引擎:`materials-parameter-recommend-flux` v2.0(相似检索 30 条 + 统计窗口 + Claude 推理)

## target_material

| 字段 | 值 |
|---|---|
| 化学式 | Ag4Ge3S5 |
| 结构原型 | novel (mattergen 演化产物, sg=P1) |
| chemsys | Ag-Ge-S |
| 是否二维 | 是 |
| 是否半导体 | 是(Eg=0.711 eV) |
| 来源 | stage 2 Pareto gen002_ind_001(cl_score=0.858) |
| CIF | `cifs/gen002_ind_001_Ag4Ge3S5.cif` |

## 主推方案:方案 A — K2S3 Reactive Flux

### flux_recipe

| 字段 | 值 |
|---|---|
| 原料(标准化) | `Ag : Ge : S = 4 : 3 : 5`(化学计量比) |
| 助熔剂 | K2S3(预制:K + S 在 200℃ 预反应 12h) |
| 助熔剂:溶质 摩尔比 | K2S3 : Ag4Ge3S5 = **5 : 1** |
| 配比依据 | (1) K2S3 是 KBiSiS4 / KSnPS4 等硫族化合物合成中标准的 reactive flux,能控制 S 化学位势;(2) 摩尔比 5:1 接近知识库统计窗口 P90=8.37 的中段(可调);(3) K2S3 熔点 ~310℃,提供良好液相介质 |

### temperature_profile

| 阶段 | 温度 | 时长 | 备注 |
|---|---|---|---|
| 升温 → 次高温(预反应) | RT → 400℃ | 10h(~40℃/h) | **次高温 ≤ S 沸点(444℃)−5℃ 安全余量**,防止 S 挥发 |
| 次高温保温 | 400℃ | 24h | 让 K-S 体系形成多硫化物熔体,Ag+Ge 逐步溶解 |
| 升温 → 最高温 | 400 → 850℃ | 8h | 形成 Ag-Ge-S-K-S 均匀液相 |
| 最高温保温 | 850℃ | 48h | 完全熔化,化学均匀化 |
| 主降温 | 850 → 400℃ | 150h @ **3℃/h** | 慢冷生长单晶 |
| 低温保温 | 400℃ | 24h | 释放内应力 |
| 炉冷 → RT | 400 → RT | ~40h @ 10℃/h | 自然炉冷 |

总实验时长:**~280h**(略超 200h 软提示,但单段均 < 200h)

### crucible_atmosphere

- **坩埚**:熔融石英安瓿(真空密封,~10⁻⁴ Torr)
- **气氛**:真空密封,无需额外保护气
- **必要**:密封防止 S 在 850℃ 大量挥发(S 蒸气压在 850℃ 约 30 atm,石英管需厚壁)

### expected_phase

| 相 | 来源 | 处理 |
|---|---|---|
| **Ag4Ge3S5(主相)** | 目标产物 | 单晶片状,0.5-2 mm |
| Ag2S(副相风险) | Ag 多余或 S 不足 | K2S3 reactive flux 控制 S 化学位势可抑制 |
| GeS2(副相风险) | Ge 多余 | 水洗可部分除去 |
| K2S5/K2S3(残渣) | 助熔剂 | DMF/乙醇洗涤溶出 |

### risk_notes

- **风险 1**:S 在 850℃ 蒸气压高 → 厚壁石英管 + 缓慢升温(尤其 400-500℃ 区间)
- **风险 2**:K2S3 吸湿 → 预制后立即使用,或手套箱操作
- **风险 3**:Ag2S 副相 → 严格控制 Ag:Ge:S 化学计量,助熔剂稍偏 S-rich

**替代方案**(详见下表):若方案 A 失败,优先转 **方案 B(CsCl/KCl 共晶)** —— 化学惰性更好,副相更少,只是 CsCl 成本高。

## 备选方案速览

| 方案 ID | 助熔剂 | Tmax | 降温速率 | 主优点 | 主缺点 |
|---|---|---|---|---|---|
| **A (baseline)** | K2S3 (5:1) | 850℃ | 3℃/h | reactive flux 控硫势 | K2S3 需预制 |
| **B (规模化)** | CsCl/KCl 共晶 (8:1) | 850℃ | 1℃/h | 化学惰性,大单晶 | 200h 长,CsCl 贵 |
| **C (self-flux)** | 过量 S (8:1) | 950℃ | 2℃/h | 无碱金属污染 | S 蒸气压极高,CS2 洗剧毒 |
| **D (低温)** | SnCl2 (8:1) | 600℃ | 5℃/h | 避开高温 S 挥发 | Cl 掺杂风险,微晶 |
| **E (Bi-flux)** | Bi 金属 (5:1) | 700℃ | 2℃/h | 温度温和,无卤素 | Bi 微量进入晶格风险 |

## 给 Stage 4 的可消费字段

下游 `flux_synthesis_execution` 可消费以下字段:
- `target_material.化学式` → product_formula = "Ag4Ge3S5"
- `target_material.CIF` → product_cif_path
- `方案_A.工艺参数.温度程序` → 直接构造合成 timeline
- `方案_A.工艺参数.分离与后处理` → 后处理步骤
- `方案_A.expected_phase.主相` → 验证目标 phase 是否生成
- 风险评估 → 用于 synthesis_log 的 observations

## 5 步 Pipeline 元数据

| Step | 工具 | 输出文件 | 行数/计数 |
|---|---|---|---|
| 1 | Claude | `data/similar_mates/intention_parsed.jsonl` | Ag4Ge3S5 intent |
| 2 | Python `similar_retrieval.py` | `data/similar_mates/similar_output_Ag4Ge3S5.jsonl` | 30 相似材料 |
| 3 | Python `statistic_window.py` | `data/recommand_window/window_output_Ag4Ge3S5.jsonl` | 24 参数窗口 |
| 4 | Claude | `data/recommand_recipe/feature_output_Ag4Ge3S5.jsonl` | 5 配方特征 + 5 典型方案 |
| 5 | Claude | `data/recommand_recipe/recommend_output_Ag4Ge3S5.jsonl` | 5 推荐方案(baseline + 4 variant) |

## 验收对照

| spec 字段 | 提供位置 |
|---|---|
| target_material | ✅ 上方表格 |
| flux_recipe | ✅ 方案 A 章节配比 + 依据 |
| temperature_profile | ✅ 7 阶段时间温度表 |
| crucible_atmosphere | ✅ 真空密封石英安瓿 |
| expected_phase | ✅ 主相 + 3 类副相 |
| risk_notes | ✅ 3 项风险 + 替代方案 B |
