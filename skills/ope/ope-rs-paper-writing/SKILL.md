---
name: ope-rs-paper-writing
description: 当用户想要撰写或润色 OPE 学术论文时自动触发。常见场景：用户说"写论文"、"润色论文"、"撰写引言"、"写方法部分"、"用 LaTeX 写论文"。本 skill 撰写引言/方法/结果/讨论各章节，支持 LaTeX 与 Markdown 两种格式，不负责内部审阅与投稿。
---

# 论文撰写（L3 — OPE）

撰写学术论文各章节，将 OPE 实验结果转化为完整论文。支持 LaTeX 与 Markdown。

## Method

### Step 0：确认目标 venue 与模板
用 AskUserQuestion 询问：
- 目标会议/期刊（NeurIPS / ICML / ICLR / KDD / WWW / IEEE 系列等）
- 输出格式偏好（LaTeX 推荐 / Markdown 快速预览）

模板选择：项目 `references/templates/` 下已有的优先用；没有的提示用户获取官方模板（IEEE / NeurIPS / ICML / ICLR 官方下载链接）。

### 子任务 1：引言撰写
**倒三角结构**：
- 广泛背景（OPE/离线 RL 在决策智能中的重要性）
- 逐步收窄（现有 OPE 估计器的不足：IPS 高方差、DM 高偏差、DR 在小样本下退化）
- 核心问题（具体研究空白）
- 本文贡献（新估计器/新分析/新基准）

### 子任务 2：方法部分撰写
- 问题定义（形式化描述：行为策略 π_b、目标策略 π_e、轨迹数据 D）
- 预备知识（IPS / DR / FQE 等基线方法）
- 本文方法（创新点的数学表述 + 算法伪代码）
- 实验设置（数据集、超参数、评估协议）

LaTeX 示例：
```latex
\section{Methodology}
\subsection{Problem Formulation}
Given a dataset $\mathcal{D} = \{(s_t, a_t, r_t, s_{t+1})\}_{t=1}^{N}$ collected by
behavior policy $\pi_b$, we aim to estimate
$J(\pi_e) = \mathbb{E}_{\pi_e}[\sum_t \gamma^t r_t]$.

\subsection{Our Estimator}
...
```

### 子任务 3：结果部分撰写
- 主要结果（假说验证 + 方法对比表）
- 消融实验
- 敏感度分析（如样本量、行为策略覆盖度）
- 统计显著性

LaTeX 表格示例：
```latex
\begin{tabular}{lccc}
\toprule
Method & Hopper & HalfCheetah & Walker2d \\
\midrule
IPS & $12.3 \pm 0.5$ & $8.1 \pm 0.3$ & $9.2 \pm 0.4$ \\
DR  & $10.1 \pm 0.4$ & $7.2 \pm 0.2$ & $8.0 \pm 0.3$ \\
\textbf{Ours} & $\mathbf{7.5 \pm 0.3}$ & $\mathbf{5.9 \pm 0.2}$ & $\mathbf{6.4 \pm 0.3}$ \\
\bottomrule
\end{tabular}
```

### 子任务 4：讨论部分撰写
**正三角结构**：总结主要发现 → 与已有工作对比 → 理论/实践意义 → 局限性与未来工作。

### LaTeX 环境配置与编译
- Windows：MiKTeX + TeXstudio
- macOS：MacTeX（`brew install --cask mactex`）
- Linux：TeX Live（`sudo apt-get install texlive-full`）
- 编译：`pdflatex paper.tex && bibtex paper && pdflatex paper.tex && pdflatex paper.tex`
- 或用 Overleaf 在线编译

## Input

- 图表与统计数据（来自 `ope-rs-data-visualization` 的 `report/visualization.md`）
- 假说列表（来自 `ope-rs-hypothesis-construction`）
- 方法设计（来自 `ope-rs-experiment-design`）
- 目标 venue 与格式偏好

## Output

按 `schemas/ope/specs/paper.md` 模板产出，使用 Write 工具写入 `report/paper.tex`（或 `report/paper.md`），结构：
- 元信息（venue、标题、作者、格式、模板）
- Abstract
- Introduction / Related Work / Method / Experiments / Conclusion
- 图表引用清单
- 下一步行动

## Constraints

- **引用真实性硬约束**：禁止虚构引用！所有引用必须用 WebSearch/WebFetch 验证存在；作者格式准确、年份准确、内容匹配上下文
- 模板优先：优先使用目标 venue 官方 LaTeX 模板
- 可复现性优先：方法部分足够详细，读者可复现
- 结果客观描述：避免过度解读，用数据说话
- 数学表达规范：变量斜体、常量正体、公式编号右对齐
- 多轮讨论：用 AskUserQuestion 确认各章节重点
- 结果落盘到 `report/paper.tex`
