# Spec — XRD Characterization（XRD 物相自动表征）

<!-- Substage: characterization
     Stage: insight_generation
     L2 skill: characterization
     L3 skills: materials-auto-xrd -->

## 目标

基于化学式 / CIF / MP ID 自动训练 XRD/PDF 模型并对实测谱图做推理，**鉴定 `synthesis_execution` 产物的物相组成**，给出相定量与置信度。

## 输入

- 产物 CIF / 化学式 / MP ID（来自 `synthesis_execution`）
- 实测 XRD 谱图（原始数据路径）
- 参考相（可选，用于混合训练）

## 产出物（output_file: `xrd_report.md`）

| 字段 | 必填 | 说明 |
|---|---|---|
| sample_id | ✅ | 样品 ID（关联 batch_id） |
| model_training | ✅ | 训练配置 + 训练状态 |
| phase_identification[] | ✅ | 鉴定相列表（相名/空间群/质量分数/匹配度） |
| residual | ✅ | 残差与拟合度（Rwp 等） |
| confidence | ✅ | 总置信度与不确定性说明 |
| recommended_actions | ⭑ | 若主相不纯，下一步建议（重训/换负样本/补相） |

## 验收标准

- [ ] sample_id 与 batch_id 可追溯
- [ ] phase_identification 给出每相的质量分数
- [ ] residual 给出拟合度数值（Rwp/GoF）
- [ ] confidence 给出不确定性来源（至少一条）
- [ ] 若主相纯度 < 阈值，recommended_actions 含可执行建议

## 与上下游的衔接

- **上游**：`synthesis_execution`（实物样品）
- **下游**：反馈至 `candidate_generation`（生成的新相是否真的被合成出来 → Pareto 前沿再评估）
