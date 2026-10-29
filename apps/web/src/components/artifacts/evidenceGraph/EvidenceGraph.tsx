import { memo, useCallback, useMemo, useState, type ReactNode } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
} from "@xyflow/react";
import type { Edge, Node, NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Button, Tag, Tooltip, Typography } from "antd";
import { CloseOutlined, ExportOutlined } from "@ant-design/icons";
import {
  projectEvidenceGraph,
  type EvidenceDisposition,
  type ProjectedConflict,
  type ProjectedEvidenceGap,
  type ProjectedEvidenceNode,
  type ProjectedJudgmentNode,
} from "./projectEvidenceGraph";
import {
  AUXILIARY_NODE_WIDTH,
  EVIDENCE_NODE_WIDTH,
  JUDGMENT_NODE_WIDTH,
  buildEvidenceGraphLayout,
  type EvidenceSide,
} from "./layoutEvidenceGraph";

const DISPOSITION_PRESENTATION: Record<
  EvidenceDisposition,
  {
    label: string;
    color: string;
    tagColor: "success" | "error" | "warning" | "processing";
    dashed: boolean;
  }
> = {
  supported: {
    label: "支持",
    color: "#15803d",
    tagColor: "success",
    dashed: false,
  },
  refuted: {
    label: "反驳",
    color: "#dc2626",
    tagColor: "error",
    dashed: false,
  },
  limited: {
    label: "限制",
    color: "#d97706",
    tagColor: "warning",
    dashed: false,
  },
  uncertain: {
    label: "不确定",
    color: "#2563eb",
    tagColor: "processing",
    dashed: true,
  },
};

const CONFLICT_COLOR = "#7c3aed";
const RELATED_COLOR = "#64748b";
const INTERACTIVE_NODE_STYLE = { pointerEvents: "all" } as const;

type FocusTarget =
  | { kind: "judgment"; key: string }
  | { kind: "evidence"; key: string }
  | { kind: "conflict"; key: string }
  | { kind: "gap"; key: string }
  | null;

type CommonNodeState = Record<string, unknown> & {
  focused: boolean;
  dimmed: boolean;
};

type EvidenceNodeData = ProjectedEvidenceNode &
  CommonNodeState & {
    side: EvidenceSide;
    onInspect: (recordId: string) => void;
  };

type JudgmentNodeData = ProjectedJudgmentNode &
  CommonNodeState & {
    onFocus: (viewKey: string) => void;
  };

type ConflictNodeData = ProjectedConflict &
  CommonNodeState & {
    onFocus: (viewKey: string) => void;
  };

type GapNodeData = ProjectedEvidenceGap &
  CommonNodeState & {
    onFocus: (viewKey: string) => void;
  };

type EvidenceFlowNode = Node<EvidenceNodeData, "evidence">;
type JudgmentFlowNode = Node<JudgmentNodeData, "judgment">;
type ConflictFlowNode = Node<ConflictNodeData, "conflict">;
type GapFlowNode = Node<GapNodeData, "gap">;
type EvidenceGraphFlowNode =
  EvidenceFlowNode | JudgmentFlowNode | ConflictFlowNode | GapFlowNode;

function nodeOpacityClass(dimmed: boolean): string {
  return dimmed ? "opacity-25" : "opacity-100";
}

function confidenceLabel(value: number | null): string {
  return value === null ? "" : `置信度 ${(value * 100).toFixed(0)}%`;
}

function doiResolverUrl(doi: string): string {
  const normalized = doi
    .trim()
    .replace(/^doi:\s*/i, "")
    .replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "");
  return normalized ? `https://doi.org/${normalized}` : "";
}

function evidenceGraphPopupContainer(triggerNode: HTMLElement): HTMLElement {
  return (
    (triggerNode.closest(
      '[data-testid="ot-evidence-graph"]',
    ) as HTMLElement | null) ??
    triggerNode.parentElement ??
    triggerNode
  );
}

function HoverDetail({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="max-w-[320px] space-y-2 text-xs leading-5">
      <div className="font-semibold">{title}</div>
      {children}
    </div>
  );
}

