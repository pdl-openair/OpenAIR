<!-- PR 证据合同：六节必填字段与 spec/workflows/pr-workflow.md 一致，由 scripts/gates/validate_pr_evidence.py 机器校验。
     验证六层级：L1 已分析 / L2 已自测 / L3 单测通过 / L4 集成验证 / L5 预发验证 / L6 真实 E2E 已验证 -->

## 改了什么

<!-- 1-3 条说明本次 PR 的主要改动；空模板直接提交会被校验脚本拦下 -->

-

## 关联任务

<!--
GitHub Issue 必须用 Closes #xx，合并 PR 后自动关闭任务。
如果只是关联但不关闭，用 Ref #xx。
飞书任务写任务名或链接。
无则写 N/A。
-->

Closes #

## 没改什么

<!-- 防 reviewer 误解范围；确无则写 N/A -->

-

## 主要修改文件

<!-- 写主要路径即可，不用列全量 diff -->

-

## 怎么验证

<!-- 必含一行"验证层级: L<1-6>"。六层级：L1 已分析 / L2 已自测 / L3 单测通过 / L4 集成验证 / L5 预发验证 / L6 真实 E2E 已验证 -->

验证层级: 

- 启动方式：
- 进入路径：
- 验证步骤：
- 预期结果：

## 影响面勾选

<!-- 至少勾选一项（含"均不涉及"）。勾选项与 shared 类型 / fixtures / skill registry / schemas 四类合同载体对齐 -->

- [ ] 均不涉及
- [ ] 影响 shared 类型（packages/shared）
- [ ] 影响 fixtures
- [ ] 影响 skill registry（skills/registry.yaml、skill-bindings）
- [ ] 影响 schemas（schemas/、Pydantic models）
- [ ] 影响接口字段
- [ ] 影响 Mock 数据
- [ ] 影响任务状态 / 过程产物
- [ ] 已同步更新相关文档
- [ ] 需要进一步确认，说明：

## CI run 链接

<!-- 填本次 PR 的 CI run 链接（http…）。确无 CI 时整行写：N/A（本地验证）——fix 类 PR 不允许 N/A，必须给链接 -->

-

## 风险分级

<!-- 圈一个：high / medium / low。判定表见 scripts/gates/classify_change_risk.py 头部注释（规则正文 spec/workflows/review-workflow.md） -->

风险分级: 

补充说明（风险细节 / 注意事项，无则 N/A）：

N/A
