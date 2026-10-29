---
name: wet-lab-workflow
description: 湿实验（人工合成+表征）工作流管理：从管线设计阶段产物生成实验指派单，初始化并维护样品台账与表征结果回收表，并生成「预测(ML/DFT) vs 实测」三方对比报告。Use when 用户说「派湿实验 / 开湿实验 / 生成实验指派单 / 填台账 / 回收表征数据 / 对比预测和实测 / wet lab / sample ledger / characterization collection」。输入来自管线设计阶段产物（design/candidates 候选 CIF、synthesis-advisor 评估报告、recommend_parameter 推荐配方），输出可直接被 experiment-report 汇聚。不负责 DFT 干实验（vasp-band-gap / vasp-thermodynamic-stability）与最终报告（experiment-report），也不负责制备方案本身的推荐（crystal-synthesis-advisor / materials-parameter-recommend-flux）。
---

# wet-lab-workflow — 湿实验工作流管理

管理湿实验（人工合成 + 表征）的"人机接口"三件套：**指派单**（告诉实验员做什么）、**台账**（记录做了什么）、**回收表**（收回测到了什么），外加一个**对比脚本**（把实测与 ML/DFT 预测对齐）。

所有文件都在研究目录 `openair/<research_name>/wetlab/` 下：

```
wetlab/
├── assignments/<candidate_id>_assignment.md   # 实验指派单（本 skill 生成）
├── sample_ledger.csv                          # 样品台账（实验员/Claude 代填）
├── characterization_results.csv               # 表征回收表（实验员/Claude 代填）
└── comparison/comparison_<YYYYMMDD>.md        # 三方对比（脚本生成）
```

## ID 规则（join key，只消费不生产候选号）

- `candidate_id`（C001…）：来自管线铸号（design/candidates/C###_<formula>.cif），本 skill 不新建。
- `assignment_id`（A001…）：与 candidate_id 一对一，指派单文件名前缀。
- `recipe_id`（R001…）：= recommend_output_<formula>.jsonl 的行号，溯源写 `文件名#L<行号>`。
- `sample_id`：`S<C###>-<nn>`，如 `SC001-01`（C001 的第 1 个样品）。
- `result_id`（X001…）：表征回收表内递增。

## 三步流程

### Step 1 — start（派单）

对进入湿实验的每个候选（默认管线 goal.yaml 的 `wet_lab.n_candidates`，默认 2）：

1. 从 `pipeline_state.json` 的 candidates[] 取该候选的 ml_metrics / recipe_ref / dft 值。
2. 按模板 `templates/assignment.md.template` 填写 9 节指派单，Write 到
   `wetlab/assignments/<candidate_id>_assignment.md`。
   - 第 3 节主推方案从 `recipe_ref` 指向的 jsonl 行取（含完整分段温度程序）；
   - 第 4 节备选方案摘自 `synthesis-advisor/<formula>_<日期>/assessment_report.md` 的推荐方案节；
   - 第 6 节表征要求的"预期值区间"必须同时给出 ML 预测值和（若有）DFT 值及来源文件。
3. 初始化台账与回收表：若 `wetlab/sample_ledger.csv` / `characterization_results.csv`
   不存在，用 `templates/*.header.csv` 的表头创建空表。

### Step 2 — record（记账）

实验员交付后，把纸质/口头记录转录进两个 csv（Claude 代填，遵守
`references/field-dictionary.md` 的枚举值；csv 为 UTF-8，Excel 编辑过带 BOM 也能读）。
`updated_at` 每次修改必须更新。

### Step 3 — compare（对比）

```bash
python .claude/skills/wet-lab-workflow/scripts/compare_predicted_measured.py \
  --research-dir openair/<research_name> [--tol 0.25]
```

输出 `wetlab/comparison/comparison_<YYYYMMDD>.md`：总览表、三方对比表（ML | DFT | 实测
均值±范围 | Δ | 判定）、缺数据清单。判定规则：bandgap 相对偏差 ≤ tol（默认 25%）记
「一致」；lattice_a 偏差 ≤2% 记「一致」；ehull↔phase_purity 定性比对。无实测的候选行
照出、判定 `pending_experiment`——**绝不省略行、绝不编数**。

## 边界与注意

- csv 均为 UTF-8；解析方（compare 脚本）已容忍 BOM 与 `#` 注释行。
- 指派单里的每个数字必须带来源（ML 值 ← pipeline_state.json，DFT 值 ← dft/<id>/ 产物）；
  没有来源的数字不要写。
- 台账/回收表是唯一事实来源（single source of truth）；对比脚本只读不写它们。
- 若候选没有 recipe（未跑 flux 推荐），指派单第 3 节写 `⚠️ 缺失：无推荐配方`，
  第 4 节 advisor 方案照写。