const EvidenceNodeComponent = memo(function EvidenceNode({
  data,
}: NodeProps<EvidenceFlowNode>) {
  const mainHandlePosition =
    data.side === "left" ? Position.Right : Position.Left;
  const sourceTitle = data.sourceTitle || data.provider || "来源研究记录";
  return (
    <>
      <Tooltip
        placement="top"
        mouseEnterDelay={0.25}
        getPopupContainer={evidenceGraphPopupContainer}
        title={
          <HoverDetail title={sourceTitle}>
            <div>{data.resultStatement}</div>
            <div className="opacity-80">研究对象：{data.researchObject}</div>
            <div className="break-all opacity-70">{data.recordId}</div>
            {data.doi ? <div className="break-all">DOI：{data.doi}</div> : null}
          </HoverDetail>
        }
      >
        <button
          type="button"
          className={`cursor-pointer rounded-xl border bg-surface px-3 py-2.5 text-left shadow-sm transition-[opacity,border-color,box-shadow,transform] duration-150 hover:-translate-y-0.5 hover:border-emerald-400 hover:shadow-md ${data.focused ? "border-emerald-500 ring-2 ring-emerald-500/20" : "border-emerald-500/40"} ${nodeOpacityClass(data.dimmed)}`}
          style={{ width: EVIDENCE_NODE_WIDTH }}
          data-testid={`evidence-node-${data.recordId}`}
          aria-label={`查看证据来源 ${data.recordId}`}
          onClick={(event) => {
            event.stopPropagation();
            data.onInspect(data.recordId);
          }}
        >
          <span className="mb-1 flex items-center justify-between gap-2">
            <Typography.Text strong className="truncate text-[13px] text-text">
              {sourceTitle}
            </Typography.Text>
            <span className="flex shrink-0 gap-1">
              {data.conflicted ? <Tag color="purple">冲突</Tag> : null}
              {data.gapRelated ? <Tag>缺口</Tag> : null}
            </span>
          </span>
          <Typography.Paragraph
            className="!mb-0 text-xs leading-5 text-text-secondary"
            ellipsis={{ rows: 2 }}
          >
            {data.resultStatement}
          </Typography.Paragraph>
        </button>
      </Tooltip>
      <Handle
        id="main"
        type="source"
        position={mainHandlePosition}
        style={{ visibility: "hidden" }}
      />
      <Handle
        id="aux"
        type="source"
        position={Position.Bottom}
        style={{ visibility: "hidden" }}
      />
    </>
  );
});

const JudgmentNodeComponent = memo(function JudgmentNode({
  data,
}: NodeProps<JudgmentFlowNode>) {
  const presentation = DISPOSITION_PRESENTATION[data.disposition];
  return (
    <>
      <Handle
        id="left"
        type="target"
        position={Position.Left}
        style={{ visibility: "hidden" }}
      />
      <Handle
        id="right"
        type="target"
        position={Position.Right}
        style={{ visibility: "hidden" }}
      />
      <Tooltip
        placement="top"
        mouseEnterDelay={0.25}
        getPopupContainer={evidenceGraphPopupContainer}
        title={
          <HoverDetail title="完整判断">
            <div>{data.statement}</div>
            <div className="opacity-80">适用边界：{data.scopeBoundary}</div>
            <div className="opacity-80">依据：{data.rationale}</div>
            {confidenceLabel(data.confidence) ? (
              <div className="opacity-70">
                {confidenceLabel(data.confidence)}
              </div>
            ) : null}
          </HoverDetail>
        }
      >
        <button
          type="button"
          className={`cursor-pointer rounded-xl border bg-surface-soft px-4 py-3 text-left shadow-sm transition-[opacity,border-color,box-shadow,transform] duration-150 hover:-translate-y-0.5 hover:border-fuchsia-400 hover:shadow-md ${data.focused ? "border-fuchsia-500 ring-2 ring-fuchsia-500/20" : "border-fuchsia-500/40"} ${nodeOpacityClass(data.dimmed)}`}
          style={{ width: JUDGMENT_NODE_WIDTH }}
          data-testid={`judgment-node-${data.viewKey}`}
          aria-label={`聚焦判断 ${data.statement}`}
          onClick={(event) => {
            event.stopPropagation();
            data.onFocus(data.viewKey);
          }}
        >
          <div className="mb-1.5 flex items-center gap-1.5">
            <Tag color={presentation.tagColor}>{presentation.label}</Tag>
            <Typography.Text type="secondary" className="text-[11px]">
              判断
            </Typography.Text>
          </div>
          <Typography.Paragraph
            strong
            className="!mb-0 text-[14px] leading-6 text-text"
            ellipsis={{ rows: 3 }}
          >
            {data.statement}
          </Typography.Paragraph>
        </button>
      </Tooltip>
    </>
  );
});

