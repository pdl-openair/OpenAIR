# Flux Parameters — CrSiTe3 Dry-Run 产物(Dry-Run Stub)

> 来源:基于 `fixtures/skill_io/materials_parameter_recommend_flux/response.json` 格式改写,目标材料 AlInSe3 → **CrSiTe3**
> 生成方式:L2 skill `parameter-recommendation` 消费上游 `02_novel_candidates.md` + 参考用 fixture,**未调用相似度检索 / 统计窗口 / Claude 推理脚本**

## target_material

| 字段 | 值 |
|---|---|
| 化学式 | **CrSiTe3** |
| 结构原型 | 层状三元硫族化合物(CrSi3 八面体 + Si-Si 共面, R-3 #148) |
| 是否二维 | ✅ 是(易解理) |
| 是否半导体 | ✅ 是(本征 bandgap ~0.6 eV,可通过应力调控到 1.0-1.5 eV) |
| 材料族系 | 三元硫族化合物 / Cr-IV-VI 族磁性半导体 |
| 上游 CIF | `fixtures/dry_run/materials_chain/cifs/CrSiTe3_mp-676.cif` |
| 上游 mp_id | mp-676 |

## 推荐方案列表

### 方案 A — baseline(推荐)

**一句话**:KCl 中稀释助熔剂 + 缓慢降温,获取层状 CrSiTe3 单晶

#### 工艺参数

| 项 | 值 |
|---|---|
| 原料信息 | Cr 粉(99.9%)、Si 粉(99.999%)、Te 粉(99.999%)按化学计量比 |
| 原料标准化 | Cr:Si:Te = 1:1:3 |
| 助熔剂信息 | KCl(预脱水,200 ℃ 真空 24 h) |
| 助熔剂标准化 | KCl:CrSiTe3 = 8:1(摩尔比) |
| 容器 | 石英坩埚,真空封管(1e-3 Pa) |
| 籽晶 | 无(自发成核) |

**温度程序**:

| 阶段 | 温度(℃) | 时长(h) | 升温/降温速率 |
|---|---|---|---|
| 升温至次高温 | 25 → 500 | 10 | 47.5 ℃/h |
| 次高温预反应 | 500 | 12 | — |
| 升温至最高温 | 500 → 950 | 20 | 22.5 ℃/h |
| 最高温保温 | 950 | 24 | — |
| 主降温 | 950 → 650 | 100 | 3 ℃/h |
| 低温恒温 | 650 | 24 | — |
| 炉冷至室温 | 650 → 25 | ~25 | 炉冷 |

**分离与后处理**:
- 分离方式:去离子水(80 ℃)溶解 KCl + 倾析
- 后处理:去离子水冲洗 3 次、乙醇脱水、真空干燥

#### 预期结果

- 预期晶体尺寸:mm 级层状单晶(易解理)
- 预期风险水平:**低**
- 风险来源:Te 蒸汽压偏高(>700 ℃ 显著),真空封管需检漏

#### 溯源信息

- 主要参考配方:rec_crsite3_lit01(Carte et al. 2017)、rec_crsite3_lit02(Casto et al. 2015)
- 参考材料:CrSiTe3(mp-676)同族 — CrGeTe3、MnPS3、FePSe3
- 方案类型:scheme_002-中稀释缓慢降温

---

### 方案 B — variant_high_dilution(大单晶追求)

**一句话**:高稀释 KCl(15:1)+ 极慢降温(1.5 ℃/h),追求 cm 级单晶

| 关键差异 | 值 |
|---|---|
| 助熔剂标准化 | KCl:CrSiTe3 = 15:1 |
| 最高温 | 1000 ℃ / 36 h(突破 Te 偏析) |
| 主降温 | 1000 → 700 ℃,1.5 ℃/h,200 h |
| 预期晶体尺寸 | 大尺寸 mm-cm 级单晶 |
| 预期风险水平 | **中**(总时长接近 240 h 上限;高稀释可能引入 KCl 包裹体) |

---

### 方案 C — variant_KI_flux(低温快冷备选)

**一句话**:KI 助熔剂(熔点更低 ~681 ℃),避免 KCl 残留腐蚀,快降温得粉末样品

| 关键差异 | 值 |
|---|---|
| 助熔剂 | KI(真空脱水) |
| 助熔剂标准化 | KI:CrSiTe3 = 6:1 |
| 最高温 | 850 ℃ / 18 h |
| 主降温 | 850 → 550 ℃,8 ℃/h,50 h |
| 预期晶体尺寸 | mm 级多晶/小单晶 |
| 预期风险水平 | **低**(KI 吸潮需严格脱水;Tmax 偏低可能导致 Te 反应不完全) |

## crucible_atmosphere

- 坩埚:石英(方案 A/B)、石英或刚玉(方案 C)
- 保护气氛:无(真空封管)
- ⚠ 实验室约束(需确认):石英坩埚 Tmax 上限 ≤ 1100 ℃(方案 B 1000 ℃ 安全)

## expected_phase

| 项 | 值 |
|---|---|
| 预期主相 | **CrSiTe3**(R-3 #148) |
| 可能副产物 | Cr2Te3(Fd-3m)、SiTe2(P-3m1)、未反应 Te |
| 副产物处理 | 通过水洗 + 超声分离(KCl + Te 残留可溶);必要时梯度密度液分离 |

> **下游 synthesis_execution 关键字段**:expected_phase 主相 `CrSiTe3` 将继承到 `synthesis_log.product_formula`,`CrSiTe3_mp-676.cif` 继承到 `product_cif_path`

## risk_notes

- 风险:Te 蒸汽压高,封管破裂风险存在 → 建议预反应段 ≤ 500 ℃ 充分化合后再升温
- 替代方案:方案 B(高稀释)、方案 C(KI 助熔剂)

## spec 字段自检

- [x] target_material(化学式 + CIF 路径)
- [x] flux_recipe(配比 + 物理化学依据 — 同族硫族化合物常用 KCl)
- [x] temperature_profile(升温速率、峰值温度、降温速率齐全)
- [x] crucible_atmosphere
- [x] expected_phase(主相 + 副产物)
- [x] risk_notes(含 ≥1 替代方案,本文件含 2 个)
