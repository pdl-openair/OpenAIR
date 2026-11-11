# Triage Labels

`triage` 技能用两个分类角色与五个状态角色表述。本文件把这些角色映射到本仓库 issue tracker 实际使用的标签字符串。

## 分类角色

| 标准角色 | 本仓库的标签 | 含义 |
| --- | --- | --- |
| `bug` | `bug` | 有东西坏了 |
| `enhancement` | `enhancement` | 新功能或改进 |

## 状态角色

| 标准角色 | 本仓库的标签 | 含义 |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | 维护者需要评估此 issue |
| `needs-info` | `needs-info` | 等待报告者补充更多信息 |
| `ready-for-agent` | `ready-for-agent` | 任务要求完整，AFK agent 可直接开始 |
| `ready-for-human` | `ready-for-human` | 需要人工实现 |
| `wontfix` | `wontfix` | 不予处理 |

当技能提到某个角色时，使用本表中对应的标签字符串。

## 与施工状态的分界

- 本表的状态标签只描述分诊位置，不描述施工进度。施工进度用 `spec/workflows/issue-workflow.md` 定义的状态机。
- `ready-for-agent` 不满足 Gate A。fix 类触产品代码的 PR 仍须关联带 `status/verified` 的 issue，见 SPEC-PROC-02。
- `bug`、`enhancement`、`wontfix` 已存在于 GitHub。其余四个状态标签需手动创建：`gh label create needs-triage --description "维护者需要评估"`，另外三条同理。
- `.github/labels.yml` 只是标签清单，没有被 CI 自动应用。该清单里的 `type/*`、`priority/*`、`area/*`、`status/*` 目前都未同步到 GitHub，只有本文件新增的四个状态标签已登记。
