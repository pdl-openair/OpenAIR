---
name: characterization
description: 对合成实验产物做物相表征（XRD/PDF 谱图模型训练与推理），给出相定量、拟合度与置信度，必要时建议重训模型或补充负样本。
---

# Characterization（L2 编排）

## 职责

对 `synthesis-execution` 的产物做物相表征，给出相定量、拟合度、置信度，必要时建议重训或换负样本，按 spec 产出表征报告（materials pack 为 `xrd_report.md`）。

本 L2 是**域无关**编排：表征手段（XRD / NMR / Raman / 其他）、训练框架、谱图解析由 L3 实现。L2 只负责：上游产物消费、训练/推理流程编排、相定量报告格式化、重训建议生成。

## 执行步骤

1. 读取上游 `synthesis_log.md`，确定 sample_id 与产物
2. 调用域特定 L3（materials pack 调用 `materials-auto-xrd`）训练模型并做推理
3. 解析 phase identification 与拟合度
4. 若主相不纯，给出 recommended_actions（重训 / 换负样本 / 补相）
5. 按 spec（`schemas/materials/specs/characterization.md`）产出表征报告

## 产出

见 spec。output_file = `xrd_report.md`（materials pack 约定）。
