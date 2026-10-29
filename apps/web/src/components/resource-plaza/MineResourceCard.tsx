import { Button, Tag, Tooltip, Typography } from "antd";
import { Bot, FileText, GitBranch, Heart, WandSparkles } from "lucide-react";
import { cn } from "../../lib/cn";
import type { ResourcePlazaResource } from "../../features/resource-plaza/types";

export type MineSegment = "saved" | "uploaded";

export type MineCardItem = {
  id: string;
  segment: MineSegment;
  title: string;
  description: string;
  typeLabel: string;
  sourceLabel: string;
  updatedAt: string | null;
  kind?: ResourcePlazaResource["kind"];
  href?: string;
  resourceId?: string;
  skillName?: string;
  files?: string[];
  tags?: string[];
};

const KIND_SHELL: Record<ResourcePlazaResource["kind"], string> = {
  agent: "bg-info-soft text-brand",
  skill: "bg-brand-soft text-brand",
  workflow: "bg-accent-soft text-brand"
};

const KindIcon = {
  agent: Bot,
  skill: WandSparkles,
  workflow: GitBranch
} as const;

function formatUpdatedAt(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function MineResourceCard({
  item,
  saved,
  savePending,
  onView,
  onToggleSave
}: {
  item: MineCardItem;
  saved?: boolean;
  savePending?: boolean;
  onView: () => void;
  onToggleSave?: () => void;
}) {
  const Icon = item.kind ? KindIcon[item.kind] : FileText;
  const shell = item.kind ? KIND_SHELL[item.kind] : "bg-brand-soft text-brand";
  const displayTags = [item.typeLabel, ...(item.tags ?? [])];

  return (
    <article
      role="button"
      tabIndex={0}
      onClick={onView}
      onKeyDown={event => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onView();
        }
      }}
      className={cn(
        "flex h-full w-full cursor-pointer flex-col gap-3 rounded-[12px] border border-border bg-surface-soft p-4 text-left",
        "transition-colors hover:border-brand focus-visible:outline focus-visible:outline-offset-2 focus-visible:outline-brand"
      )}
      data-testid={`resource-mine-card-${item.id}`}
    >
      <div className="flex items-start gap-3">
        <span
          className={cn("inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-[10px]", shell)}
          aria-hidden
        >
          <Icon size={20} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Typography.Title
              level={5}
              ellipsis
              title={item.title}
              className="m-0! min-w-0 flex-1 line-clamp-1 text-[16px]! leading-6! text-text!"
            >
              {item.title}
            </Typography.Title>
            {onToggleSave ? (
              <Tooltip title={savePending ? "处理中" : saved ? "取消收藏" : "收藏"}>
                <span
                  className="group/save inline-flex shrink-0"
                  onClick={event => event.stopPropagation()}
                  onKeyDown={event => event.stopPropagation()}
                >
                  <Button
                    type="text"
                    shape="circle"
                    className="text-text-secondary hover:bg-brand-soft! hover:text-brand!"
                    aria-label={savePending ? "正在处理收藏" : saved ? "取消收藏" : "收藏"}
                    icon={
                      <Heart
                        size={16}
                        className={cn(
                          "transition-colors",
                          saved
                            ? "fill-current text-brand group-hover/save:text-brand"
                            : "text-current group-hover/save:fill-current group-hover/save:text-brand"
                        )}
                      />
                    }
                    loading={savePending}
                    disabled={savePending}
                    onClick={event => {
                      event.stopPropagation();
                      onToggleSave();
                    }}
                  />
                </span>
              </Tooltip>
            ) : null}
          </div>
          <Typography.Paragraph
            ellipsis={{ rows: 2 }}
            title={item.description || "未提供资源说明"}
            className="mb-0! mt-1.5! h-10! line-clamp-2 text-[13px]! leading-5! text-text-secondary!"
          >
            {item.description || "未提供资源说明"}
          </Typography.Paragraph>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {displayTags.map(tag => (
          <Tag key={tag} className="m-0! rounded-md border-0 bg-surface px-2 text-[11px] text-text-secondary">
            {tag}
          </Tag>
        ))}
        {item.updatedAt ? (
          <Tag className="m-0! rounded-md border-0 bg-surface px-2 text-[11px] text-text-secondary">
            更新 {formatUpdatedAt(item.updatedAt)}
          </Tag>
        ) : null}
      </div>
    </article>
  );
}
