import { useRef, useState } from "react";
import { Button, Input, List, Space, Tooltip, Typography, message } from "antd";
import { DeleteOutlined, FolderOpenOutlined, QuestionCircleOutlined, UploadOutlined } from "@ant-design/icons";
import { uploadUserSkill, type SkillUploadEntry } from "../api/skills";

const { Text, Paragraph } = Typography;

const SKILL_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;

/** Keep in sync with services/api/app/config.py default OPENAIR_USER_SKILL_MAX_FILES. */
const USER_SKILL_MAX_FILES = 200;

const SKIP_PATH_SEGMENTS = new Set([
  "__pycache__",
  "node_modules",
  "venv",
  ".venv",
  "dist",
  "build",
  "egg-info",
  ".pytest_cache",
  ".mypy_cache",
  ".ruff_cache",
  ".tox",
  ".eggs"
]);

function shouldSkipRelPath(relPath: string): boolean {
  const segments = relPath.split(/[/\\]/).filter(Boolean);
  return segments.some(segment => segment.startsWith(".") || SKIP_PATH_SEGMENTS.has(segment));
}

type SkillUploadFormProps = {
  onCancel?: () => void;
  onSuccess?: (skillName: string) => void;
  /** 嵌入外层弹窗时不自带底栏；由外层 footer 控制时传 false */
  showActions?: boolean;
};

/**
 * 私有技能上传表单：名称、选目录/文件、提交。
 * 供「上传资源」弹窗与 SkillManager 复用。
 */
export default function SkillUploadForm({ onCancel, onSuccess, showActions = true }: SkillUploadFormProps) {
  const [uploading, setUploading] = useState(false);
  const [pending, setPending] = useState<SkillUploadEntry[]>([]);
  const [skillName, setSkillName] = useState("");
  const dirInputRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const addFiles = (list: FileList | null, fromDir: boolean) => {
    if (!list || list.length === 0) return;
    const entries: SkillUploadEntry[] = [];
    let topFolder = "";
    let skipped = 0;
    for (const file of Array.from(list)) {
      const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath || "";
      let relPath: string;
      if (fromDir && rel.includes("/")) {
        const segs = rel.split("/");
        topFolder = topFolder || segs[0];
        relPath = segs.slice(1).join("/");
      } else {
        relPath = file.name;
      }
      if (!relPath) continue;
      if (shouldSkipRelPath(relPath)) {
        skipped += 1;
        continue;
      }
      entries.push({ file, relPath });
    }
    if (skipped > 0) {
      message.info(`已自动跳过 ${skipped} 个无关文件（如 __pycache__、node_modules）`);
    }
    if (entries.length) {
      setPending(previous => {
        const byPath = new Map(previous.map(entry => [entry.relPath, entry]));
        for (const entry of entries) byPath.set(entry.relPath, entry);
        return Array.from(byPath.values());
      });
    }
    if (fromDir && topFolder && !skillName.trim()) setSkillName(topFolder);
  };

  const handleUpload = async () => {
    if (pending.length === 0) {
      message.warning("请至少选择一个文件（必须包含根目录 SKILL.md）");
      return;
    }
    if (pending.length > USER_SKILL_MAX_FILES) {
      message.error(
        `文件过多（${pending.length} > 上限 ${USER_SKILL_MAX_FILES}）。请精简技能目录，或在服务端设置 OPENAIR_USER_SKILL_MAX_FILES。`
      );
      return;
    }
    if (!pending.some(entry => entry.relPath.toLowerCase() === "skill.md")) {
      message.error("必须包含 SKILL.md（位于技能根目录）");
      return;
    }
    const name = skillName.trim();
    if (!SKILL_NAME_RE.test(name)) {
      message.error("技能名称不合法：仅限 1-64 位字母、数字、下划线或连字符，且首字符须为字母或数字");
      return;
    }

    setUploading(true);
    try {
      await uploadUserSkill(name, pending);
      message.success(`技能“${name}”上传成功，当前仅你可用`);
      setPending([]);
      setSkillName("");
      onSuccess?.(name);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "上传失败");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div data-testid="skill-upload-form">
      <Paragraph type="secondary" className="mb-3! bg-surface-soft p-2 rounded-md">
        请选择完整技能目录并保留 scripts/、schemas/ 等子目录结构。根目录必须包含
        SKILL.md；上传后默认私有可用。会自动跳过 __pycache__、node_modules 等无关目录。
      </Paragraph>
      <div className="mb-3 space-y-2!">
        <div className="mb-1 flex items-center gap-1">
          <Text strong>技能名称</Text>
          <Tooltip title="仅限 1-64 位字母/数字/下划线/连字符，首字符须为字母或数字。">
            <QuestionCircleOutlined className="cursor-help text-xs text-text-muted" aria-label="命名规则说明" />
          </Tooltip>
        </div>
        <Input
          value={skillName}
          onChange={event => setSkillName(event.target.value)}
          placeholder="例如：my-literature-review（选目录时自动填入）"
        />
      </div>
      <Space className="mb-3">
        <Button icon={<FolderOpenOutlined />} onClick={() => dirInputRef.current?.click()}>
          选择目录
        </Button>
        <Button icon={<UploadOutlined />} onClick={() => fileInputRef.current?.click()}>
          选择文件
        </Button>
      </Space>
      <input
        ref={dirInputRef}
        type="file"
        multiple
        title="选择技能目录"
        className="hidden"
        {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
        onChange={event => {
          addFiles(event.target.files, true);
          event.target.value = "";
        }}
      />
      <input
        ref={fileInputRef}
        type="file"
        multiple
        title="选择技能文件"
        className="hidden"
        onChange={event => {
          addFiles(event.target.files, false);
          event.target.value = "";
        }}
      />
      {pending.length > 0 ? (
        <>
          <Text type={pending.length > USER_SKILL_MAX_FILES ? "danger" : "secondary"} className="mb-2 block text-xs">
            已选 {pending.length} 个文件（上限 {USER_SKILL_MAX_FILES}）
          </Text>
          <List
            size="small"
            bordered
            className="mb-3"
            dataSource={pending}
            renderItem={(item, index) => (
              <List.Item
                actions={[
                  <Button
                    key="rm"
                    type="text"
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    onClick={() => setPending(previous => previous.filter((_, i) => i !== index))}
                  >
                    移除
                  </Button>
                ]}
              >
                <Text code className="text-xs">
                  {item.relPath}
                </Text>
              </List.Item>
            )}
          />
        </>
      ) : null}
      {showActions ? (
        <div className="mt-4 flex justify-end gap-2">
          {onCancel ? <Button onClick={onCancel}>取消</Button> : null}
          <Button type="primary" loading={uploading} onClick={() => void handleUpload()}>
            上传
          </Button>
        </div>
      ) : null}
    </div>
  );
}