const ConflictNodeComponent = memo(function ConflictNode({
  data,
}: NodeProps<ConflictFlowNode>) {
  return (
    <>
      <Handle
        id="top"
        type="target"
        position={Position.Top}
        style={{ visibility: "hidden" }}
      />
      <Tooltip
        placement="top"
        mouseEnterDelay={0.25}
        getPopupContainer={evidenceGraphPopupContainer}
        title={
          <HoverDetail title="证据冲突">
            <div>{data.statement}</div>
            <div className="opacity-80">影响：{data.implication}</div>
            <div className="opacity-70">
              涉及 {data.recordIds.length} 条研究记录
            </div>
          </HoverDetail>
        }
      >
        <button
          type="button"
          className={`cursor-pointer rounded-xl border bg-surface px-3 py-2.5 text-left shadow-sm transition-[opacity,border-color,box-shadow] duration-150 hover:border-violet-400 hover:shadow-md ${data.focused ? "border-violet-500 ring-2 ring-violet-500/20" : "border-violet-500/40"} ${nodeOpacityClass(data.dimmed)}`}
          style={{ width: AUXILIARY_NODE_WIDTH }}
          data-testid={`conflict-node-${data.viewKey}`}
          aria-label={`聚焦证据冲突 ${data.statement}`}
          onClick={(event) => {
            event.stopPropagation();
            data.onFocus(data.viewKey);
          }}
        >
          <div className="mb-1 flex items-center justify-between gap-2">
            <Typography.Text strong className="text-[13px] text-text">
              ⚠ 证据冲突
            </Typography.Text>
            <Typography.Text type="secondary" className="text-[11px]">
              {data.recordIds.length} 条记录
            </Typography.Text>
          </div>
          <Typography.Paragraph
            className="!mb-0 text-xs leading-5 text-text-secondary"
            ellipsis={{ rows: 2 }}
          >
            {data.statement}
          </Typography.Paragraph>
        </button>
      </Tooltip>
    </>
  );
});

const GapNodeComponent = memo(function GapNode({
  data,
}: NodeProps<GapFlowNode>) {
  return (
    <>
      <Handle
        id="top"
        type="target"
        position={Position.Top}
        style={{ visibility: "hidden" }}
      />
      <Tooltip
        placement="top"
        mouseEnterDelay={0.25}
        getPopupContainer={evidenceGraphPopupContainer}
        title={
          <HoverDetail title="证据缺口">
            <div>{data.gap}</div>
            <div className="opacity-80">影响：{data.impact}</div>
            <div className="opacity-70">
              关联 {data.relatedRecordIds.length} 条研究记录
            </div>
          </HoverDetail>
        }
      >
        <button
          type="button"
          className={`cursor-pointer rounded-xl border bg-surface px-3 py-2.5 text-left shadow-sm transition-[opacity,border-color,box-shadow] duration-150 hover:border-amber-400 hover:shadow-md ${data.focused ? "border-amber-500 ring-2 ring-amber-500/20" : "border-amber-500/40"} ${nodeOpacityClass(data.dimmed)}`}
          style={{ width: AUXILIARY_NODE_WIDTH }}
          data-testid={`gap-node-${data.viewKey}`}
          aria-label={`聚焦证据缺口 ${data.gap}`}
          onClick={(event) => {
            event.stopPropagation();
            data.onFocus(data.viewKey);
          }}
        >
          <div className="mb-1 flex items-center justify-between gap-2">
            <Typography.Text strong className="text-[13px] text-text">
              ○ 证据缺口
            </Typography.Text>
            <Typography.Text type="secondary" className="text-[11px]">
              {data.relatedRecordIds.length} 条相关记录
            </Typography.Text>
          </div>
          <Typography.Paragraph
            className="!mb-0 text-xs leading-5 text-text-secondary"
            ellipsis={{ rows: 2 }}
          >
            {data.gap}
          </Typography.Paragraph>
        </button>
      </Tooltip>
    </>
  );
});

