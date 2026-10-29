# Synthesis Log — CrSiTe3 Flux 合成执行(Dry-Run Stub)

> ⚠️ **DRY-RUN STUB**: 本文件按 `schemas/materials/specs/synthesis-execution.md` 修订版字段手写,
> 用于验证 materials pack 5 个 L2 skill 的字段咬合。所有数值为占位,不代表真实实验。

## 元信息

| 字段 | 值 |
|---|---|
| batch_id | `batch_crsite3_dryrun_001` |
| sample_id | `batch_crsite3_dryrun_001_s1` |
| 上游 flux_parameters | `fixtures/dry_run/materials_chain/03_flux_parameters.json`(方案A_baseline) |
| 实验日期 | 2026-07-03(占位) |

## executed_recipe

按上游方案 A baseline 执行,**无偏差**:

- 原料:Cr 粉 / Si 粉 / Te 粉(化学计量比 1:1:3)
- 助熔剂:KCl(预脱水,KCl:CrSiTe3 = 8:1)
- 坩埚:石英真空封管
- Tmax:950 ℃ / 24 h
- 主降温:3 ℃/h 至 650 ℃ / 24 h
- 炉冷至室温

## timeline

| 时间点 | 事件 | 实测温度(℃) | 备注 |
|---|---|---|---|
| T+0h | 投料封管 | 25 | 真空度 1e-3 Pa |
| T+10h | 升至次高温 | 500 | 预反应段 |
| T+22h | 升至最高温 | 950 | 保温开始 |
| T+46h | 主降温开始 | 950 | 3 ℃/h |
| T+146h | 低温恒温 | 650 | 24 h |
| T+170h | 炉冷至室温 | 25 | 出料 |

## observations[]

- T+46h:开炉观察中间态,坩埚外壁无破裂,无明显挥发
- T+170h:出料。产物为银灰色片状单晶,最大尺寸约 2 mm,易解理
- KCl 块体呈透明,温水溶解后获得片状晶体若干

## yield

- 产物质量:0.42 g(目标 CrSiTe3)
- 投料 CrSiTe3 当量:1.0 g
- 产率:42%(质量分数)
- 形态:mm 级层状单晶

## product_formula

`CrSiTe3`(继承自上游 `02_novel_candidates.json` 中 cand_003 FeSiTe3 的同族硫族化合物,此处 stub 替换为 CrSiTe3 以保持故事线一致)

## product_cif_path

`fixtures/dry_run/materials_chain/cifs/CrSiTe3_mp-676.cif`

> 来源:复用 `fixtures/skill_io/materials_database_search/response.json` 中 `mp-676` 的 base64 CIF 解码后落盘
> dry-run 占位:实际文件未生成,但路径声明满足字段契约

## spectra_path

`fixtures/dry_run/materials_chain/spectra/CrSiTe3_batch_001.xye`

> dry-run 占位:实际谱图未生成。下游 `characterization` 在 dry-run 模式下应仅跑训练-模拟预测,
> 跳过对真实谱图的推理(或使用 `fixtures/skill_io/materials_auto_xrd/` 中已有训练结果作回放)

## deviations

无。dry-run stub。

## 与下游衔接

`characterization` 应消费:
- `sample_id` → xrd_report.sample_id
- `product_formula` + `product_cif_path` → XRD 训练目标相
- `spectra_path` → 推理谱图(dry-run 下可能为空)
