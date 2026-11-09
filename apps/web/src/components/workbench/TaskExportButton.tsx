import { useState } from "react";
import { Button, Typography, message } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { exportTaskArchive } from "../../api/client";
import { userFacingErrorText } from "../../lib/userFacingLanguage";

/** 导出整个研究任务：后端把全部产物打包为 zip（含 manifest.json）后触发浏览器下载。 */
export function TaskExportButton({
  taskId,
  size = "small",
  label = "导出项目",
}: {
  taskId: string;
  size?: "small" | "middle";
  label?: string;
}) {
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);
  const download = async () => {
    if (!taskId || pending) return;
    setPending(true);
    setFailed(false);
    try {
      await exportTaskArchive(taskId);
    } catch (error) {
      setFailed(true);
      message.error(
        userFacingErrorText(
          error instanceof Error ? error.message : String(error),
          "导出暂未完成，请稍后重试。",
        ),
      );
    } finally {
      setPending(false);
    }
  };
  return (
    <span className="inline-flex flex-col items-end gap-1">
      <Button
        size={size}
        icon={<DownloadOutlined />}
        loading={pending}
        onClick={() => void download()}
        data-testid="task-export-zip"
      >
        {label}
      </Button>
      {failed ? (
        <Typography.Text role="status" type="secondary" className="text-xs">
          导出暂未完成，已保存成果仍保留。
        </Typography.Text>
      ) : null}
    </span>
  );
}
