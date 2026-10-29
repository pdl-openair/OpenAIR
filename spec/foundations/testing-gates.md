# 测试门禁登记（testing-gates）

> **定位**：登记账。登记《测试代码架构》体系条目的当前强制状态——**已机器强制 / 待 tg 合入接线 / 仍为文档层**——并指向 `verification/`（Suite 契约与守卫实物）与《测试代码架构》（规则权威）。本文件不复制其规则正文。
>
> **规则权威**：《测试代码架构》等测试体系文档 → `spec/testing/`（已由 B2 批次迁移并合入）。规则-强制映射总表见 `spec/enforcement/enforcement-matrix.md`（B2 交付）。
>
> **设计依据**：《AI4SE Harness 重构设计方案》§9.1-B4、§13-2/§13-4/§13-5、§5.1（SPEC-TEST-01/SPEC-TEST-02）。**纪律**：测试资产放置守卫不自建、不提前接线、不从 tg 分支 cherry-pick（冻结裁决，§13-4）。

## 一、已机器强制

| 条目 | 强制载体 | 状态与证据 |
|---|---|---|
| Suite 合同元数据（suite.yaml 必填字段） | `verification/suites/test_asset_architecture/guard.py`（已在 test 基线 c1f15592） | 新增/变更 suite.yaml 全量校验 12 必填字段；存量 Suite 豁免全量字段校验，仍受 YAML 合法性、live profile 身份一致性、suite_id 唯一性、平行 manifest 禁令、"有内容无合同 Suite 目录"检查。4 个 Suite 在案（durable_child_policy_handoff、materials_workflow_admission、opentrajectory_v3_research_record_handoff、test_asset_architecture）。B4 实测：guard 全仓扫描通过（exit 0） |
| required-checks 手挑 P0 快信号（5 条 + build） | `.github/workflows/required-checks.yml` | 过渡态，tg 合入后由 unit-test job 承接运行面（§13-5 配方③）。**B4 实测发现**：第 5 条节点 test_post_confirmation_central_guard_never_projects_provide_input（`services/api/tests/test_workbench_user_actions.py` 中已不存在，该文件现为 test_post_confirmation_human_authority_projects_a_real_input_entry）——pytest 对不存在的 node id 整场 exit 4，该 CI 步骤当前必红；修正属 B5 域（.github 禁改本批次） |
| SPEC-COV-01：产品代码新增/修改行 diff-coverage ≥50% | `scripts/gates/diff_gate.py`（B1 交付）+ pytest-cov | 工具随 Wave 1 落地；CI 接线在 B5（本分支不含工具本体） |
| SPEC-TEST-01：CI 单测套件按目录选择 | `scripts/gates/select_unit_tests.py`（B4 交付）+ B5 unit-test job | 选择器已交付：341 文件 / 44 个可共跑组（runs）；收录与排除边界（含 services/api 集成边界、节点级 deselect 清单）见脚本 docstring 与 `docs/dev_plans/2026-09-ai4se-harness/ledger/b4.md`。调用契约：**逐组** `PYTHONPATH=services/api python -m pytest <组内文件> --deselect <节点>`（跨 skill 同进程收集物理不可行——adapter 包名/sys.modules 污染+同名模块，详见账本）；CI 接线在 B5 |

## 二、tg 合入接线（已接线，sync 批 2026-09-26）

**SPEC-TEST-02：测试资产放置守卫。**

- **已接线（sync 批）**：guard 两步（Collect changed paths for Architecture Guard → Enforce test-asset architecture）随配方③融合进 required-checks.yml 的 test job（fetch-depth: 0）；运行面（guard 自测）由 unit-test job 的选面承接（verification/suites 在选面内）。
- **组件根扩展（debt ① 裁决：扩根非豁免）**：guard 的 `PRODUCTION_COMPONENT_ROOTS` 增加 `scripts/gates`——门禁实现是生产组件，其 `tests/` 遵循组件配对原则（tests/ 放置＋拥有 suite.yaml），`scripts/` 其余位置仍禁。`scripts/gates/tests/suite.yaml` 与 `scripts/gates/canary/tests/suite.yaml` 为配对合同。
- **二波 authority_refs 改写**：28 个组件级 suite.yaml 的 `docs/01_核心设计/测试体系/*` → `spec/testing/*`（B2 迁移补链），幻锚点（guard 无锚点语法，ref 按纯路径校验）已去除后缀。
- 历史（接线前状态，B4 只读实证 2026-09-24）：
  - guard 自测：`pytest verification/suites/test_asset_architecture/ -q` → 34 passed，exit 0。
  - guard CLI 全仓扫描（无 added-paths）→ "Test-asset architecture guard passed."，exit 0（当前基线布局对守卫全绿）。
  - **前瞻判定实证**（`--added-paths-file` 假设清单）：`scripts/gates/tests/` 下门禁单测曾被判 "new long-term test assets are forbidden under scripts/"——由上述扩根裁决处置（非豁免）。
  - 非 scripts/ 的新资产（spec/ 文档、verification 新 Suite 测试）不触发该规则；guard 对存量文件不做回溯追究（前瞻式漂移保护）。

## 三、仍为文档层（无工具承载）

| 条目 | 出处锚点（迁移后路径） | 为什么不适合机器判（或暂无承载） |
|---|---|---|
| 测试替身分类学（替身不得变成被测系统） | 《测试代码架构》→ `spec/testing/`（已迁移合入） | 语义判定：机器判不了"替身是否行使了被测系统的真实责任"，属独立评审（L4）职责（设计 §5.2 诚实分界） |
| 三态结果治理（PASS / FAIL / NO_VALID_EVIDENCE） | 同上 | `verdict_rule` 字段承载三态裁决文本合同（guard 只校验字段存在），三态语义无机器执行器；acceptance runner 属 SDR 战役线，不在本战役范围 |
| 非确定性预登记纪律（live/概率性测试须声明依赖与证据） | 同上 | `execution_profiles`/`required_dependencies` 承载声明本身（字段已机器持有），但声明与实际执行的一致性无工具校验——登记纪律靠评审与 Suite owner |
| 《测试代码架构》其余原则条款（5 类代码归属表、三类边界、依赖方向） | 同上 | 原则性条文；其中"放置"子集已由 tg guard 承载（待接线，见上节），其余以 L4 评审承载，不强行降为机械规则（"更严 ≠ 更对"） |

---

*维护约定：本登记账随强制状态变化更新（接线事件、新 Suite、新工具承载）；每次变更须同步 `spec/enforcement/enforcement-matrix.md`（B2 交付后）对应行。*
