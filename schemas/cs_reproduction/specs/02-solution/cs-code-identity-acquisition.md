# 代码版本固定

## 输入

- `repository`
- `revision`

## 输出

- `repository_revision_acquisition_result`

## 验收

- `immutable_code_identity_resolved`

## 研究工作说明

基于上一节点已经形成并被接受的正式的已确认并通过来源核验的 `reproduction_repository` 与 `reproduction_revision`，解析真实、可复验的代码身份。

缺少必要输入时不得启动；不得静默切换默认分支或最新 revision；结果必须保留真实仓库与 immutable 代码身份的追溯关系。