const nodeTypes = {
  evidence: EvidenceNodeComponent,
  judgment: JudgmentNodeComponent,
  conflict: ConflictNodeComponent,
  gap: GapNodeComponent,
};

interface ActiveGraphSet {
  nodeIds: Set<string>;
  edgeIds: Set<string>;
}

function relationEdgeId(recordId: string, judgmentKey: string): string {
  return `relation:${recordId}->${judgmentKey}`;
}

function conflictEdgeId(recordId: string, conflictKey: string): string {
  return `conflict-link:${recordId}->${conflictKey}`;
}

function gapEdgeId(recordId: string, gapKey: string): string {
  return `gap-link:${recordId}->${gapKey}`;
}

function activeGraphSet(
  projection: ReturnType<typeof projectEvidenceGraph>,
  focus: FocusTarget,
): ActiveGraphSet | null {
  if (!focus) return null;
  const nodeIds = new Set<string>();
  const edgeIds = new Set<string>();

  if (focus.kind === "judgment") {
    nodeIds.add(`judgment:${focus.key}`);
    const recordIds = projection.relations
      .filter((relation) => relation.targetJudgmentKey === focus.key)
      .map((relation) => relation.sourceRecordId);
    const records = new Set(recordIds);
    for (const relation of projection.relations) {
      if (relation.targetJudgmentKey !== focus.key) continue;
      nodeIds.add(`evidence:${relation.sourceRecordId}`);
      edgeIds.add(relationEdgeId(relation.sourceRecordId, focus.key));
    }
    for (const conflict of projection.conflicts) {
      if (!conflict.recordIds.some((id) => records.has(id))) continue;
      nodeIds.add(`conflict:${conflict.viewKey}`);
      for (const recordId of conflict.recordIds) {
        if (!records.has(recordId)) continue;
        edgeIds.add(conflictEdgeId(recordId, conflict.viewKey));
      }
    }
    for (const gap of projection.gaps) {
      if (!gap.relatedRecordIds.some((id) => records.has(id))) continue;
      nodeIds.add(`gap:${gap.viewKey}`);
      for (const recordId of gap.relatedRecordIds) {
        if (!records.has(recordId)) continue;
        edgeIds.add(gapEdgeId(recordId, gap.viewKey));
      }
    }
  }

  if (focus.kind === "evidence") {
    nodeIds.add(`evidence:${focus.key}`);
    for (const relation of projection.relations) {
      if (relation.sourceRecordId !== focus.key) continue;
      nodeIds.add(`judgment:${relation.targetJudgmentKey}`);
      edgeIds.add(relationEdgeId(focus.key, relation.targetJudgmentKey));
    }
    for (const conflict of projection.conflicts) {
      if (!conflict.recordIds.includes(focus.key)) continue;
      nodeIds.add(`conflict:${conflict.viewKey}`);
      edgeIds.add(conflictEdgeId(focus.key, conflict.viewKey));
    }
    for (const gap of projection.gaps) {
      if (!gap.relatedRecordIds.includes(focus.key)) continue;
      nodeIds.add(`gap:${gap.viewKey}`);
      edgeIds.add(gapEdgeId(focus.key, gap.viewKey));
    }
  }

  if (focus.kind === "conflict") {
    nodeIds.add(`conflict:${focus.key}`);
    const conflict = projection.conflicts.find(
      (row) => row.viewKey === focus.key,
    );
    for (const recordId of conflict?.recordIds ?? []) {
      nodeIds.add(`evidence:${recordId}`);
      edgeIds.add(conflictEdgeId(recordId, focus.key));
    }
  }

  if (focus.kind === "gap") {
    nodeIds.add(`gap:${focus.key}`);
    const gap = projection.gaps.find((row) => row.viewKey === focus.key);
    for (const recordId of gap?.relatedRecordIds ?? []) {
      nodeIds.add(`evidence:${recordId}`);
      edgeIds.add(gapEdgeId(recordId, focus.key));
    }
  }

  return { nodeIds, edgeIds };
}

