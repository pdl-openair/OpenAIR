import { Bookmark, BookmarkCheck, LoaderCircle } from "lucide-react";
import { Button, Card, Tag, Tooltip, Typography } from "antd";
import { cn } from "../../lib/cn";
import {
  resourceAvailabilityGuidance,
  resourceAvailabilityPresentation,
} from "../../features/resource-plaza/state";
import type { ResourcePlazaResource } from "../../features/resource-plaza/types";

export interface ResourceCardProps {
  resource: ResourcePlazaResource;
  variant?: "featured" | "catalog";
  saved?: boolean;
  savePending?: boolean;
  className?: string;
  onOpen?: (resource: ResourcePlazaResource) => void;
  onPrimaryAction?: (resource: ResourcePlazaResource) => void;
  onSave?: (resource: ResourcePlazaResource, nextSaved: boolean) => void;
  onEvaluateFit?: (resource: ResourcePlazaResource) => void;
}

export function ResourceCard({
  resource,
  variant = "featured",
  saved = false,
  savePending = false,
  className,
  onOpen,
  onPrimaryAction,
  onSave,
  onEvaluateFit,
}: ResourceCardProps) {
  const presentation = resourceAvailabilityPresentation(resource.availability, resource.safetyRisk === "execution_blocked");
  const guidance = resourceAvailabilityGuidance(resource);
  const isCatalog = variant === "catalog";
  const openDetail = () => onOpen?.(resource) ?? onPrimaryAction?.(resource);

  return (
    <Card
      className={cn(
        "h-full rounded-[10px] border-border shadow-none [&_.ant-card-body]:flex [&_.ant-card-body]:h-full [&_.ant-card-body]:gap-2.5",
        onOpen || onPrimaryAction ? "cursor-pointer transition-colors hover:border-brand" : "",
        isCatalog
          ? "[&_.ant-card-body]:items-start [&_.ant-card-body]:justify-between [&_.ant-card-body]:p-5 max-[680px]:[&_.ant-card-body]:flex-col"
          : "[&_.ant-card-body]:flex-col [&_.ant-card-body]:p-6",
        className,
      )}
      data-testid={`resource-card-${resource.id}`}
      onClick={openDetail}
      role={onOpen || onPrimaryAction ? "link" : undefined}
      tabIndex={onOpen || onPrimaryAction ? 0 : undefined}
      onKeyDown={event => {
        if (!(onOpen || onPrimaryAction)) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openDetail();
        }
      }}
    >
      <div className={cn("min-w-0", isCatalog ? "flex-1" : "flex flex-1 flex-col gap-2.5")}>
        <Typography.Title level={4} className={cn("m-0! leading-6!", isCatalog ? "mt-2! text-lg!" : "text-xl!")}>
          {resource.name}
        </Typography.Title>
        <Typography.Paragraph className={cn("mb-0! text-text-secondary", isCatalog ? "mt-2! text-sm leading-5" : "text-[15px] leading-6")}>
          {resource.description || "未提供资源说明"}
        </Typography.Paragraph>
      </div>

      <div className={cn("flex shrink-0 flex-wrap gap-2", isCatalog ? "min-w-[136px] flex-col items-end max-[680px]:w-full max-[680px]:flex-row max-[680px]:items-center" : "mt-auto min-h-8 items-center pt-1")}>
        <Tag color={presentation.color} className="m-0! rounded-full px-2.5 text-xs">
          {presentation.label}
        </Tag>
        <p className={cn("m-0 text-text-secondary", isCatalog ? "max-w-[220px] text-right text-xs leading-5 max-[680px]:order-3 max-[680px]:max-w-none max-[680px]:text-left" : "text-[13px] leading-5")} data-testid="resource-availability-guidance">{guidance}</p>
        <Button
          type="primary"
          size="small"
          onClick={event => {
            event.stopPropagation();
            (onPrimaryAction ?? onOpen)?.(resource);
          }}
          data-testid={`resource-card-primary-${resource.id}`}
        >
          {presentation.primaryActionLabel}
        </Button>
        {onEvaluateFit ? (
          <Button
            size="small"
            onClick={event => {
              event.stopPropagation();
              onEvaluateFit(resource);
            }}
          >
            选择任务评估
          </Button>
        ) : null}
        {!isCatalog ? <span className="min-w-0 flex-1" /> : null}
        {onSave ? (
          <Tooltip title={savePending ? "处理中" : saved ? "取消保存" : "保存"}>
            <Button
              aria-label={savePending ? "正在处理保存" : saved ? "已保存" : "保存"}
              aria-busy={savePending || undefined}
              icon={savePending ? <LoaderCircle size={15} className="animate-spin" /> : saved ? <BookmarkCheck size={15} /> : <Bookmark size={15} />}
              size="small"
              loading={savePending}
              disabled={savePending}
              type={saved ? "default" : "dashed"}
              data-testid={`resource-card-save-${resource.id}`}
              onClick={event => {
                event.stopPropagation();
                event.preventDefault();
                if (savePending) return;
                onSave(resource, !saved);
              }}
            >
              {savePending ? "处理中" : saved ? "已保存" : "保存"}
            </Button>
          </Tooltip>
        ) : null}
      </div>
    </Card>
  );
}
