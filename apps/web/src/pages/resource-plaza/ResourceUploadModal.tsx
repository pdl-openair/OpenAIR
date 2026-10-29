import { useEffect, useState } from "react";
import { Modal, Radio } from "antd";
import { Bot, GitBranch, WandSparkles } from "lucide-react";
import SkillUploadForm from "../../components/SkillUploadForm";

type UploadResourceKind = "agent" | "skill" | "workflow";

const KIND_OPTIONS: Array<{
  value: UploadResourceKind;
  label: string;
  icon: typeof Bot;
}> = [
  { value: "agent", label: "智能体", icon: Bot },
  { value: "skill", label: "技能", icon: WandSparkles },
  { value: "workflow", label: "工作流", icon: GitBranch },
];

const UNSUPPORTED_COPY: Record<Exclude<UploadResourceKind, "skill">, string> = {
  agent: "智能体上传暂不支持，请先选择技能。",
  workflow: "工作流上传暂不支持，请先选择技能。",
};

type ResourceUploadModalProps = {
  open: boolean;
  onClose: () => void;
};

/**
 * 上传资源：先选类型（默认技能）；技能直接展示上传表单，智能体/工作流暂不支持。
 */
export default function ResourceUploadModal({ open, onClose }: ResourceUploadModalProps) {
  const [kind, setKind] = useState<UploadResourceKind>("skill");
  const [formKey, setFormKey] = useState(0);

  useEffect(() => {
    if (open) {
      setKind("skill");
      setFormKey(value => value + 1);
    }
  }, [open]);

  return (
    <Modal
      title="上传资源"
      open={open}
      onCancel={onClose}
      footer={null}
      width={560}
      destroyOnClose
      data-testid="resource-upload-modal"
    >
      <section className="mb-4" aria-label="资源类型">
        <p className="mb-2 mt-0 text-[13px] font-medium text-text">选择资源类型</p>
        <Radio.Group
          value={kind}
          onChange={event => setKind(event.target.value as UploadResourceKind)}
          optionType="button"
          buttonStyle="solid"
        >
          {KIND_OPTIONS.map(option => {
            const Icon = option.icon;
            return (
              <Radio.Button
                key={option.value}
                value={option.value}
                data-testid={`resource-upload-kind-${option.value}`}
              >
                <span className="inline-flex items-center gap-1.5">
                  <Icon size={14} aria-hidden />
                  {option.label}
                </span>
              </Radio.Button>
            );
          })}
        </Radio.Group>
      </section>

      {kind === "skill" ? (
        <div
          className="rounded-[12px] border border-border bg-surface px-4 py-4"
          data-testid="resource-upload-skill-panel"
        >
          <SkillUploadForm
            key={formKey}
            onCancel={onClose}
            onSuccess={() => onClose()}
          />
        </div>
      ) : (
        <div
          className="rounded-[12px] border border-dashed border-border bg-surface-soft px-5 py-10 text-center"
          data-testid="resource-upload-unsupported"
        >
          <p className="mb-0 text-sm text-text-secondary">{UNSUPPORTED_COPY[kind]}</p>
        </div>
      )}
    </Modal>
  );
}
