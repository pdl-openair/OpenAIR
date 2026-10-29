# section ↔ 数据来源映射 + 占位符清单

报告 section 0–10 固定不可删；每个占位符的数据从哪读、缺了怎么写，逐项如下。
路径均相对研究目录 `openair/<research_name>/`；`EM` = `report/evidence_manifest.json`
（由 `scripts/collect_evidence.py` 生成，key 与下表「EM key」列一致）。

## 映射表

| 占位符 | Section | EM key | 来源文件 / 抽取字段 | 缺失时写 |
|---|---|---|---|---|
| `{{research_name}}` | 头部 | state | goal.yaml `meta.research_name` / 目录名 | —（必有） |
| `{{report_status}}` | 头部 | — | dry_complete / wet_pending / full（由 wetlab_comparison + wetlab_ledger 推断） | 必填 |
| `{{date_range}}` `{{generated_date}}` | 头部 | — | goal.yaml created → 报告日 | 必填 |
| `{{goal_summary_table}}` | §1 | goal | goal.yaml objectives[]/acceptance | `⚠️ 缺失：无 goal.yaml` |
| `{{composition_constraints}}` `{{budget_summary}}` | §1 | goal | goal.yaml composition/budget | 同上 |
| `{{pipeline_figure_note}}` | §2 | — | figure-spec 生成 figures/pipeline.svg 的说明（图本身已由模板内嵌 `<img width=900>`，此占位仅写说明/降级注记，不再负责嵌图） | 图缺失时写 `⚠️ 缺失（figure-spec 未安装或数据不足）`；图存在则此占位可留空或写渲染说明 |
| `{{components_table}}` | §2 | — | 实际用到的 skill/MCP 名（从 state history + EM found 项归纳） | 至少列已 found 的组件 |
| `{{mp_summary}}` | §3 | mp_report | proposal/mp_search_report.md 摘要（命中数/结论节） | `⚠️ 缺失：MP 检索未执行或降级`；降级时引用 degraded_proposal.md |
| `{{mp_report_file}}` | §3 | mp_report | mp_search_report.md 或 degraded_proposal.md | — |
| `{{seed_table}}` | §3 | seed_cifs | design/seeds/*.cif + state.candidates origin | `⚠️ 缺失：无种子` |
| `{{evo_config_table}}` | §4.1 | evo_config | design/evo/config.json（objectives/backend/pop_size/max_gen…） | `⚠️ 缺失：无进化运行` |
| `{{steer_log_summary}}` | §4.2 | evo_snapshot, steer_log | snapshot_latest.json（generation/pareto_front_size/summary）+ steer_log.jsonl 代际干预 | 同上 |
| `{{leaderboard_top}}` | §4.3 | evo_leaderboard | leaderboard.csv Top 行（原列呈现） | 同上；降级环改用 alt_ledger |
| `{{novelty_table}}` | §4.4 | evo_evaluate | evaluate_report.json | `⚠️ 缺失：未运行 finalize --evaluate` |
| `{{synth_grade_table}}` | §4.5 | assessment_reports | synthesis-advisor/*/assessment_report.md（等级+首选路线） | `⚠️ 缺失：未做可制备性评估` |
| `{{recipe_table}}` | §4.6 | recipes | recommend_output_*.jsonl（方案/温度程序摘要） | `⚠️ 缺失：未跑配方推荐` |
| `{{dft_setup_summary}}` | §5.1 | dft_results | dft/<id>/band_gap/inputs 存在性 + vasp skill 协议名 | `⚠️ 缺失：无 DFT 运行` |
| `{{dft_bandgap_table}}` | §5.2 | dft_results | band_gap.json（**最小格式 `{"<NAME>": gap}`**，键=提交 --name）+ state.ml_metrics.bandgap | 同上 |
| `{{dft_stability_table}}` | §5.3 | dft_results | stability_result.json（formation_energy/ehull/status/pass_hull） | 同上 |
| `{{phase_diagram_note}}` | §5.3 | dft_results | stability/phase_diagram.png 的口径说明（图本身已由模板内嵌 `<img width=360>` 于 `{{dft_stability_table}}` 之后，此占位仅写 raw-PBE/校正口径等说明，不再负责嵌图） | 无相图则删模板内该 `<img>` 行并写 `（相图未生成）` |
| `{{dft_incidents}}` | §5.4 | state | state.stages.phase3.jcc_tasks + history（排队/失败/续跑事件） | `（无异常事件记录）` |
| `{{wetlab_ledger_summary}}` | §6 | wetlab_ledger | sample_ledger.csv（行数/outcome 分布） | `⚠️ 缺失：湿实验未启动` |
| `{{tri_compare_table}}` | §7 | wetlab_comparison | wetlab/comparison/comparison_*.md（表2 引入或内嵌） | 同上；**未回收时本节固定写「全部 pending_experiment」** |
| `{{conclusion_ranking}}` | §8 | — | goal.acceptance × §5/§7 判定的排名；措辞受限规则见 evidence-rules.md | — |
| `{{missing_links_table}}` | §9 | missing | EM missing[] 全量（环节/涉及/原因/影响/补救） | 无缺失写 `（无）` |
| `{{uncertainty_statement}}` | §9 | — | 依据 missing 的影响综合声明 | — |
| `{{evidence_manifest_table}}` | §10.1 | — | EM artifacts 全表（key/path/status） | — |
| `{{dir_tree}}` | §10.2 | — | 研究目录两层树 | — |
| `{{repro_commands}}` | §10.3 | — | 各阶段实际执行的命令（从 state history / 本报告 §2 归纳） | — |
| `{{glossary}}` | §10.4 | — | ehull/formation_energy/CLscore/pareto 等一行一条 | — |

## 报告状态判定

- 有 wetlab_comparison 且其表1 存在 `complete` 行 → `full`；
- 有 wetlab_ledger 行（n>0）但无 complete 对比 → `wet_pending`；
- 其余 → `dry_complete`。
