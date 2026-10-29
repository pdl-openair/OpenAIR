# evals（效果验收与产品 Harness）

本目录存放**不修改产品运行时**的验收与回归资产：OpenTrajectory Skill 效果 evals、对外产品浏览器 Harness。

| 子目录 | 用途 | 入口 |
| --- | --- | --- |
| [opentrajectory/](opentrajectory/README.md) | OpenTrajectory Skill 真实效果验收、回归与版本比较 | `governance/README.md`、各 Skill Eval Spec |
| [openair-product-harness/](openair-product-harness/README.md) | 对外科研工作台浏览器证据 Harness（Playwright） | `scripts/run-baseline.ps1` |

与 `fixtures/` 的区别：`fixtures/` 供联调与回放样例；`evals/` 供**验收规则、数据集、harness 与留档**，不替代生产代码路径。
