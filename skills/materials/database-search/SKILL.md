---
name: database-search
description: 把多目标性能约束（带隙、形成能、ehull、元素限制等）翻译为对真实材料数据库的查询，检索满足约束的已有候选并整理为下游演化的种子清单。
---

# Database Search（L2 编排）

## 职责

把用户的"多目标性能约束"翻译为对外部数据库的查询，检索满足约束的真实候选，按规范产出候选清单（artifact filename 由 pack 决定，materials pack 为 `mp_seeds.md`），供下游 `candidate-generation` substage 演化使用。

本 L2 是**域无关**编排：数据库选择、查询语法、属性项都由 L3 实现。L2 只负责：约束收集、流程编排、候选清单格式化。

## 执行步骤

1. 与用户确认约束集（性能上下界 + 元素/组成范围 + 禁用项）
2. 调用域特定 L3（materials pack 调用 `materials-mp-constraint-search`）跑数据库查询
3. （可选）调用第三方精算服务对 top 候选做交叉验证
4. 按 spec（`schemas/materials/specs/database-search.md`）产出候选清单
5. 给下游 `candidate-generation` 留下"哪些子空间值得演化"的引导

## 产出

见 spec。output_file = `mp_seeds.md`（materials pack 约定）。
