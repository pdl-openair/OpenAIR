# XRD Report — CrSiTe3 Dry-Run 产物(Dry-Run Stub)

> 来源:基于 `fixtures/skill_io/materials_auto_xrd/response.json` 格式改写,目标相 CrSiTe3+AlN 双相 → **CrSiTe3 单相 + 副相**
> 生成方式:L2 skill `characterization` 消费上游 `04_synthesis_log.md` + 参考用 fixture,**未调 Docker / MP API / XRD-1.1**

## sample_id

`batch_crsite3_dryrun_001_s1`(来自上游 synthesis_log)

## model_training

| 项 | 值 |
|---|---|
| run_name | `crsite3_v1_dryrun` |
| 训练相 | CrSiTe3(R-3 #148,mp-676) |
| 训练谱图数 | 5 epoch × 增强样本 |
| 训练状态 | ✅ completed |
| 模型路径(占位) | `auto_xrd/soft_link/All_CIFs/crsite3_v1_dryrun/Models` |
| Docker | dry-run 未启动,占位声明 |
| MP_API_KEY | dry-run 未调用 |

## phase_identification

| 相名 | 空间群 | 质量分数 | 匹配度 | 备注 |
|---|---|---|---|---|
| **CrSiTe3** | R-3 (#148) | **88%** | 0.92 | **主相** |
| Te 残留 | P-3m1 (#164) | 8% | 0.78 | 上游 synthesis_log 风险项(未反应 Te) |
| 仪器背景相 | — | 4% | 0.65 | 样品台漫散 |

**主相纯度**:88% ⚠ **未达阈值 90%**

## residual

| 指标 | 值 | 评价 |
|---|---|---|
| Rwp | 0.087 | 良好(< 0.10) |
| GoF | 1.42 | 良好(< 2.0) |
| 残差主要来源 | Te (101) 峰位偏移 + 高角区背景 |

## confidence

| 项 | 值 |
|---|---|
| 总置信度 | **0.84** |
| 不确定性来源 | (1) 谱图 S/N 偏低(< 1000 计数/主峰);(2) CrSiTe3 与 Cr2Te3 部分峰位重叠;(3) dry-run 未真训练,数值为占位 |

## recommended_actions

⚠ 主相纯度 88% < 90% 阈值,建议执行以下操作:

1. **重训 + 补负样本**:加入 Cr2Te3、Cr5Te3、SiTe2 作为负样本相,提高混淆相区分度
2. **重新制备**:优化上游 synthesis_execution 的 Te 投料(过量 5% → 0%),延长主降温时间(100h → 150h)以减少 Te 偏析
3. **补相训练**:加入 Te 单相训练,使模型能干净剥离 Te 残留贡献

## spec 字段自检

- [x] sample_id 与上游 batch_id 可追溯(`batch_crsite3_dryrun_001_s1` ↔ `batch_crsite3_dryrun_001`)
- [x] model_training 给出配置 + 状态
- [x] phase_identification 每相含质量分数
- [x] residual 给出 Rwp/GoF 数值
- [x] confidence 给出 ≥1 不确定性来源(本报告含 3 条)
- [x] 主相纯度 < 阈值 → recommended_actions 含可执行建议(3 条)

## 端到端链路闭环验证

| Stage | 输入 | 产物 | 关键字段传递 |
|---|---|---|---|
| 1 database_search | 用户约束 | `01_mp_seeds.md`(CrSiTe3 mp-676) | formula + cif_path + 9 项性能 |
| 2 candidate_generation | 01_mp_seeds | `02_novel_candidates.md`(CrPS3/CrAsSe3/FeSiTe3) | formula + cif_name + objectives |
| 3 flux_parameter_rec | 02_novel_candidates | `03_flux_parameters.md`(KCl 8:1, 950℃) | expected_phase=CrSiTe3 + product_cif |
| 4 flux_synthesis_exec | 03_flux_parameters | `04_synthesis_log.md`(batch_001, 42% yield) | product_formula=CrSiTe3 + spectra_path |
| 5 characterization | 04_synthesis_log | `05_xrd_report.md`(主相 88%) | sample_id 反向追溯 |

**闭环判定**:✅ **字段层完全闭环**;主相纯度 88% 触发回流(→ 重训 / 回到 Stage 4 调整工艺 / 回到 Stage 2 重选 Pareto 候选)
