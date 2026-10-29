import { memo, useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "motion/react";
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  NodeToolbar,
} from "@xyflow/react";
import type { Node, Edge, NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Typography } from "antd";
import { ExportOutlined } from "@ant-design/icons";
import type { WorkbenchArtifact, WorkbenchCapabilityCall } from "../model/workbenchTask";
import { labelForSkill } from "../data/recordedRunAdapter";
import type { UiLanguage } from "@openair/shared";
import { formatBeijingDateTime } from "../lib/beijingTime";
import { rememberCurrentArtifact, rememberCurrentTask } from "../lib/userRouteState";
import { cn } from "../lib/cn";

/* ── Node data type ── */
type ArtifactRef = { id: string; title: string };

type CallNodeData = {
  callId: string;
  label: string;
  status: string;
  startedAt?: string;
  endedAt?: string | null;
  resultSummary?: string | null;
  artifacts: ArtifactRef[];
  taskId: string;
  active: boolean;
};

type CallNode = Node<CallNodeData, "call">;

const flowContainerClass = cn(
  "h-[300px] w-full overflow-hidden rounded-lg border border-border bg-surface-soft",
  "[&_.react-flow__controls]:rounded-md [&_.react-flow__controls]:border [&_.react-flow__controls]:border-border [&_.react-flow__controls]:shadow-sm",
  "[&_.react-flow__controls_button]:h-7 [&_.react-flow__controls_button]:w-7 [&_.react-flow__controls_button]:border-b [&_.react-flow__controls_button]:border-border [&_.react-flow__controls_button]:bg-surface [&_.react-flow__controls_button]:text-text-secondary",
  "[&_.react-flow__controls_button:hover]:bg-surface-soft",
);

function callNodeClass(status: string, active: boolean): string {
  return cn(
    "min-w-[160px] cursor-pointer rounded-lg border border-border bg-surface px-4 py-2.5 text-sm leading-normal shadow-sm transition-[border-color,box-shadow] duration-150",
    "hover:border-info-border hover:shadow-accent",
    status === "completed" && "border-l-3 border-l-success",
    status === "running" && "border-l-3 border-l-accent-strong",
    status === "failed" && "border-l-3 border-l-danger",
    status !== "completed" && status !== "running" && status !== "failed" && "border-l-3 border-l-text-muted",
    active && "border-accent-strong shadow-[0_0_0_2px_var(--wb-accent-strong),var(--wb-shadow-sm)]",
  );
}

/* ── helpers ── */
function fmtTime(iso?: string | null): string {
  if (!iso) return "---";
  return formatBeijingDateTime(iso);
}

/* ── DAG layout ── */
const SPACING_X = 240;
const SPACING_Y = 120;

function computeLanes(calls: WorkbenchCapabilityCall[]): Map<string, number> {
  const lanes = new Map<string, number>();
  for (const c of calls) lanes.set(c.id, 0);

  const callIndex = new Map(calls.map((c, i) => [c.id, i]));

  const childrenOf = new Map<string, string[]>();
  for (const call of calls) {
    for (const ref of call.inputCallRefs ?? []) {
      if (!childrenOf.has(ref)) childrenOf.set(ref, []);
      childrenOf.get(ref)!.push(call.id);
    }
  }

  let nextLane = 1;

  for (const [sourceId, children] of childrenOf) {
    const srcIdx = callIndex.get(sourceId) ?? 0;

    if (children.length > 1) {
      for (let i = 0; i < children.length; i++) {
        lanes.set(children[i], nextLane + i);
      }
      nextLane += children.length;
    } else {
      const childIdx = callIndex.get(children[0]) ?? 0;
      const gap = childIdx - srcIdx;
      if (gap > 1) {
        lanes.set(children[0], nextLane++);
      }
    }
  }

  return lanes;
}

/* ── Custom node ── */
const CallNodeComponent = memo(function CallNode({ data }: NodeProps<CallNode>) {
  const navigate = useNavigate();
  return (
    <>
      <Handle type="target" position={Position.Left} style={{ visibility: "hidden" }} />
      <motion.div
        className={callNodeClass(data.status, data.active)}
        data-testid={`capability-call-${data.callId}`}
        whileHover={{ scale: 1.01 }}
        transition={{ duration: 0.15 }}
      >
        <div className="max-w-[200px] truncate font-semibold text-text">{data.label}</div>
        <div className="mt-1 text-2xs text-text-muted">
          {fmtTime(data.startedAt)}
          {data.endedAt ? ` → ${fmtTime(data.endedAt).slice(11)}` : ""}
        </div>
      </motion.div>
      <Handle type="source" position={Position.Right} style={{ visibility: "hidden" }} />
      <NodeToolbar isVisible={data.active} position={Position.Top} offset={8}>
        <div className="min-w-[200px] max-w-[360px] rounded-lg border border-border bg-surface px-4 py-3 text-sm leading-normal shadow-sm">
          <div className="mb-1.5 text-xs text-text-muted">
            {fmtTime(data.startedAt)} → {fmtTime(data.endedAt)}
          </div>
          {data.resultSummary && (
            <div className="mb-1.5 text-text-secondary">{data.resultSummary}</div>
          )}
          {data.artifacts.length > 0 && (
            <div className="text-xs text-accent-strong">
              <span className="text-text-muted">产物：</span>
              {data.artifacts.map((a, i) => (
                <span key={a.id}>
                  {i > 0 && "、"}
                  <button
                    type="button"
                    className="cursor-pointer border-0 bg-transparent p-0 font-inherit text-accent-strong no-underline hover:underline"
                    data-testid={`capability-artifact-${data.callId}-${a.id}`}
                    onClick={() => {
                      rememberCurrentTask(data.taskId);
                      rememberCurrentArtifact(a.id);
                      navigate(`/workbench/${encodeURIComponent(data.taskId)}?artifact=${encodeURIComponent(a.id)}`);
                    }}
                  >
                    {a.title}
                    <ExportOutlined className="ml-1 text-2xs" />
                  </button>
                </span>
              ))}
            </div>
          )}
        </div>
      </NodeToolbar>
    </>
  );
});

