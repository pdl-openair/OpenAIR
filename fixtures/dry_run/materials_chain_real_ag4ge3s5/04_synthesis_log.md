# Synthesis Log — Ag4Ge3S5 Flux 合成执行(Stub)

> ⚠️ **STUB**:本文件按 `schemas/materials/specs/flux-synthesis-execution.md` 字段手写,
> L3 atomic skill `materials-flux-synthesis-execution` 占位未落地。所有数值模拟自 stage 3 方案 A baseline,
> 产物 CIF 来自 stage 2 Pareto 真实候选。本 stub 给 stage 4 `xrd-characterization` 提供 spec 字段契约。

## 元信息

| 字段 | 值 |
|---|---|
| batch_id | `batch_ag4ge3s5_stub_001` |
| sample_id | `batch_ag4ge3s5_stub_001_s1` |
| 上游 flux_parameters | `./03_flux_parameters.md` (方案 A baseline) |
| 上游 novel_candidates | `./02_novel_candidates.md` (gen002_ind_001) |
| 实验日期 | 2026-07-07(模拟) |
| L2 skill | `flux-synthesis-execution`(占位) |
| L3 skill | ⚠️ 待落地 |

## executed_recipe

按上游方案 A baseline 执行(K2S3 reactive flux),**无偏差**:

| 项 | 值 |
|---|---|
| 原料 | Ag 粉 99.99% / Ge 粉 99.999% / S 粉 99.998% |
| 原料标准化 | Ag : Ge : S = 4 : 3 : 5 (化学计量比) |
| 助熔剂 | K2S3(预制:K + S 200℃ × 12h) |
| 助熔剂:溶质 摩尔比 | K2S3 : Ag4Ge3S5 = 5 : 1 |
| 容器 | 熔融石英安瓿(厚壁,真空密封 ~10⁻⁴ Torr) |
| Tmax | 850℃ × 48h |
| 次高温预反应 | 400℃ × 24h(S 沸点 444℃ 以下,5℃ 安全余量) |
| 主降温 | 850℃ → 400℃ @ 3℃/h(150h) |
| 低温保温 | 400℃ × 24h |
| 炉冷 | 400℃ → RT @ ~10℃/h |

## timeline

| 时间点 | 事件 | 目标温度(℃) | 模拟实测(℃) | 备注 |
|---|---|---|---|---|
| T+0h | 投料封管 | 25 | 25 | 真空 8.5e-5 Torr |
| T+10h | 升至次高温 | 400 | 401 | 升温 ~38℃/h |
| T+34h | 次高温保温结束 | 400 | 399 | K-S 多硫化物熔体形成 |
| T+42h | 升至最高温 | 850 | 850 | Ag-Ge-S-K 均匀液相 |
| T+90h | Tmax 保温结束 | 850 | 850 | 主降温开始 |
| T+240h | 主降温结束 | 400 | 401 | 3℃/h × 150h,片晶生长 |
| T+264h | 低温保温结束 | 400 | 400 | 应力释放 |
| T+304h | 炉冷至室温 | 25 | 26 | 出料 |

总实验时长:**304h**(单段均 ≤ 200h,符合实验室约束)

## observations[]

- **T+34h(预反应末)**:模拟观察——坩埚外壁无破裂,内壁无附着,深褐色熔体(K2S3-S 多硫化物)
- **T+90h(Tmax 末)**:模拟观察——均匀暗红色液相,无可见分层,无挥发物冷凝
- **T+240h(主降温末)**:模拟观察——液面边缘可见 mm 级片晶,中心区有少量针状副相(疑 Ag2S)
- **T+304h(出料)**:
  - 产物为深灰色片状单晶,最大尺寸约 1.8 mm,易解理
  - K2S3 块体呈黄褐色透明,温水/DMF 洗涤后获得片晶若干
  - 少量针状副相已机械分离(留作 EDX 验证)

## yield

| 字段 | 值 |
|---|---|
| 产物质量 | 0.38 g(目标 Ag4Ge3S5) |
| 投料 Ag4Ge3S5 当量 | 1.0 g(化学计量) |
| 产率(质量分数) | 38% |
| 形态 | mm 级层状片晶(最大 1.8 mm) |
| 副相质量 | <0.05 g(针状,疑 Ag2S) |

## product_formula

`Ag4Ge3S5` —— **继承自上游 stage 2 `02_novel_candidates.md` Pareto 前沿 gen002_ind_001**,
与上游 stage 3 `03_flux_parameters.md` 的 expected_phase 主相一致。

## product_cif_path

`cifs/gen002_ind_001_Ag4Ge3S5.cif`(fixture 内相对路径;原始真跑产物位于 `/tmp/mp_run/stage2_pareto_cifs/`)

> ✅ 真实存在(12 sites, sg=1 P1, 来自 mattergen evolution session evo_20260703_153008_08a2249a)
> Stage 4 `xrd-characterization` 可直接读取该 CIF 训练 XRD/PDF 模型

## spectra_path

`spectra_stub/ag4ge3s5_s1_placeholder.xye`(⚠️ stub 路径,fixture 内未实际放置该文件;原始位置 `/tmp/mp_run/spectra_stub/`)

> ⚠️ **STUB 占位**:L3 atomic skill 未落地,无真实谱图。
> Stage 4 在此情况下应:
> - (a) 跳过对实测谱图的 phase identification,仅跑"训练 + 模拟预测"模式
> - (b) 或使用 `fixtures/skill_io/materials_auto_xrd/response.json` 作为回放 fixture
> - (c) 待真实谱图补全后重跑

## deviations

无(执行完全按方案 A baseline,模拟环境无意外偏差)

## 与下游衔接

`xrd-characterization` 应消费:
- `sample_id` → xrd_report.sample_id
- `product_formula` (Ag4Ge3S5) + `product_cif_path` → XRD 训练目标相
- `spectra_path` → 推理谱图(stub 模式下需降级处理)
- `observations` 中的"针状副相疑 Ag2S" → xrd 应在 phase_identification 中检查 Ag2S

## 验收对照

| spec 字段 | 提供 | 来源 |
|---|---|---|
| batch_id ✅ | batch_ag4ge3s5_stub_001 | 模拟生成 |
| sample_id ✅ | batch_ag4ge3s5_stub_001_s1 | `{batch_id}_s1` 命名约定 |
| executed_recipe ✅ | 方案 A baseline,无偏差 | stage 3 flux_parameters |
| timeline ✅ | 8 个时间节点 | 模拟 |
| observations[] ✅ | 4 条 | 模拟 |
| yield ✅ | 0.38 g / 38% | 模拟 |
| product_formula ✅ | Ag4Ge3S5 | stage 2 novel_candidates |
| product_cif_path ✅ | 真实 CIF | stage 2 mattergen Pareto |
| spectra_path ⚠️ | stub 占位 | L3 待落地 |
| deviations ⭑ | 无 | — |
