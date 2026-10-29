---
name: ope-rs-info-retrieval
description: 当用户想要检索离线策略评估（OPE）/离线强化学习/决策智能相关的学术论文、代码仓库或两者兼有时自动触发。常见场景：用户说"搜 IPS/DR/WDR 估计器相关论文"、"找 OPE benchmark 代码"、"查离线 RL 最新进展"、"帮我找 OPE 数据集"。本 skill 并行检索多个平台、去重排序后输出结构化清单，不做深度精读（那是 ope-rs-literature-filter 的工作）。
---

# 信息检索（L3 — OPE）

给定研究主题或关键词，并行检索学术论文数据库与代码仓库，输出结构化综合结果。

## Method

### Step 1：解析用户输入
- 提取研究主题/关键词（如 `off-policy evaluation`、`IPS estimator bias`、`OPE benchmark`）
- 判断用户是否指定范围（仅论文 / 仅代码 / 两者都要），默认两者都要
- 为每个平台生成针对性 query

### Step 2：并行发起搜索
同时向所有目标平台发起搜索（用 `WebSearch`），互不依赖：
- **论文**：arXiv（`site:arxiv.org`）、Google Scholar（`site:scholar.google.com`）、IEEE（`site:ieeexplore.ieee.org`）
- **代码**：GitHub（`site:github.com`）、Papers with Code（`site:paperswithcode.com`）

OPE 相关 query 示例：
```
site:arxiv.org off-policy evaluation "doubly robust"
site:github.com "off-policy evaluation" OPE benchmark
site:paperswithcode.com off-policy evaluation
```

### Step 3：收集并合并结果
1. 每个平台的结果去重（相同标题或相同 URL 只保留一个）
2. 论文按发表时间排序、仓库按 stars 排序
3. 每类结果限数：论文最多 10 篇，代码仓库最多 8 个

### Step 4：质量评估
- **真实性**：再检索几轮，确保所有结果真实存在，报告中给出链接
- **时效性**：论文要求近 3 年数量不小于一半
- **仓库质量**：star 数优先 >100，若全部不足则放宽至 >50
- **相关性**：与 OPE/离线 RL/决策评估主题直接相关
- **完整性**：覆盖主题下的主要方法族（DM/IPS/DR/WDR/FQE 等）

### Step 5：输出结构化结果
按下方 Output Contract 输出，附后续操作提示（如"可让我精读某几篇"、"分析这些仓库的技术栈"）。

## Input

- 研究主题 / 关键词（必填）
- 范围偏好：仅论文 / 仅代码 / 两者（可选，默认两者）
- 时间或方法过滤（可选）

## Output

按 `schemas/ope/specs/reference.md` 模板产出，使用 Write 工具写入当前任务工作区的 `proposal/reference.md`，结构：

```markdown
# 研究检索结果：<研究主题>

## 学术论文（共 N 篇）
| # | 标题 | 来源 | 年份 | 链接 |
|---|------|------|------|------|

## 代码仓库（共 N 个）
| # | 仓库名 | 来源 | Stars | 链接 |
|---|--------|------|-------|------|
```

## Constraints

- 必须并行检索所有平台，不串行等待
- 不要堆砌 raw 结果：必须去重、排序后再输出
- 某平台搜索失败时跳过并在结果中注明，不阻塞其他平台
- 禁止虚构论文标题、作者或链接——拿不准的条目不要列入
- 结果必须落盘到 `proposal/reference.md`，不只输出在对话中
