import { useEffect, useMemo, useRef, useState } from "react";
import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import { Button, Input, Modal, Tooltip, Typography } from "antd";
import type { TaskPlan, TaskPlanUpdate, TaskStage } from "@openair/shared";
import { STAGE_LABELS } from "@openair/shared";

const FIXED_STAGE_ORDER: TaskStage[] = [
  "problem_modeling",
  "solution_design",
  "execution_validation",
  "insight_generation",
];

type EditablePlan = Omit<TaskPlanUpdate, "expectedRevision">;
type FieldErrors = Record<string, string>;

function editablePlanFrom(plan: TaskPlan): EditablePlan {
  const stages = new Map(plan.stages.map((stage) => [stage.stageKey, stage]));
  const semanticRevision = plan.planningSource === "research_design_v1_2";
  return {
    goalSummary: plan.goalSummary ?? "",
    understanding: { evidenceBoundaries: [...(plan.understanding?.evidenceBoundaries ?? [])] },
    clarifications: [...(plan.clarifications ?? [])],
    stages: FIXED_STAGE_ORDER.map((stageKey) => {
      const stage = stages.get(stageKey);
      const legacySubstages: Array<{
        instanceId?: string | null;
        substage: string;
        label?: string;
        action?: string;
        expectedArtifact?: string;
      }> = stage?.substages ?? (stage?.subStages ?? []).map((substage) => ({ substage }));
      return {
        stageKey,
        title: semanticRevision ? (stage?.title?.trim() || STAGE_LABELS[stageKey]) : (stage?.title ?? ""),
        description: semanticRevision ? (stage?.description?.trim() || STAGE_LABELS[stageKey]) : (stage?.description ?? ""),
        substages: legacySubstages.map((substage) => ({
          instanceId: substage.instanceId ?? null,
          substage: substage.substage,
          label: substage.label ?? "",
          action: substage.action ?? "",
          expectedArtifact: substage.expectedArtifact ?? "",
        })),
      };
    }),
  };
}

function planFingerprint(plan: EditablePlan): string {
  return JSON.stringify(plan);
}

function validatePlan(plan: EditablePlan, semanticRevision: boolean): FieldErrors {
  const errors: FieldErrors = {};
  if (!plan.goalSummary.trim()) errors.goalSummary = "请填写研究目标。";
  const validateRows = (rows: string[], prefix: string, label: string) => {
    rows.forEach((row, index) => {
      if (!row.trim()) errors[`${prefix}.${index}`] = `${label}不能为空。`;
    });
  };
  if (!semanticRevision) {
    validateRows(plan.understanding.evidenceBoundaries, "evidenceBoundaries", "证据边界");
    validateRows(plan.clarifications, "clarifications", "待澄清事项");
  }
  if (!plan.stages.some((stage) => stage.substages.length > 0)) {
    errors.substages = "整份计划至少需要一个研究步骤。";
  }
  plan.stages.forEach((stage, stageIndex) => {
    if (!semanticRevision) {
      if (!stage.title.trim()) errors[`stages.${stageIndex}.title`] = "请填写阶段标题。";
      if (!stage.description.trim()) errors[`stages.${stageIndex}.description`] = "请填写阶段说明。";
    }
    stage.substages.forEach((substage, substageIndex) => {
      if (semanticRevision) {
        if (!substage.action.trim()) errors[`stages.${stageIndex}.substages.${substageIndex}.action`] = "请填写研究工作。";
      } else {
        if (!substage.label.trim()) errors[`stages.${stageIndex}.substages.${substageIndex}.label`] = "请填写子阶段名称。";
        if (!substage.action.trim()) errors[`stages.${stageIndex}.substages.${substageIndex}.action`] = "请填写执行动作。";
        if (!substage.expectedArtifact.trim()) errors[`stages.${stageIndex}.substages.${substageIndex}.expectedArtifact`] = "请填写预期产物。";
      }
    });
  });
  return errors;
}

