import { Button, Typography } from "antd";
import type { CanonicalTaskWorkUnit } from "@openair/shared";
import type { WorkbenchArtifact } from "../../model/workbenchTask";
import { scientificArtifactUserTitle } from "../artifacts/ScientificArtifactPresentation";
import {
  formatRoundTime,
  groupSubstageArtifactsByRound,
} from "./substageArtifactRoundsCore.mjs";
import {
  projectSubstageArtifactFooter,
  reconcileSubstageArtifactFooterSelection,
  type SubstageArtifactFooterSelection,
} from "./substageArtifactFooterCore.mjs";

export interface SubstageArtifactFooterProps {
  taskId: string;
  substageInstanceId: string;
  artifactRefs: string[];
  artifacts: WorkbenchArtifact[];
  /** Canonical public WorkUnits; enables per-round grouping for repeatable substages. */
  workUnits?: CanonicalTaskWorkUnit[] | null;
  selection: SubstageArtifactFooterSelection | null;
  deepLinkedArtifactId?: string | null;
  artifactHref: (artifactId: string) => string;
  onSelectArtifact: (artifactId: string) => void;
}

/** Fixed selector for every user-visible Artifact owned by one Substage. */
export function SubstageArtifactFooter({
  taskId,
  substageInstanceId,
  artifactRefs,
  artifacts,
  workUnits = null,
  selection,
  deepLinkedArtifactId = null,
  artifactHref,
  onSelectArtifact,
}: SubstageArtifactFooterProps) {
  const items = projectSubstageArtifactFooter({
    taskId,
    substageInstanceId,
    artifactRefs,
    artifacts,
  });
  if (!items.length) return null;
  const artifactsById = new Map(
    artifacts.map((artifact) => [artifact.id, artifact]),
  );

  const reconciled = reconcileSubstageArtifactFooterSelection({
    selection,
    taskId,
    substageInstanceId,
    artifactIds: items.map((item) => item.id),
    deepLinkedArtifactId,
  });

  const roundGroups = groupSubstageArtifactsByRound({
    workUnits,
    substageInstanceId,
    artifactIds: items.map((item) => item.id),
  });

  const renderArtifactButton = (artifactId: string, fallbackTitle: string) => {
    const selected = artifactId === reconciled.artifactId;
    const artifact = artifactsById.get(artifactId);
    const title = artifact
      ? (scientificArtifactUserTitle(artifact) ?? fallbackTitle)
      : fallbackTitle;
    return (
      <Button
        key={artifactId}
        href={artifactHref(artifactId)}
        type={selected ? "primary" : "default"}
        size="small"
        aria-current={selected ? "page" : undefined}
        data-artifact-id={artifactId}
        onClick={(event) => {
          if (
            event.button !== 0 ||
            event.ctrlKey ||
            event.metaKey ||
            event.shiftKey ||
            event.altKey
          )
            return;
          event.preventDefault();
          onSelectArtifact(artifactId);
        }}
      >
        {title}
      </Button>
    );
  };

  const itemsById = new Map(items.map((item) => [item.id, item]));

  return (
    <footer
      className="shrink-0 border-t border-border bg-surface px-3 py-3"
      aria-label="当前研究节点产物"
      data-testid="substage-artifact-footer"
      data-substage-instance-id={substageInstanceId}
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <Typography.Text strong>本步骤已形成的研究成果</Typography.Text>
        <Typography.Text type="secondary">
          已形成 {items.length} 项
          {roundGroups ? `，共 ${roundGroups.filter((group) => group.roundIndex !== null).length} 轮` : ""}，可随时切换
        </Typography.Text>
      </div>
      {roundGroups ? (
        <div className="grid gap-2" role="list" aria-label="按执行轮次分组的研究产物">
          {roundGroups.map((group) => {
            const groupItems = group.artifactIds
              .map((artifactId) => itemsById.get(artifactId))
              .filter((item): item is NonNullable<typeof item> => Boolean(item));
            if (groupItems.length === 0) return null;
            const time = group.finishedAt ? formatRoundTime(group.finishedAt) : null;
            return (
              <div
                key={group.key ?? "unassigned"}
                className="grid gap-1.5"
                data-testid="substage-artifact-round-group"
                data-round-index={group.roundIndex ?? undefined}
              >
                <Typography.Text type="secondary" className="text-xs">
                  {group.roundIndex === null
                    ? "早期产物（轮次信息缺失）"
                    : `第 ${group.roundIndex} 轮${time ? ` · ${time}` : ""}`}
                </Typography.Text>
                <div className="flex flex-wrap gap-2">
                  {groupItems.map((item) =>
                    renderArtifactButton(item.id, item.title),
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div
          className="flex flex-wrap gap-2"
          role="list"
          aria-label="可查看的研究产物"
        >
          {items.map((item) => renderArtifactButton(item.id, item.title))}
        </div>
      )}
    </footer>
  );
}