const nodeTypes = { call: CallNodeComponent };

/* ── Build nodes + edges ── */
function buildFlowData(
  calls: WorkbenchCapabilityCall[],
  artifacts: WorkbenchArtifact[],
  language: UiLanguage,
  taskId: string,
  activeNodeId: string | null,
): { nodes: CallNode[]; edges: Edge[] } {
  const artMap = new Map(artifacts.map((a) => [a.id, a]));
  const lanes = computeLanes(calls);

  const nodes: CallNode[] = calls.map((c, i) => ({
    id: c.id,
    type: "call" as const,
    position: { x: i * SPACING_X, y: (lanes.get(c.id) ?? 0) * SPACING_Y },
    data: {
      callId: c.id,
      label: labelForSkill(c.name, language),
      status: c.status,
      startedAt: c.startedAt,
      endedAt: c.endedAt,
      resultSummary: c.resultSummary,
      taskId,
      active: activeNodeId === c.id,
      artifacts: (c.artifactRefs ?? [])
        .map((ref) => {
          const art = artMap.get(ref);
          return art ? { id: art.id, title: art.title } : null;
        })
        .filter(Boolean) as ArtifactRef[],
    },
  }));

  const edges: Edge[] = [];
  const edgeSet = new Set<string>();
  const callIds = new Set(calls.map((c) => c.id));

  for (const call of calls) {
    for (const ref of call.inputCallRefs ?? []) {
      const key = `${ref}->${call.id}`;
      if (!edgeSet.has(key) && callIds.has(ref)) {
        edgeSet.add(key);
        edges.push({
          id: key,
          source: ref,
          target: call.id,
          type: "smoothstep",
          animated: call.status === "running",
          style: { stroke: "var(--wb-text)", strokeWidth: 2 },
        });
      }
    }
  }

  for (const call of calls) {
    if (call.prevCallId) {
      const key = `${call.prevCallId}->${call.id}`;
      if (!edgeSet.has(key) && callIds.has(call.prevCallId)) {
        edgeSet.add(key);
        edges.push({
          id: key,
          source: call.prevCallId,
          target: call.id,
          type: "smoothstep",
          animated: call.status === "running",
          style: {
            stroke: "var(--wb-border)",
            strokeWidth: 1.5,
            strokeDasharray: "8 4",
          },
        });
      }
    }
  }

  if (edges.length === 0 && calls.length > 1) {
    for (let i = 0; i < calls.length - 1; i++) {
      edges.push({
        id: `${calls[i].id}-${calls[i + 1].id}`,
        source: calls[i].id,
        target: calls[i + 1].id,
        type: "smoothstep",
        animated: calls[i + 1].status === "running",
        style: {
          stroke: "var(--wb-border)",
          strokeWidth: 1.5,
          strokeDasharray: "8 4",
        },
      });
    }
  }

  return { nodes, edges };
}

/* ── Main component ── */
interface ResearchTimelineFlowProps {
  calls: WorkbenchCapabilityCall[];
  artifacts: WorkbenchArtifact[];
  language: UiLanguage;
  taskId: string;
}

export function ResearchTimelineFlow({ calls, artifacts, language, taskId }: ResearchTimelineFlowProps) {
  const [activeNodeId, setActiveNodeId] = useState<string | null>(null);

  const { nodes, edges } = useMemo(
    () => buildFlowData(calls, artifacts, language, taskId, activeNodeId),
    [calls, artifacts, language, taskId, activeNodeId],
  );

  const handleNodeClick = useCallback((_event: React.MouseEvent, node: Node) => {
    setActiveNodeId((prev) => (prev === node.id ? null : node.id));
  }, []);

  const handlePaneClick = useCallback(() => {
    setActiveNodeId(null);
  }, []);

  if (calls.length === 0) {
    return (
      <div className="flex min-h-[120px] items-center justify-center">
        <Typography.Text type="secondary">暂无运行记录</Typography.Text>
      </div>
    );
  }

  return (
    <div className={flowContainerClass}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={handleNodeClick}
        onPaneClick={handlePaneClick}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        minZoom={0.1}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        zoomOnScroll
        panOnDrag
      >
        <Background gap={20} size={1} color="var(--wb-border)" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
