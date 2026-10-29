import { Bot, GitBranch, WandSparkles } from "lucide-react";
import { Tag, Typography } from "antd";
import { cn } from "../../lib/cn";
import {
  RESOURCE_DOMAIN_LABELS,
  RESOURCE_KIND_LABELS,
  RESOURCE_SCENARIO_LABELS,
  resourceAvailabilityPresentation,
} from "../../features/resource-plaza/state";
import type { ResourcePlazaResource } from "../../features/resource-plaza/types";

const KIND_SHELL: Record<ResourcePlazaResource["kind"], string> = {
  agent: "bg-info-soft text-brand",
  skill: "bg-brand-soft text-brand",
  workflow: "bg-accent-soft text-brand",
};

const KindIcon = {
  agent: Bot,
  skill: WandSparkles,
  workflow: GitBranch,
} as const;

export function CatalogIconCard({
  resource,
  onOpen,
}: {
  resource: ResourcePlazaResource;
  onOpen: () => void;
}) {
  const presentation = resourceAvailabilityPresentation(
    resource.availability,
    resource.safetyRisk === "execution_blocked",
  );
  const Icon = KindIcon[resource.kind];
  const domainTags = resource.domains.slice(0, 2).map(d => RESOURCE_DOMAIN_LABELS[d]);
  const scenarioTags = resource.scenarios.slice(0, 2).map(s => RESOURCE_SCENARIO_LABELS[s]);
  const tags = [...domainTags, ...scenarioTags].slice(0, 3);

  return (
    <button
      type="button"
      onClick={onOpen}
      data-testid={`catalog-icon-card-${resource.id}`}
      className={cn(
        "flex h-auto w-full flex-col gap-3 rounded-[12px] border border-border bg-surface-soft p-4 text-left",
        "transition-colors hover:border-brand focus-visible:outline focus-visible:outline-offset-2 focus-visible:outline-brand",
      )}
    >
      <div className="flex items-start gap-3">
        <span
          className={cn(
            "inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-[10px]",
            KIND_SHELL[resource.kind],
          )}
          aria-hidden
        >
          <Icon size={20} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-2">
            <Typography.Title
              level={5}
              ellipsis
              title={resource.name}
              className="m-0! min-w-0 flex-1 line-clamp-1 text-[16px]! leading-6! text-text!"
            >
              {resource.name}
            </Typography.Title>
            <Tag color={presentation.color} className="m-0! shrink-0 rounded-full px-2 text-[11px]">
              {presentation.label}
            </Tag>
          </div>
          <Typography.Paragraph
            ellipsis={{ rows: 2 }}
            title={resource.description || "未提供资源说明"}
            className="mb-0! mt-1.5! h-10! line-clamp-2 text-[13px]! leading-5! text-text-secondary!"
          >
            {resource.description || "未提供资源说明"}
          </Typography.Paragraph>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <Tag className="m-0! rounded-md border-0 bg-surface px-2 text-[11px] text-text-secondary">
          {RESOURCE_KIND_LABELS[resource.kind]}
        </Tag>
        {tags.map(tag => (
          <Tag key={tag} className="m-0! rounded-md border-0 bg-surface px-2 text-[11px] text-text-secondary">
            {tag}
          </Tag>
        ))}
      </div>
    </button>
  );
}