function EdgeLegend() {
  return (
    <div
      className="flex max-w-[360px] flex-wrap gap-x-4 gap-y-1.5 text-xs text-white/90"
      aria-label="证据关系图图例"
    >
      {(Object.keys(DISPOSITION_PRESENTATION) as EvidenceDisposition[]).map(
        (kind) => (
          <span key={kind} className="inline-flex items-center gap-1.5">
            <span
              className="h-0.5 w-5"
              style={{ backgroundColor: DISPOSITION_PRESENTATION[kind].color }}
            />
            {DISPOSITION_PRESENTATION[kind].label}
          </span>
        ),
      )}
      <span className="inline-flex items-center gap-1.5">
        <span className="w-5 border-t-2 border-dashed border-violet-600" />
        冲突涉及
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="w-5 border-t-2 border-dashed border-slate-500" />
        缺口相关
      </span>
    </div>
  );
}

function EvidenceInspector({
  record,
  onClose,
  onOpenRecord,
}: {
  record: ProjectedEvidenceNode;
  onClose: () => void;
  onOpenRecord?: (recordId: string) => void;
}) {
  const doiUrl = doiResolverUrl(record.doi);
  return (
    <aside
      data-testid="evidence-record-inspector"
      className="absolute right-3 top-3 z-20 max-h-[calc(100%-24px)] overflow-auto rounded-xl border border-border bg-surface/95 p-4 shadow-xl backdrop-blur"
      style={{ width: "min(340px, calc(100% - 24px))" }}
      onClick={(event) => event.stopPropagation()}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Typography.Text type="secondary" className="block text-[11px]">
            研究记录详情
          </Typography.Text>
          <Typography.Text strong className="mt-0.5 block text-sm text-text">
            {record.sourceTitle || record.provider}
          </Typography.Text>
        </div>
        <Button
          type="text"
          size="small"
          aria-label="关闭研究记录详情"
          icon={<CloseOutlined />}
          onClick={onClose}
        />
      </div>

      <div className="mt-4 space-y-3">
        <div>
          <div className="text-[11px] text-text-muted">这条记录发现了什么</div>
          <div className="mt-0.5 text-sm leading-6 text-text-secondary">
            {record.resultStatement}
          </div>
        </div>
        <div>
          <div className="text-[11px] text-text-muted">研究对象</div>
          <div className="mt-0.5 text-sm font-medium text-text">
            {record.researchObject}
          </div>
        </div>
        <div className="rounded-lg bg-surface-soft p-3">
          <div className="text-[11px] text-text-muted">原始来源</div>
          <div className="mt-1 text-sm font-medium text-text">
            {record.sourceTitle || record.provider}
          </div>
          {record.sectionName ? (
            <div className="mt-1 text-xs text-text-muted">
              {record.sectionName}
            </div>
          ) : null}
          <div className="mt-3 text-[11px] text-text-muted">DOI</div>
          {record.doi ? (
            <Typography.Link
              data-testid="evidence-inspector-doi"
              href={doiUrl}
              target="_blank"
              rel="noreferrer"
              className="mt-0.5 inline-flex break-all text-sm"
            >
              {record.doi} <ExportOutlined className="ml-1" />
            </Typography.Link>
          ) : (
            <div
              className="mt-0.5 text-sm text-text-muted"
              data-testid="evidence-inspector-doi-empty"
            >
              未提供 DOI
            </div>
          )}
          {record.arxivId ? (
            <div className="mt-2 text-xs text-text-muted">
              arXiv {record.arxivId}
            </div>
          ) : null}
        </div>
        <div>
          <div className="text-[11px] text-text-muted">记录 ID</div>
          <div
            className="mt-0.5 break-all font-mono text-xs text-text-secondary"
            data-testid="evidence-inspector-record-id"
          >
            {record.recordId}
          </div>
        </div>
      </div>

      {onOpenRecord ? (
        <Button
          className="mt-4 w-full"
          onClick={() => onOpenRecord(record.recordId)}
        >
          查看完整研究记录
        </Button>
      ) : null}
    </aside>
  );
}

