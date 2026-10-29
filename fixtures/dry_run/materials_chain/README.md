# Materials Pack 端到端 Dry-Run 链

统一故事线:**CrSiTe3** 三元硫族化合物。

本目录用于验证 materials pack 5 个 L2 skill 在统一材料线下的字段咬合,**不调外部 API**(MP / mattergen MCP / crystal-property-prediction MCP / XRD Docker)。

## 文件清单

| 文件 | 来源 | 对应 substage |
|---|---|---|
| `01_mp_seeds.json` | 复用 `fixtures/skill_io/materials_database_search/response.json` 中 CrSiTe3 (mp-676) 子集 | database_search |
| `02_novel_candidates.json` | 复用 `fixtures/skill_io/materials_new_candidate_generation/response.json`,标注 CrSiTe3 衍生 | candidate_generation |
| `03_flux_parameters.json` | **stub**(基于 CrSiTe3 化学式手写,参考 flux rec fixture 格式) | parameter_recommendation |
| `04_synthesis_log.md` | **stub**(按 `schemas/materials/specs/synthesis-execution.md` 修订版手写) | synthesis_execution |
| `05_xrd_report.md` | **stub**(按 spec 字段最小可用版,假设谱图路径指向 fixtures 内 placeholder) | characterization |

## 使用方式

1. 在 Claude Code 中按顺序调 5 个 L2 Skill:`database-search` → `candidate-generation` → `parameter-recommendation` → `synthesis-execution` → `characterization`
2. 每次调用时,把上游产物文件路径作为输入,要求 L2 skill 不调外部 API,只验证它能消费上游字段
3. 记录每个 L2 skill 是否能产出 spec 要求的产物

## 已知缺口(本 dry-run 修复)

- `schemas/materials/specs/synthesis-execution.md` 已补 `sample_id / product_formula / product_cif_path / spectra_path` 4 个字段(原 spec 缺这些,导致 characterization 无法衔接)
- `cif_path` 在 mp_seeds 和 novel_candidates 中是相对路径,dry-run 时统一指向 `fixtures/dry_run/materials_chain/cifs/`