function taskPlanUpdateFrom(draft: EditablePlan, revision: number): TaskPlanUpdate {
  return {
    expectedRevision: revision,
    goalSummary: draft.goalSummary.trim(),
    understanding: {
      evidenceBoundaries: draft.understanding.evidenceBoundaries.map((item) => item.trim()).filter(Boolean),
    },
    clarifications: draft.clarifications.map((item) => item.trim()).filter(Boolean),
    stages: draft.stages.map((stage) => ({
      stageKey: stage.stageKey,
      title: stage.title.trim(),
      description: stage.description.trim(),
      substages: stage.substages.map((substage) => ({
        instanceId: substage.instanceId ?? null,
        substage: substage.substage,
        label: substage.label.trim(),
        action: substage.action.trim(),
        expectedArtifact: substage.expectedArtifact.trim(),
      })),
    })),
  };
}

function FieldError({ error }: { error?: string }) {
  return error ? <Typography.Text type="danger" className="text-xs">{error}</Typography.Text> : null;
}

export function DraftPlanEditorModal({
  open,
  revision,
  plan,
  saving,
  conflict,
  onCancel,
  onReloadDraft,
  onSave,
}: {
  open: boolean;
  revision: number;
  plan: TaskPlan;
  saving: boolean;
  conflict: boolean;
  onCancel: () => void;
  onReloadDraft: () => Promise<TaskPlan>;
  onSave: (update: TaskPlanUpdate) => void;
}) {
  const semanticRevision = plan.planningSource === "research_design_v1_2";
  const sourcePlan = useMemo(() => editablePlanFrom(plan), [plan]);
  const [draft, setDraft] = useState<EditablePlan>(sourcePlan);
  const [discardConfirmationOpen, setDiscardConfirmationOpen] = useState(false);
  const [reloading, setReloading] = useState(false);
  const wasOpenRef = useRef(false);

  useEffect(() => {
    const opening = open && !wasOpenRef.current;
    wasOpenRef.current = open;
    if (!opening) return;
    setDraft(sourcePlan);
    setDiscardConfirmationOpen(false);
  }, [open, sourcePlan]);

  const errors = validatePlan(draft, semanticRevision);
  const dirty = planFingerprint(draft) !== planFingerprint(sourcePlan);
  const canSave = dirty && !saving && !conflict && Object.keys(errors).length === 0;
  const updateStage = (stageIndex: number, field: "title" | "description", value: string) => {
    setDraft((current) => ({
      ...current,
      stages: current.stages.map((stage, index) => index === stageIndex ? { ...stage, [field]: value } : stage),
    }));
  };
  const updateSubstage = (
    stageIndex: number,
    substageIndex: number,
    field: "label" | "action" | "expectedArtifact",
    value: string,
  ) => {
    setDraft((current) => ({
      ...current,
      stages: current.stages.map((stage, index) => index === stageIndex
        ? {
            ...stage,
            substages: stage.substages.map((substage, subIndex) => (
              subIndex === substageIndex ? { ...substage, [field]: value } : substage
            )),
          }
        : stage),
    }));
  };
  const addResearchStep = (stageIndex: number) => {
    setDraft((current) => ({
      ...current,
      stages: current.stages.map((stage, index) => index === stageIndex
        ? {
            ...stage,
            substages: [
              ...stage.substages,
              {
                instanceId: null,
                substage: `user_step_${stage.stageKey}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
                label: "用户新增研究步骤",
                action: "",
                expectedArtifact: "由系统重新确定",
              },
            ],
          }
        : stage),
    }));
  };
  const deleteResearchStep = (stageIndex: number, substageIndex: number) => {
    setDraft((current) => ({
      ...current,
      stages: current.stages.map((stage, index) => index === stageIndex
        ? { ...stage, substages: stage.substages.filter((_, subIndex) => subIndex !== substageIndex) }
        : stage),
    }));
  };
  const updateRows = (field: "evidenceBoundaries" | "clarifications", rows: string[]) => {
    setDraft((current) => field === "evidenceBoundaries"
      ? { ...current, understanding: { evidenceBoundaries: rows } }
      : { ...current, clarifications: rows });
  };
  const requestClose = () => {
    if (saving || reloading) return;
    if (dirty) {
      setDiscardConfirmationOpen(true);
      return;
    }
    onCancel();
  };
  const reloadDraft = async () => {
    setReloading(true);
    try {
      const reloadedPlan = await onReloadDraft();
      setDraft(editablePlanFrom(reloadedPlan));
      setDiscardConfirmationOpen(false);
    } catch {
      // Parent surface already reports the reload failure. Keep the current local draft.
    } finally {
      setReloading(false);
    }
  };

  const renderRows = (rows: string[], field: "evidenceBoundaries" | "clarifications", label: string) => (
    <section className="grid gap-2">
      <div className="flex items-center justify-between gap-2">
        <Typography.Text strong>{label}</Typography.Text>
        <Tooltip title={`新增${label}`}>
          <Button
            type="text"
            size="small"
            icon={<PlusOutlined />}
            onClick={() => updateRows(field, [...rows, ""])}
            data-testid={`draft-plan-${field}-add`}
          />
        </Tooltip>
      </div>
      {rows.map((row, index) => (
        <div key={`${field}-${index}`} className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
          <div className="grid gap-1">
            <Input
              value={row}
              status={errors[`${field}.${index}`] ? "error" : undefined}
              onChange={(event) => updateRows(field, rows.map((item, rowIndex) => rowIndex === index ? event.target.value : item))}
              data-testid={`draft-plan-${field}-${index}`}
            />
            <FieldError error={errors[`${field}.${index}`]} />
          </div>
          <Tooltip title={`删除${label}`}>
            <Button
              type="text"
              danger
              icon={<DeleteOutlined />}
              onClick={() => updateRows(field, rows.filter((_, rowIndex) => rowIndex !== index))}
              data-testid={`draft-plan-${field}-delete-${index}`}
            />
          </Tooltip>
        </div>
      ))}
    </section>
  );

  return (
    <>
      <Modal
        title="编辑研究草案"
        open={open}
        width={960}
        destroyOnClose
        maskClosable={!saving && !reloading}
        closable={!saving && !reloading}
        onCancel={requestClose}
        footer={[
          <Button key="cancel" disabled={saving || reloading} onClick={requestClose}>取消</Button>,
          <Button key="save" type="primary" loading={saving} disabled={!canSave} onClick={() => onSave(taskPlanUpdateFrom(draft, revision))} data-testid="draft-plan-save">
            保存草案
          </Button>,
        ]}
      >
        <div className="flex flex-col gap-4" data-testid="draft-plan-editor">
          {conflict ? (
            <div className="flex flex-wrap items-center justify-between gap-2 border border-warning px-3 py-2">
              <Typography.Text type="warning">草案已被其他修改更新，请重新加载后再编辑。</Typography.Text>
              <Button size="small" loading={reloading} disabled={reloading || saving} onClick={() => void reloadDraft()} data-testid="draft-plan-reload">
                重新加载草案
              </Button>
            </div>
          ) : null}
          <section className="grid gap-2">
            <Typography.Text strong>研究目标</Typography.Text>
            <Input.TextArea
              value={draft.goalSummary}
              status={errors.goalSummary ? "error" : undefined}
              onChange={(event) => setDraft((current) => ({ ...current, goalSummary: event.target.value }))}
              autoSize={{ minRows: 2, maxRows: 5 }}
              data-testid="draft-plan-goal-summary"
            />
            <FieldError error={errors.goalSummary} />
          </section>
          {!semanticRevision ? renderRows(draft.understanding.evidenceBoundaries, "evidenceBoundaries", "证据边界") : null}
          {!semanticRevision ? renderRows(draft.clarifications, "clarifications", "待澄清事项") : null}
          <FieldError error={errors.substages} />
          {draft.stages.map((stage, stageIndex) => (
            <section key={stage.stageKey} className="grid gap-3 rounded-lg border border-border p-3" data-testid={`draft-plan-stage-${stageIndex}`}>
              <div className="flex items-center justify-between gap-2">
                <Typography.Text type="secondary">{STAGE_LABELS[stage.stageKey]}</Typography.Text>
                {semanticRevision ? (
                  <Button
                    type="text"
                    size="small"
                    icon={<PlusOutlined />}
                    onClick={() => addResearchStep(stageIndex)}
                    data-testid={`draft-plan-step-add-${stageIndex}`}
                  >
                    添加研究步骤
                  </Button>
                ) : null}
              </div>
              {!semanticRevision ? (
                <>
                  <label className="grid gap-1 text-sm text-text-secondary">
                    阶段标题
                    <Input value={stage.title} status={errors[`stages.${stageIndex}.title`] ? "error" : undefined} onChange={(event) => updateStage(stageIndex, "title", event.target.value)} data-testid={`draft-plan-stage-title-${stageIndex}`} />
                    <FieldError error={errors[`stages.${stageIndex}.title`]} />
                  </label>
                  <label className="grid gap-1 text-sm text-text-secondary">
                    阶段说明
                    <Input.TextArea value={stage.description} status={errors[`stages.${stageIndex}.description`] ? "error" : undefined} onChange={(event) => updateStage(stageIndex, "description", event.target.value)} autoSize={{ minRows: 2, maxRows: 5 }} data-testid={`draft-plan-stage-description-${stageIndex}`} />
                    <FieldError error={errors[`stages.${stageIndex}.description`]} />
                  </label>
                </>
              ) : null}
              <FieldError error={errors[`stages.${stageIndex}.substages`]} />
              {stage.substages.map((substage, substageIndex) => (
                <div key={substage.instanceId ?? substage.substage} className="grid gap-2 border-t border-border pt-3">
                  {semanticRevision ? (
                    <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
                      <label className="grid gap-1 text-sm text-text-secondary">
                        研究工作
                        <Input.TextArea
                          value={substage.action}
                          status={errors[`stages.${stageIndex}.substages.${substageIndex}.action`] ? "error" : undefined}
                          onChange={(event) => updateSubstage(stageIndex, substageIndex, "action", event.target.value)}
                          autoSize={{ minRows: 2, maxRows: 5 }}
                          data-testid={`draft-plan-research-step-${stageIndex}-${substageIndex}`}
                        />
                        <FieldError error={errors[`stages.${stageIndex}.substages.${substageIndex}.action`]} />
                      </label>
                      <Tooltip title="删除研究步骤">
                        <Button
                          type="text"
                          danger
                          icon={<DeleteOutlined />}
                          onClick={() => deleteResearchStep(stageIndex, substageIndex)}
                          data-testid={`draft-plan-step-delete-${stageIndex}-${substageIndex}`}
                        />
                      </Tooltip>
                    </div>
                  ) : (
                    <>
                      <label className="grid gap-1 text-sm text-text-secondary">
                        子阶段名称
                        <Input value={substage.label} status={errors[`stages.${stageIndex}.substages.${substageIndex}.label`] ? "error" : undefined} onChange={(event) => updateSubstage(stageIndex, substageIndex, "label", event.target.value)} data-testid={`draft-plan-substage-label-${stageIndex}-${substageIndex}`} />
                        <FieldError error={errors[`stages.${stageIndex}.substages.${substageIndex}.label`]} />
                      </label>
                      <label className="grid gap-1 text-sm text-text-secondary">
                        执行动作
                        <Input.TextArea value={substage.action} status={errors[`stages.${stageIndex}.substages.${substageIndex}.action`] ? "error" : undefined} onChange={(event) => updateSubstage(stageIndex, substageIndex, "action", event.target.value)} autoSize={{ minRows: 2, maxRows: 4 }} data-testid={`draft-plan-substage-action-${stageIndex}-${substageIndex}`} />
                        <FieldError error={errors[`stages.${stageIndex}.substages.${substageIndex}.action`]} />
                      </label>
                      <label className="grid gap-1 text-sm text-text-secondary">
                        预期产物
                        <Input value={substage.expectedArtifact} status={errors[`stages.${stageIndex}.substages.${substageIndex}.expectedArtifact`] ? "error" : undefined} onChange={(event) => updateSubstage(stageIndex, substageIndex, "expectedArtifact", event.target.value)} data-testid={`draft-plan-substage-artifact-${stageIndex}-${substageIndex}`} />
                        <FieldError error={errors[`stages.${stageIndex}.substages.${substageIndex}.expectedArtifact`]} />
                      </label>
                    </>
                  )}
                </div>
              ))}
            </section>
          ))}
        </div>
      </Modal>
      <Modal
        title="放弃未保存的修改？"
        open={discardConfirmationOpen}
        onCancel={() => setDiscardConfirmationOpen(false)}
        onOk={() => {
          setDiscardConfirmationOpen(false);
          onCancel();
        }}
        okText="放弃修改"
        cancelText="继续编辑"
      >
        <Typography.Paragraph className="m-0">关闭后，本次未保存的草案修改将丢失。</Typography.Paragraph>
      </Modal>
    </>
  );
}
