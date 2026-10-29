# 字段字典 — wet-lab-workflow

三件套全部字段的语义、单位、枚举与 join 规则。csv 为 UTF-8（容忍 BOM 与 `#` 注释行）。

## sample_ledger.csv（样品台账，一行 = 一个样品）

| 列 | 类型/枚举 | 说明 |
|---|---|---|
| sample_id | string, PK | `S<C###>-<nn>`，如 SC001-02 = 候选 C001 第 2 个样品 |
| candidate_id | string, FK | C###；与 sample_id 前缀一致 |
| assignment_id | string, FK | A### |
| recipe_id | string, FK | R###（= recommend_output jsonl 行号） |
| prep_date | YYYY-MM-DD | 合成日期 |
| method | `flux\|cvt\|solid_state\|arc_melting\|cvd\|other` | 制备方法 |
| batch_no | string | 同炉批次号（可空） |
| precursors | text | 原料与配比，如 `Mo:Se=1:2` |
| flux_agent | string | 助熔剂/输运剂（可空） |
| max_temp_c | number | 最高温度 ℃ |
| hold_time_h | number | 保温时长 h |
| cooling_rate_kh | number | 降温速率 ℃/h |
| container | `quartz_ampoule\|alumina_crucible\|other` | 容器 |
| atmosphere | `ar\|vacuum\|air\|se_vapor\|other` | 气氛 |
| outcome | `success\|partial\|failed\|pending` | 结果（pending=未开炉/未判定） |
| mass_mg | number | 产物质量 mg（可空） |
| operator / notes / updated_at | string | 记录人 / 备注 / 最后更新时间 |

## characterization_results.csv（表征回收，一行 = 一条测量）

| 列 | 类型/枚举 | 说明 |
|---|---|---|
| result_id | string, PK | X### 递增 |
| sample_id | string, FK | 指向台账 |
| candidate_id | string, FK | 冗余列便于 join（缺省时脚本会从 sample_id 前缀推导） |
| technique | `xrd\|sem\|eds\|uv_vis\|raman\|hall\|ppms\|afm\|other` | 表征技术 |
| property | 见下表 | 测量的性质 |
| value | string | 原始记录值（原文，含单位/注记） |
| value_num | float | 数值化（能转则转，不能空） |
| unit | `Å\|eV\|S·cm⁻¹\|cm⁻³\|K\|deg\|other\|–` | 单位 |
| conditions / instrument / measured_date / measured_by | string | 条件 / 仪器 / 日期 / 测量人 |
| data_file | string | 原始数据文件路径（相对研究目录，可空） |
| confidence | `high\|medium\|low` | 置信度 |
| notes | string | 备注 |

### property 枚举（与对比脚本的映射）

| property | technique 约定 | 对应预测量 | 对比规则 |
|---|---|---|---|
| `bandgap_eV` | uv_vis | ML bandgap / DFT 带隙 | 相对偏差 ≤ tol（默认 0.25） |
| `lattice_a` | xrd | （无 ML/DFT 对应） | 仅记录与跨样品一致性 |
| `space_group` | xrd | 结构信息 | 定性 |
| `phase_purity` | xrd | ehull（定性） | stable↔success/partial |
| `morphology` | sem | — | 记录 |
| `composition` | eds | — | 记录 |
| `carrier_concentration` / `resistivity` | hall | — | 记录 |
| `other` | any | — | 记录 |

## 指派单占位符（assignment.md.template）

`{{assignment_id}} {{candidate_id}} {{candidate_id_num}} {{formula}} {{recipe_id}} {{assign_date}}
{{operator}} {{priority}} {{objectives_summary}} {{ml_metrics_table}} {{cif_path}} {{space_group}}
{{lattice_params}} {{natoms}} {{recipe_ref}} {{precursors}} {{flux_agent}} {{container}}
{{atmosphere}} {{separation}} {{temperature_program}} {{expected_outcome}} {{alt_options}}
{{assess_date}} {{risk_notes}} {{ml_dft_note}} {{characterization_requirements_table}}
{{due_date}} {{provenance_list}}`

## 与上游产物的对应

| 需要的数据 | 从哪读 |
|---|---|
| 候选/ML 预测/DFT 结果 | `pipeline_state.json` 的 candidates[]（ml_metrics、dft、recipe_ref） |
| DFT 兜底 | `dft/<id>/band_gap/final/band_gap.json`（最小格式 `{"<NAME>": gap}`）、`dft/<id>/stability/stability_result.json` |
| 主推配方 | recipe_ref 指向的 `recommend_parameter/data/recommand_recipe/recommend_output_<formula>.jsonl#L<行号>` |
| 备选方案/风险 | `synthesis-advisor/<formula>_<日期>/assessment_report.md` |
| 目标与约束 | `goal.yaml` |
