---
name: experiment-report
description: 自包含的材料实验研究报告生成器（markdown 输出）。按固定 section 结构汇聚全链路证据（MP 调研 → 进化/生成设计 → ML 预测 → DFT 验证 → 湿实验实测），每个数值可溯源到产物文件，缺失环节显式标注 ⚠️ 缺失 并评估影响，图表由项目内 paper-figure / figure-spec 生成落 report/figures/。Use when 用户说「出报告 / 写实验报告 / 汇总这次研究 / 生成发现报告 / experiment report / final report」。本 skill 只做证据汇聚与撰写，不执行任何计算或实验，不修改任何上游产物；上游组件的孤立执行（算带隙、查 MP 等）不用本 skill。
---

# experiment-report — 实验报告生成（自包含，markdown）

把一次材料发现研究的全链路产物汇聚成一份 `openair/<research_name>/report/experiment_report.md`。**自包含**：只依赖本 skill 与项目内 viz skill（`paper-figure` / `figure-spec`），不引用任何用户级目录。

## 工作流

1. **收集证据**（先跑，报告只允许引用其清单内的数）：
   ```bash
   python .claude/skills/experiment-report/scripts/collect_evidence.py \
     --research-dir openair/<research_name>
   ```
   → `report/evidence_manifest.json`（逐项 found/missing/empty + 关键数值抽取）。
2. **生成图表**（manifest 里有数据才生成，落 `report/figures/`）：
   - 管线/方法图 → 调项目内 `figure-spec`（JSON→SVG，命名 `pipeline.svg`）；
   - 数据图（Pareto 前沿散点、ML-DFT-实验 parity、候选性质柱状）→ 调项目内
     `paper-figure`（matplotlib，PNG）。项目内缺该 skill 时图位写
     `⚠️ 缺失（paper-figure 未安装）`，**不得引用用户级路径**。
3. **撰写报告**：按 `templates/experiment_report.md.template` 的固定 section 0–10，
   替换 `{{占位符}}`（清单见 `references/section-sources.md`）。未获得的占位符写
   `⚠️ 缺失：<原因>`，**不删 section**——结构永远完整。

## 铁律（违反任何一条即返工）

1. **无数值不得无溯源**：报告中每个数值必须来自 evidence_manifest 的 extracted，
   或标注来源文件相对路径（表格带 `来源` 列，正文用 `‹相对路径›` 行内指针）。
   manifest 里没有的数字不许出现。
2. **缺失显式标注**：统一 `⚠️ 缺失` 前缀；§9 按固定表列出全部缺失（从 manifest
   的 missing 列表生成），并写明影响与补救建议。
3. **措辞受限**：湿实验未回收（§6/§7 全 pending）→ 结论 §8 禁用「实验验证」字样；
   走过降级链路（degraded_proposal / evo-alternative / DFT 部分 skipped）→ §9
   必须点名，禁止表述为完整验证。
4. **只读汇聚**：不执行计算、不改上游产物、不重跑任何组件。

## 报告状态（头部）

- `dry_complete`：目标→设计→DFT 全链路完成，无湿实验数据；
- `wet_pending`：湿实验已派单未回收；
- `full`：含实测三方对比。

## 参考

- `references/section-sources.md` — section 0–10 ↔ 数据来源文件映射 + 占位符清单；
- `references/evidence-rules.md` — 证据规则细则（溯源格式、缺失表、三方对比表结构、图表规范）；
- `templates/experiment_report.md.template` — 报告骨架。
