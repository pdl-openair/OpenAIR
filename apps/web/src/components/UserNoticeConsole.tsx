import { Badge, Button, Popover, Typography } from "antd";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  InfoCircleOutlined,
  LoadingOutlined,
  WarningOutlined
} from "@ant-design/icons";
import { Bell } from "lucide-react";
import { cn } from "../lib/cn";
import { formatElapsedSeconds } from "../lib/researchStartWaiting";
import { useUserNotice, type UserNotice, type UserNoticeLevel } from "../context/UserNoticeContext";

const LEVEL_ICON: Record<UserNoticeLevel, typeof InfoCircleOutlined> = {
  progress: LoadingOutlined,
  info: InfoCircleOutlined,
  warning: WarningOutlined,
  error: CloseCircleOutlined,
  success: CheckCircleOutlined
};

const LEVEL_CLASS: Record<UserNoticeLevel, string> = {
  progress: "text-brand",
  info: "text-brand",
  warning: "text-warning",
  error: "text-danger",
  success: "text-success"
};

function formatUpdatedAt(value: number): string {
  return new Date(value).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function NoticeItem({ notice, onDismiss }: { notice: UserNotice; onDismiss: (id: string) => void }) {
  const Icon = LEVEL_ICON[notice.level];
  const elapsed = formatElapsedSeconds(notice.elapsedSeconds);
  return (
    <article
      className={cn(
        "group relative px-3 py-2.5",
        notice.level === "error"
          ? "bg-danger-soft/40"
          : notice.level === "warning"
            ? "bg-warning-soft/40"
            : notice.status === "active" && "bg-info-soft/50",
      )}
      data-testid={`user-notice-item-${notice.id}`}
      data-notice-level={notice.level}
      data-notice-status={notice.status}
    >
      <div className="flex items-start gap-2">
        <Icon className={cn("mt-0.5 shrink-0 text-sm", LEVEL_CLASS[notice.level])} />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <Typography.Text strong className="text-[13px]! leading-5! text-shell-text-strong!">
              {notice.title}
            </Typography.Text>
            <Button
              type="text"
              size="small"
              className="h-6! w-6! min-w-6! p-0! text-shell-text-secondary! opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
              aria-label="忽略这条提示"
              onClick={() => onDismiss(notice.id)}
            >
              ×
            </Button>
          </div>
          <Typography.Paragraph className="mb-0! mt-0.5! text-[13px]! leading-5! text-shell-text-secondary!">
            {notice.message}
          </Typography.Paragraph>
          {notice.nextActionHint ? (
            <Typography.Paragraph className="mb-0! mt-1! text-[12px]! leading-5! text-shell-text-secondary!">
              {notice.nextActionHint}
            </Typography.Paragraph>
          ) : null}
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 text-[11px] text-shell-text-secondary">
            {elapsed ? <span>{elapsed}</span> : null}
            <span>{formatUpdatedAt(notice.updatedAt)}</span>
          </div>
        </div>
      </div>
    </article>
  );
}

function UserNoticePanel() {
  const { notices, dismissNotice, clearNotices } = useUserNotice();
  return (
    <div
      className="flex w-[360px] max-w-[calc(100vw-24px)] flex-col"
      data-testid="user-notice-console"
    >
      <header className="flex items-center justify-between gap-3 border-b border-border px-3 py-2.5">
        <Typography.Text strong className="text-[13px]! text-shell-text-strong!">
          系统提示
        </Typography.Text>
        <Button
          type="link"
          size="small"
          className="h-auto! px-0! text-[12px]!"
          disabled={notices.length === 0}
          data-testid="user-notice-clear-all"
          onClick={clearNotices}
        >
          清空全部
        </Button>
      </header>
      {notices.length === 0 ? (
        <div className="px-3 py-8 text-center text-[13px] leading-5 text-shell-text-secondary">
          暂无系统提示。开始项目或出现错误时，进度和说明会显示在这里。
        </div>
      ) : (
        <div className="max-h-[min(420px,70vh)] divide-y divide-border overflow-y-auto">
          {notices.map(notice => (
            <NoticeItem key={notice.id} notice={notice} onDismiss={dismissNotice} />
          ))}
        </div>
      )}
    </div>
  );
}

export function UserNoticeTrigger() {
  const { unreadCount, activeCount, panelOpen, setPanelOpen } = useUserNotice();
  const badgeCount = unreadCount || activeCount;
  return (
    <Popover
      trigger="click"
      open={panelOpen}
      onOpenChange={setPanelOpen}
      placement="bottomRight"
      arrow={false}
      content={<UserNoticePanel />}
      getPopupContainer={() => document.body}
      styles={{ content: { padding: 0, overflow: "hidden" } }}
    >
      <span className="inline-flex">
        <Badge count={badgeCount} size="small" offset={[-2, 2]} color={unreadCount ? undefined : "blue"}>
          <Button
            type="text"
            aria-label="系统提示"
            aria-expanded={panelOpen}
            data-testid="user-notice-trigger"
            icon={<Bell size={16} strokeWidth={1.8} aria-hidden="true" />}
            className="h-8! w-8! min-w-8! text-shell-text-secondary hover:text-text [&_.anticon]:inline-flex [&_svg]:h-4 [&_svg]:w-4"
          />
        </Badge>
      </span>
    </Popover>
  );
}
