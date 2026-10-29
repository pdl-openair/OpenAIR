import {
  Bot,
  GitBranch,
  WandSparkles,
} from "lucide-react";
import { Typography, Tag } from "antd";
import { cn } from "../../lib/cn";
import type { ResourcePlazaResource } from "../../features/resource-plaza/types";
import {
  RESOURCE_DOMAIN_LABELS,
  RESOURCE_SCENARIO_LABELS,
} from "../../features/resource-plaza/state";

const CARD_TONES = {
  agent: {
    shell: "bg-brand-soft",
    icon: "bg-brand text-surface",
    tag: "bg-surface! text-text-secondary!",
  },
  workflow: {
    shell: "bg-success-soft",
    icon: "bg-success text-surface",
    tag: "bg-surface! text-text-secondary!",
  },
  skill: {
    shell: "bg-warning-soft",
    icon: "bg-warning text-surface",
    tag: "bg-surface! text-text-secondary!",
  },
} as const;

const KindIcon = {
  agent: Bot,
  skill: WandSparkles,
  workflow: GitBranch,
} as const;

export function FeaturedBentoCard({
  resource,
  onOpen,
}: {
  resource: ResourcePlazaResource;
  toneIndex: number;
  onOpen: () => void;
}) {
  const domainTags = resource.domains.slice(0, 2).map(d => RESOURCE_DOMAIN_LABELS[d]);
  const scenarioTags = resource.scenarios.slice(0, 2).map(s => RESOURCE_SCENARIO_LABELS[s]);
  const tags = [...domainTags, ...scenarioTags].slice(0, 3);
  const tone = CARD_TONES[resource.kind];
  const Icon = KindIcon[resource.kind];

  return (
    <button
      type="button"
      onClick={onOpen}
      data-testid={`featured-card-${resource.id}`}
      className={cn(
        "flex h-auto w-full flex-col items-start gap-3 rounded-[20px] border border-transparent p-5 text-left transition-transform duration-200",
        "hover:-translate-y-0.5 focus-visible:outline focus-visible:outline-offset-2 focus-visible:outline-brand",
        tone.shell,
      )}
    >
      <span className={cn("inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-[12px]", tone.icon)}>
        <Icon size={20} aria-hidden />
      </span>
      <div className="flex min-w-0 flex-col gap-1.5">
        <Typography.Title
          level={4}
          ellipsis
          title={resource.name}
          className="m-0! line-clamp-1 text-[18px]! leading-7! text-text!"
        >
          {resource.name}
        </Typography.Title>
        <Typography.Paragraph
          ellipsis={{ rows: 2 }}
          title={resource.description || resource.name}
          className="mb-0! h-12! line-clamp-2 text-[14px]! leading-6! text-text-secondary!"
        >
          {resource.description || resource.name}
        </Typography.Paragraph>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {tags.map(tag => (
          <Tag key={tag} className={cn("m-0! rounded-md border-0 px-2 text-[11px]", tone.tag)}>
            {tag}
          </Tag>
        ))}
      </div>
    </button>
  );
}
