# Phase3 Retrieval Matrix LLM Judge (Deferred)

本目录用于存放 Phase3 `retrieval-matrix-generation` 的 LLM judge 资产，当前 **尚未随 PR #102 合入**，刻意 defer 到后续任务。

## 预期文件

| 文件 | 用途 |
| --- | --- |
| `judge-prompt-v0.md` | Judge prompt 模板（含 `{{JUDGE_INPUT_JSON}}` 占位符） |
| `judge-output.schema.json` | Judge 结构化输出 schema |

## 运行时行为

- `run_strategy_phase1_3.py --llm-judge` 在确定性检查通过后会调用 `phase3_llm_judge.evaluate_judge_with_llm()`。
- 在资产补齐前，调用会因缺少上述文件而失败；单元测试通过 mock 绕过，不依赖本目录正文。
- Harness 路径约定：`evals/opentrajectory/phase3-retrieval-matrix-llm-judge-v0/`（见 `phase3_llm_judge._judge_root()`）。

## 后续收口

1. 添加 `judge-prompt-v0.md` 与 `judge-output.schema.json`。
2. 在 isolation 环境跑至少一个 replay case 的 `--llm-judge` 留档。
3. 更新 `evals/opentrajectory/README.md` 批次 1 状态表，将 LLM judge 从 deferred 标为可用。