export interface EvidenceGraphProps {
  assessment: unknown;
  researchRecords: unknown;
  /** Optional Integration hook. DOI/source inspection works without it. */
  onOpenRecord?: (recordId: string) => void;
}

export function EvidenceGraph({
  assessment,
  researchRecords,
  onOpenRecord,
}: EvidenceGraphProps) {
  const projection = useMemo(
    () => projectEvidenceGraph(assessment, researchRecords),
    [assessment, researchRecords],
  );
  const layout = useMemo(
    () => buildEvidenceGraphLayout(projection),
    [projection],
  );
  const [focus, setFocus] = useState<FocusTarget>(null);
  const [selectedRecordId, setSelectedRecordId] = useState<string | null>(null);

  const evidenceById = useMemo(
    () =>
      new Map(
        projection.evidenceNodes.map((record) => [record.recordId, record]),
      ),
    [projection.evidenceNodes],
  );
  const selectedRecord = selectedRecordId
    ? (evidenceById.get(selectedRecordId) ?? null)
    : null;
  const active = useMemo(
    () => activeGraphSet(projection, focus),
    [projection, focus],
  );

  const inspectRecord = useCallback((recordId: string) => {
    setSelectedRecordId(recordId);
    setFocus({ kind: "evidence", key: recordId });
  }, []);
  const focusJudgment = useCallback((viewKey: string) => {
    setSelectedRecordId(null);
    setFocus({ kind: "judgment", key: viewKey });
  }, []);
  const focusConflict = useCallback((viewKey: string) => {
    setSelectedRecordId(null);
    setFocus({ kind: "conflict", key: viewKey });
  }, []);
  const focusGap = useCallback((viewKey: string) => {
    setSelectedRecordId(null);
    setFocus({ kind: "gap", key: viewKey });
  }, []);
  const clearFocus = useCallback(() => {
    setFocus(null);
    setSelectedRecordId(null);
  }, []);

  const { nodes, edges } = useMemo(() => {
    const evidencePlacement = new Map(
      layout.evidence.map((row) => [row.recordId, row]),
    );
    const judgmentPlacement = new Map(
      layout.judgments.map((row) => [row.viewKey, row]),
    );
    const auxiliaryPlacement = new Map(
      layout.auxiliary.map((row) => [`${row.kind}:${row.viewKey}`, row]),
    );

    const nodeState = (id: string): CommonNodeState => ({
      focused: Boolean(active?.nodeIds.has(id)),
      dimmed: Boolean(active && !active.nodeIds.has(id)),
    });

    const evidenceNodes: EvidenceFlowNode[] = projection.evidenceNodes.map(
      (record) => {
        const placement = evidencePlacement.get(record.recordId)!;
        return {
          id: `evidence:${record.recordId}`,
          type: "evidence",
          position: { x: placement.x, y: placement.y },
          data: {
            ...record,
            ...nodeState(`evidence:${record.recordId}`),
            side: placement.side,
            onInspect: inspectRecord,
          },
          style: INTERACTIVE_NODE_STYLE,
          draggable: false,
          selectable: false,
        };
      },
    );

    const judgmentNodes: JudgmentFlowNode[] = projection.judgmentNodes.map(
      (judgment) => {
        const placement = judgmentPlacement.get(judgment.viewKey)!;
        return {
          id: `judgment:${judgment.viewKey}`,
          type: "judgment",
          position: { x: placement.x, y: placement.y },
          data: {
            ...judgment,
            ...nodeState(`judgment:${judgment.viewKey}`),
            onFocus: focusJudgment,
          },
          style: INTERACTIVE_NODE_STYLE,
          draggable: false,
          selectable: false,
        };
      },
    );

    const conflictNodes: ConflictFlowNode[] = projection.conflicts.map(
      (conflict) => {
        const placement = auxiliaryPlacement.get(
          `conflict:${conflict.viewKey}`,
        )!;
        return {
          id: `conflict:${conflict.viewKey}`,
          type: "conflict",
          position: { x: placement.x, y: placement.y },
          data: {
            ...conflict,
            ...nodeState(`conflict:${conflict.viewKey}`),
            onFocus: focusConflict,
          },
          style: INTERACTIVE_NODE_STYLE,
          draggable: false,
          selectable: false,
        };
      },
    );

    const gapNodes: GapFlowNode[] = projection.gaps.map((gap) => {
      const placement = auxiliaryPlacement.get(`gap:${gap.viewKey}`)!;
      return {
        id: `gap:${gap.viewKey}`,
        type: "gap",
        position: { x: placement.x, y: placement.y },
        data: {
          ...gap,
          ...nodeState(`gap:${gap.viewKey}`),
          onFocus: focusGap,
        },
        style: INTERACTIVE_NODE_STYLE,
        draggable: false,
        selectable: false,
      };
    });

    const relationEdges: Edge[] = projection.relations.map((relation) => {
      const presentation = DISPOSITION_PRESENTATION[relation.disposition];
      const side =
        evidencePlacement.get(relation.sourceRecordId)?.side ?? "left";
      const id = relationEdgeId(
        relation.sourceRecordId,
        relation.targetJudgmentKey,
      );
      const dimmed = Boolean(active && !active.edgeIds.has(id));
      return {
        id,
        source: `evidence:${relation.sourceRecordId}`,
        sourceHandle: "main",
        target: `judgment:${relation.targetJudgmentKey}`,
        targetHandle: side === "left" ? "left" : "right",
        type: "smoothstep",
        label: presentation.label,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: presentation.color,
          width: 17,
          height: 17,
        },
        style: {
          stroke: presentation.color,
          strokeWidth: dimmed ? 1.2 : 2.2,
          strokeDasharray: presentation.dashed ? "7 5" : undefined,
          opacity: dimmed ? 0.16 : 1,
        },
        labelStyle: {
          fill: presentation.color,
          fontWeight: 650,
          fontSize: 12,
          opacity: dimmed ? 0.2 : 1,
        },
        labelBgStyle: {
          fill: "transparent",
          fillOpacity: 0,
        },
        labelBgPadding: [5, 3],
        labelBgBorderRadius: 4,
      };
    });

    const conflictEdges: Edge[] = projection.conflicts.flatMap((conflict) =>
      conflict.recordIds.map((recordId) => {
        const id = conflictEdgeId(recordId, conflict.viewKey);
        const dimmed = Boolean(active && !active.edgeIds.has(id));
        return {
          id,
          source: `evidence:${recordId}`,
          sourceHandle: "aux",
          target: `conflict:${conflict.viewKey}`,
          targetHandle: "top",
          type: "smoothstep",
          label: "冲突涉及",
          style: {
            stroke: CONFLICT_COLOR,
            strokeWidth: dimmed ? 1 : 1.8,
            strokeDasharray: "7 5",
            opacity: dimmed ? 0.12 : 0.8,
          },
          labelStyle: {
            fill: CONFLICT_COLOR,
            fontSize: 11,
            opacity: dimmed ? 0.16 : 0.9,
          },
          labelBgStyle: { fill: "transparent", fillOpacity: 0 },
          labelBgPadding: [4, 2],
          labelBgBorderRadius: 4,
        } satisfies Edge;
      }),
    );

    const gapEdges: Edge[] = projection.gaps.flatMap((gap) =>
      gap.relatedRecordIds.map((recordId) => {
        const id = gapEdgeId(recordId, gap.viewKey);
        const dimmed = Boolean(active && !active.edgeIds.has(id));
        return {
          id,
          source: `evidence:${recordId}`,
          sourceHandle: "aux",
          target: `gap:${gap.viewKey}`,
          targetHandle: "top",
          type: "smoothstep",
          label: "相关",
          style: {
            stroke: RELATED_COLOR,
            strokeWidth: dimmed ? 1 : 1.6,
            strokeDasharray: "4 5",
            opacity: dimmed ? 0.12 : 0.72,
          },
          labelStyle: {
            fill: RELATED_COLOR,
            fontSize: 11,
            opacity: dimmed ? 0.16 : 0.9,
          },
          labelBgStyle: { fill: "transparent", fillOpacity: 0 },
          labelBgPadding: [4, 2],
          labelBgBorderRadius: 4,
        } satisfies Edge;
      }),
    );

    return {
      nodes: [
        ...evidenceNodes,
        ...judgmentNodes,
        ...conflictNodes,
        ...gapNodes,
      ] as EvidenceGraphFlowNode[],
      edges: [...relationEdges, ...conflictEdges, ...gapEdges],
    };
  }, [
    active,
    focusConflict,
    focusGap,
    focusJudgment,
    inspectRecord,
    layout.auxiliary,
    layout.evidence,
    layout.judgments,
    projection.conflicts,
    projection.evidenceNodes,
    projection.gaps,
    projection.judgmentNodes,
    projection.relations,
  ]);

  const hasGraphContent = nodes.length > 0;
  const initialFitNodes = useMemo(() => {
    if (projection.relations.length === 0) {
      return nodes.map((node) => ({ id: node.id }));
    }
    const relatedEvidenceIds = new Set(
      projection.relations.map(
        (relation) => `evidence:${relation.sourceRecordId}`,
      ),
    );
    return nodes
      .filter(
        (node) => node.type === "judgment" || relatedEvidenceIds.has(node.id),
      )
      .map((node) => ({ id: node.id }));
  }, [nodes, projection.relations]);
  const graphHeight = Math.min(
    660,
    Math.max(520, Math.round(layout.height * 0.62)),
  );

  return (
    <section
      data-testid="ot-evidence-graph"
      className="relative min-w-0 space-y-3"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-[220px] flex-1">
          <Typography.Title level={4} className="!mb-1">
            证据关系图
          </Typography.Title>
          <Typography.Text type="secondary">
            点击节点聚焦相关关系；悬浮查看细节，点击研究记录可核查来源与 DOI。
          </Typography.Text>
        </div>
        <Typography.Text
          type="secondary"
          className="text-xs"
          aria-label="证据关系图摘要"
        >
          {projection.evidenceNodes.length} 条研究记录 ·{" "}
          {projection.judgmentNodes.length} 个判断 ·{" "}
          {projection.conflicts.length} 个冲突 · {projection.gaps.length}{" "}
          个证据缺口
        </Typography.Text>
      </div>

      <div className="flex flex-wrap items-center justify-end gap-2">
        <Tooltip
          placement="bottom"
          title={<EdgeLegend />}
          getPopupContainer={evidenceGraphPopupContainer}
        >
          <Button size="small">图例</Button>
        </Tooltip>
        <Button
          size="small"
          disabled={!focus && !selectedRecordId}
          onClick={clearFocus}
        >
          取消聚焦
        </Button>
      </div>

      {hasGraphContent ? (
        <div
          className="relative w-full overflow-hidden rounded-xl border border-border bg-surface-soft [&_.react-flow__controls]:rounded-md [&_.react-flow__controls]:border [&_.react-flow__controls]:border-border [&_.react-flow__controls]:shadow-sm [&_.react-flow__controls_button]:h-7 [&_.react-flow__controls_button]:w-7 [&_.react-flow__controls_button]:border-b [&_.react-flow__controls_button]:border-border [&_.react-flow__controls_button]:bg-surface [&_.react-flow__controls_button]:text-text-secondary [&_.react-flow__controls_button:hover]:bg-surface-soft"
          style={{ height: graphHeight }}
          data-testid="evidence-graph-canvas"
        >
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{
              padding: 0.06,
              minZoom: 0.76,
              maxZoom: 0.92,
              nodes: initialFitNodes,
            }}
            minZoom={0.18}
            maxZoom={1.35}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={false}
            onPaneClick={clearFocus}
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={24} size={1} color="var(--wb-border)" />
            <Controls showInteractive={false} />
          </ReactFlow>
          {selectedRecord ? (
            <EvidenceInspector
              record={selectedRecord}
              onClose={() => setSelectedRecordId(null)}
              onOpenRecord={onOpenRecord}
            />
          ) : null}
        </div>
      ) : (
        <div
          className="rounded-xl border border-border bg-surface-soft px-4 py-8 text-center"
          data-testid="evidence-graph-empty"
        >
          <Typography.Text type="secondary">
            当前数据不足以安全绘制证据关系图；不会根据缺失记录补造关系。
          </Typography.Text>
        </div>
      )}
    </section>
  );
}
