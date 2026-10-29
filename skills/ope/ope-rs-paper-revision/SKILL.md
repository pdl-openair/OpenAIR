---
name: ope-rs-paper-revision
description: 当用户想要修改润色 OPE 论文、选定期刊投稿、或回复审稿意见时自动触发。常见场景：用户说"修改论文"、"润色"、"选期刊"、"投稿"、"回复审稿人"。本 skill 完成内部审阅、投稿准备、审稿意见回复，不负责论文初稿撰写。
---

# 论文修订（L3 — OPE）

修改论文、内部审阅、选定期刊投稿、回复审稿意见。

## Method

### 子任务 1：内部审阅与打磨

1. **自检清单**
   - 逻辑连贯性：段落之间逻辑通顺，引言→方法→结果→讨论一脉相承
   - 语言质量：语法、拼写、用词
   - 格式规范：是否符合目标 venue 格式（页数、字号、行距）
   - 图表质量：清晰度、分辨率、标注完整性
   - 引用完整性：所有 \cite{} 都有对应 bib entry，所有 bib entry 都被引用

2. **同行审阅模拟**
   - 模拟审稿人视角（OPE 领域专家）
   - 识别潜在质疑（如："为什么没和 FQE 比"、"覆盖度低时如何保证估计可靠"）
   - 补充可能的解释或额外实验建议

3. **修改跟踪**
   ```markdown
   | 日期 | 修改内容 | 修改原因 |
   |-----|---------|---------|
   | 2026-06-15 | 方法部分增加收敛性证明 | 审阅人指出理论支撑不足 |
   ```

### 子任务 2：选定期刊与投稿

1. **venue 选择**
   - OPE 相关顶会：NeurIPS / ICML / ICLR（理论 + 方法）
   - 应用类：KDD / WWW / RecSys（推荐系统 OPE）
   - 期刊：JMLR / MLJ / IEEE TNNLS
   - 评估维度：影响因子、接收率、审稿周期

2. **投稿前检查**
   - 页数限制（NeurIPS 9 页正文 + 无限附录）
   - 格式要求（匿名 / 非匿名）
   - 补充材料要求
   - 作者指南核对

3. **投稿材料准备**
   - Main Paper (PDF)
   - Supplementary Materials (ZIP)
   - Cover Letter（强调贡献与 venue 匹配度）
   - Highlights / Graphical Abstract（如需要）
   - Conflict of Interest Statement
   - Data Availability Statement（OPE 复现代码与数据）

### 子任务 3：回复审稿意见

1. **意见分析**
   - 分类：Major / Minor / Reject-Resubmit
   - 逐条分析每个问题
   - 评估修改难度

2. **回复策略**
   - 逐条回复，不遗漏
   - 态度：谦虚但不卑微
   - 能改则改；不能改要充分解释（如计算资源不足、超出研究范围）

3. **回复模板**
   ```markdown
   ### Reviewer 1

   **Comment 1:** [原文引用]
   > 谢谢审稿人的建议。我们进行了以下修改：
   - 在第 X 页增加了 Y 的描述（见 Figure X）
   - 修改了 Z 的描述使其更准确（见第 X 行）

   **Comment 2:** [原文引用]
   > 谢谢审稿人的指出。您说得对，但我们认为... [理由]
   ```

## Input

- 论文草稿（来自 `ope-rs-paper-writing` 的 `report/paper.tex` 或 `paper.md`）
- 可选：已有的审稿意见

## Output

按 `schemas/ope/specs/revision_notes.md` 模板产出，使用 Write 工具写入 `report/revision_notes.md`，结构：
- 内部审阅（自检清单 + 修改记录）
- 投稿准备（venue 选择 + 投稿材料清单）
- 审稿意见回复（如已有，含意见分析 + 回复草稿）
- 下一步行动

## Constraints

- 逐条回复：每个审稿意见都必须有回复，不遗漏
- 态度诚恳：感谢审稿人付出，客观回应意见
- 修改透明：说明改了哪里，方便审稿人核实
- 不过度承诺：做不到的修改要诚实说明并给替代方案
- 多轮讨论：用 AskUserQuestion 与用户确认修改策略
- 结果落盘到 `report/revision_notes.md`
