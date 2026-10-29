---
name: cs-paper-source-resolution
description: 为 CS 论文复现确认可追溯的代码来源；优先保留用户给出的代码身份，其次只接受已声明来源页、主论文作者自述或模型卡中的唯一直接源码链接。
---

# CS 复现正式来源确认

## 做什么

输入是冻结的研究目标，以及其中已明确给出的 repository / revision（如有）。

1. 用户已经给出 repository 时，保留该选择并进行一次真实 remote 可达性检查。
2. 用户给出了可访问的论文、项目页或模型卡地址时，只读取该已声明页面中的直接 GitHub 代码链接。
3. 未给出来源页时，用研究目标中明确出现的专有方法标签，经 **OpenAIR 共享多源文献 discovery** 定位主论文身份；再经 **共享 access readiness / content-form access** 按 metadata → landing/HTML → PDF 的顺序读取足够内容。只有唯一标题起始精确匹配的论文，且其中作者直接声明唯一 GitHub 链接时，才可输出 repository；论文标题相似不构成代码来源。
4. 任一 source / access route 失败只记为局部 access fact 与 provenance，不把整次 source-resolution 判失败；仅当全部可信路径仍不足以确定必要事实时，返回明确的 incomplete / unresolved 来源状态。
5. 若论文路径未能确认代码来源，再从 checkpoint 标识检索 Hugging Face 公开模型；只在唯一精确匹配的模型卡中读取直接写出的 GitHub 代码链接。模型卡的发布者身份不会被本能力擅自写成“官方”。
6. 只在代码链接唯一时输出 repository；未指定 revision 时以 `HEAD` 作为本次**系统选择的执行入口**，由下游 repository revision acquisition 固定为实际 commit。

## 不做什么

- 不根据 GitHub 名称、描述、作者名或热度猜测仓库。
- 不把论文参考文献或普通 GitHub 引用当作论文作者的代码声明。
- 不把模型搜索结果本身写成“官方代码”。
- 不解释论文、参考指标、实验条件或比较判据。
- 不把某一个具体 PDF URL 的成败当作 source-resolution 的成败。
- 找不到可信代码来源时，不要求用户填写内部字段；返回可见的不完整来源结果。
