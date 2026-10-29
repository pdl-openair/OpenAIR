import { Typography } from "antd";
import type { ResearchStartDisplayContext } from "../../api/researchStartDisplayContext";
import { readResearchStartPendingForSession } from "../../lib/userRouteState";

export function WorkbenchOriginalInput({
  taskId,
  sessionId,
  context: visibleStartContext,
}: {
  taskId: string;
  sessionId: string | null;
  context: {
    status: "ready" | "unavailable";
    value: ResearchStartDisplayContext | null;
  } | null;
}) {
  const pendingInput = readResearchStartPendingForSession(
    sessionId ?? undefined,
  );
  const pendingMatchesTask =
    (pendingInput?.reservedTaskId ?? pendingInput?.taskId) === taskId;
  const originalInput =
    visibleStartContext?.value?.originalInput ??
    (visibleStartContext?.status !== "ready" && pendingMatchesTask
      ? pendingInput?.input
      : null);
  const originalInputNotice =
    visibleStartContext?.status === "ready"
      ? "该历史版本未保存原始输入。"
      : visibleStartContext?.status === "unavailable"
        ? "原始输入暂时无法读取。"
        : "正在读取原始输入。";
  return (
    <>
      {originalInput != null && originalInput !== "" ? (
        <details
          key={taskId}
          className="mb-3 text-sm text-text-secondary [&[open]_.original-input-preview]:hidden"
          data-testid="workbench-original-input"
        >
          <summary className="cursor-pointer">
            原始输入
            <span className="original-input-preview ml-2 inline-block max-w-[75%] truncate align-bottom">
              {originalInput}
            </span>
          </summary>
          <Typography.Paragraph className="m-0! mt-2! whitespace-pre-wrap leading-6!">
            {originalInput}
          </Typography.Paragraph>
        </details>
      ) : (
        <Typography.Paragraph
          className="m-0 mb-3! text-sm! text-text-secondary"
          data-testid="workbench-original-input-notice"
        >
          原始输入：{originalInputNotice}
        </Typography.Paragraph>
      )}
    </>
  );
}
