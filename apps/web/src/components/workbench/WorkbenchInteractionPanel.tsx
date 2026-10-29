import { researchWorkUnitContexts as projectResearchWorkUnitContexts } from "../../model/researchActivityStreamCore.mjs";
import { WorkbenchPlanningStatus } from "./WorkbenchPlanningStatus";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { CompressOutlined, ExpandOutlined, ReloadOutlined, ArrowUpOutlined, StopOutlined } from "@ant-design/icons";
import { App, Button, Input, Popconfirm, Typography } from "antd";
import { DepthSegmentedControl } from "../DepthSegmentedControl";
import {
  decideTaskCollaborationGate,
  type CollaborationDecisionAction,
  type CollaborationDecisionResult,
  type CollaborationProjection,
  type ControllerCommand,
  type TaskExecutionPolicy
} from "../../api/client";
import { fetchAgentMessages, type AgentMessage } from "../../api/agent";
import { ApiError } from "../../api/http";
import type { TaskExecutionMode } from "@openair/shared";
import type { ResearchStartSessionStatus } from "../../api/client";
import type { WorkbenchArtifact, WorkbenchTaskModel } from "../../model/workbenchTask";
import { type WorkbenchScreenViewModel } from "../../model/workbenchScreenView";
import { projectWorkbenchArtifactState } from "../../lib/workbenchUi";
import {
  currentWorkbenchActionSnapshot, hasWorkbenchTaskAction,
} from "../../lib/workbenchInterruption.mjs";
import type { TaskRecoveryProjection } from "../../hooks/useTaskRecoveryProjection";
import { getWorkbenchTaskRequestCoordinator } from "../../lib/workbenchTaskRequestCoordinator";
import { ResearchActivityStream, type ResearchActivityPresentationApi } from "./ResearchActivityStream";
import { scientificArtifactUserTitle } from "../artifacts/ScientificArtifactPresentation";
import type { WorkbenchUserActionsController } from "../../hooks/useWorkbenchUserActions";
import type {
  WorkbenchActionOperationState,
  WorkbenchUserActionItem,
  WorkbenchUserActionName
} from "../../api/workbenchUserActions";
import {
  authoritativeReloadPlan,
  collaborationCanonicalArtifactExists,
  presentCollaborationActionReason,
  presentCollaborationResultSummary,
  projectRightPanelArtifacts,
  safeRightPanelText
} from "./workbenchRightPanelCore.mjs";
import { PvdFeedbackPanel } from "./PvdFeedbackPanel";
import { pendingDecisionsFromProjection } from "./pendingDecisionsFromProjection.mjs";
import { formatElapsedSeconds, projectResearchStartWaitingView } from "../../lib/researchStartWaiting";
import type { WorkbenchUserStateId } from "../../lib/workbenchUserState";
import { composeCollaborationPresentation } from "../../model/collaborationPresentationCore.mjs";
import {
  isCollaborationScrollNearBottom,
  shouldFollowCollaborationScroll
} from "../../model/collaborationScrollPolicy.mjs";

export interface WorkbenchInteractionPanelProps {
  taskId: string;
  model: WorkbenchTaskModel;
  /** Canonical user-visible Artifact set supplied by the Workbench parent. */
  artifacts: WorkbenchArtifact[];
  /** Required unique screen/view model from the parent (same Task revision). */
  screen: WorkbenchScreenViewModel;
  /** Collaboration snapshot already matched to screen.revision; do not refetch here. */
  collaboration?: CollaborationProjection | null;
  onCollaborationRefresh?: () => void;
  executionPolicy?: TaskExecutionPolicy | null;
  executionPolicyUnavailable?: boolean;
  executionModeSaving?: boolean;
  showExecutionControls?: boolean;
  onExecutionModeChange?: (mode: TaskExecutionMode) => void;
  supplementNote: string;
  onSupplementNoteChange: (value: string) => void;
  controllerPendingMessage?: string | null;
  controllerReceipt?: ControllerCommand | null;
  onSubmitControllerSupplement?: () => Promise<ControllerCommand | null>;
  controllerSubmitting?: boolean;
  onTaskReload?: () => void;
  apiConnected?: boolean;
  onSelectArtifact?: (artifactId: string) => void;
  continuingResearch?: boolean;
  onContinueResearch?: () => void;
  canStopExecution?: boolean;
  stoppingExecution?: boolean;
  onStopExecution?: () => void;
  recoveryProjection?: TaskRecoveryProjection | null;
  onRecoveryReconcile?: () => void;
  recoveryVisible?: boolean;
  planningAwaitingConfirmation?: boolean;
  planningRevisionRequired?: boolean;
  planningConfirming?: boolean;
  planningConfirmDisabled?: boolean;
  planningModifyDisabled?: boolean;
  onConfirmPlanning?: () => void;
  onModifyPlanning?: () => void;
  canStartConfirmedResearch?: boolean;
  startingConfirmedResearch?: boolean;
  onStartConfirmedResearch?: () => void;
  userActions: WorkbenchUserActionsController;
  planningStatus?: ResearchStartSessionStatus | null;
  planningStatusReadUnavailable?: boolean;
  planningStatusActive?: boolean;
  workbenchUserState?: WorkbenchUserStateId;
  onRetryPlanning?: () => void;
  planningRetrying?: boolean;
  onRecheckPlanningSession?: () => void;
  /** Whether to show the PVD experiment feedback entry button */
  showPvdFeedback?: boolean;
}

type CollaborationPresentationNode = {
  id: string;
  kind: "research_activity" | "user_intervention" | "research_result" | "action_required" | "artifact" | "control";
  controlKind?: string;
  source: unknown;
  related: CollaborationPresentationNode[];
};

type CollaborationControlFact = {
  id: string;
  kind: string;
  occurredAt?: string | null;
  workUnitId?: string | null;
  artifactRefs?: string[];
  source: unknown;
};

const USER_ACTION_LABELS: Record<WorkbenchUserActionName, string> = {
  supplement_input: "补充信息",
  explicit_retry: "重试",
  continue_research: "继续研究",
  stop: "停止执行"
};

const EXECUTION_MODE_OPTIONS: Array<{ label: string; value: TaskExecutionMode }> = [
  { label: "自动研究", value: "automatic_research" },
  { label: "分阶段确认", value: "stage_confirmation" },
  { label: "手动推进", value: "manual_step" }
];

function ControllerConversationStreamItems({
  pendingMessage,
  receipt
}: {
  pendingMessage?: string | null;
  receipt?: ControllerCommand | null;
}) {
  const authoritativeMessage = receipt
    ? receipt.state === "applied"
      ? "已应用你的补充"
      : receipt.state === "received"
        ? "已收到你的补充"
        : receipt.state === "rejected" || receipt.state === "failed"
          ? "这次补充没有生效"
          : null
    : null;
  return (
    <>
      {pendingMessage ? (
        <StreamItem testId="workbench-controller-user-message">
          <Typography.Text type="secondary">你</Typography.Text>
          <Typography.Text>{pendingMessage}</Typography.Text>
        </StreamItem>
      ) : null}
      {receipt && authoritativeMessage ? (
        <StreamItem testId="workbench-controller-ai-message">
          <Typography.Text>{authoritativeMessage}</Typography.Text>
        </StreamItem>
      ) : null}
    </>
  );
}

function formatMessageTime(value?: string | null): string | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return null;
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(parsed));
}

function AgentMessageStreamItem({
  message,
  relatedContent,
  visibleArtifactIds
}: {
  message: AgentMessage;
  relatedContent?: ReactNode;
  visibleArtifactIds: string[];
}) {
  if (message.role === "user") {
    const timeLabel = formatMessageTime(message.at);
    return (
      <StreamItem testId={`workbench-user-intervention-${message.id}`}>
        <div className="grid gap-1" data-testid="workbench-user-message-bubble">
          <div className="rounded-xl border border-white/10 bg-surface-muted px-3 py-2 shadow-[0_8px_24px_rgba(0,0,0,0.35)]">
            <Typography.Text>{message.text}</Typography.Text>
            {timeLabel ? (
              <div className="mt-1 text-right">
                <Typography.Text type="secondary" className="text-[11px]" data-testid="workbench-user-message-time">
                  {timeLabel}
                </Typography.Text>
              </div>
            ) : null}
          </div>
        </div>
        {relatedContent}
      </StreamItem>
    );
  }
  const canonicalArtifactExists = collaborationCanonicalArtifactExists(message.artifactRefs, visibleArtifactIds);
  const summary =
    message.messageType === "research_dispatch_result"
      ? presentCollaborationResultSummary(message.userFacingNarrative?.summary, {
          canonicalArtifactExists
        })
      : null;
  if (canonicalArtifactExists && relatedContent) return <>{relatedContent}</>;
  const assistantText = summary || message.text?.trim() || "";
  if (!assistantText) return null;
  return (
    <StreamItem testId={`workbench-research-result-${message.id}`}>
      {summary ? <Typography.Text type="secondary">研究结果</Typography.Text> : null}
      <Typography.Text>{assistantText}</Typography.Text>
      {relatedContent}
    </StreamItem>
  );
}

function ArtifactReferenceStreamItem({
  taskId,
  artifacts,
  taskResultSource,
  apiConnected,
  onSelectArtifact,
  artifactIds,
  compact = false
}: {
  taskId: string;
  artifacts: WorkbenchArtifact[];
  taskResultSource: WorkbenchTaskModel["resultSource"];
  apiConnected: boolean;
  onSelectArtifact?: (artifactId: string) => void;
  artifactIds?: string[];
  compact?: boolean;
}) {
  const requestedIds = artifactIds?.length ? new Set(artifactIds) : null;
  const projected = projectRightPanelArtifacts(
    taskId,
    artifacts
      .filter(artifact => !requestedIds || requestedIds.has(artifact.id))
      .map(artifact => ({ artifact, projection: projectWorkbenchArtifactState(artifact) })),
    taskResultSource,
    apiConnected
  );
  const displayTitleById = new Map(
    artifacts.map((artifact) => [artifact.id, scientificArtifactUserTitle(artifact) ?? artifact.title])
  );
  const visibleProjected = (
    projected as Array<{ id: string; title: string; stateLabel: string }>
  ).map((artifact) => ({
    ...artifact,
    title: displayTitleById.get(artifact.id) ?? artifact.title
  }));
  if (!visibleProjected.length) return null;
  const content = (
    <>
      <Typography.Text type="secondary">{compact ? "相关产物" : "研究产物"}</Typography.Text>
      <div className="flex flex-wrap gap-1.5">
        {visibleProjected.slice(0, 6).map((artifact) => (
          <Button key={artifact.id} size="small" onClick={() => onSelectArtifact?.(artifact.id)}>
            {compact ? `${artifact.title} 查看 →` : artifact.title} · {artifact.stateLabel}
          </Button>
        ))}
      </div>
    </>
  );
  return compact ? (
    <div className="grid gap-1" data-testid="workbench-action-related-artifacts">
      {content}
    </div>
  ) : (
    <StreamItem testId="workbench-artifact-reference-stream-item">{content}</StreamItem>
  );
}

function actionStateMessage(action: WorkbenchUserActionName, state: WorkbenchActionOperationState): string | null {
  if (state === "submitting") return "正在提交…";
  if (state === "response_unknown") return "提交结果暂未确认，正在重新核对。";
  if (action === "supplement_input" && state === "pending") return "已提交，等待在安全节点生效。";
  if (action === "supplement_input" && state === "confirmed") return "补充已生效。";
  if (state === "pending") return "正在处理，请稍候。";
  if (state === "confirmed") return "已确认。";
  if (state === "rejected") return "这项操作没有被接受。";
  if (state === "failed") return "操作未完成，请重新读取最新进展后再试。";
  return null;
}

function actionReasonPresentation(item: WorkbenchUserActionItem, visibleArtifactIds: string[]): string | null {
  if (item.kind === "explicit_retry_ready") return "上次执行未成功完成。重试会创建一次新的执行。";
  if (item.kind === "structured_output_retry_ready") return "上次输出未通过检查。重试会重新生成，已有成果保留。";
  if (item.kind === "artifact_review_required") return "当前产物需要你确认。";
  if (item.kind === "supplement_pending") return "补充已提交，等待应用。";
  if (item.kind === "supplement_rejected") return "这次补充没有生效。";
  if (item.kind === "provide_input") {
    if (!item.allowedActions.includes("supplement_input")) return safeRightPanelText(item.reason, "系统暂不能接收并应用这次补充；你现在无需操作。");
    const reason = presentCollaborationActionReason(item.reason, {
      canonicalArtifactExists: collaborationCanonicalArtifactExists(item.artifactRefs, visibleArtifactIds)
    });
    if (reason && reason !== "当前步骤尚未继续，系统正在确认原因。") return reason;
    return item.title.startsWith("可补充")
      ? "如有补充信息，可在这里添加。"
      : "当前步骤还缺少继续研究所需的具体条件或材料。请补充你目前能提供的范围、材料或约束。";
  }
  return presentCollaborationActionReason(item.reason, {
    canonicalArtifactExists: collaborationCanonicalArtifactExists(item.artifactRefs, visibleArtifactIds)
  });
}

function provideInputDetailLines(item: WorkbenchUserActionItem, visibleArtifactIds: string[]): string[] {
  if (item.kind !== "provide_input") return [];
  const reason = actionReasonPresentation(item, visibleArtifactIds);
  const objective = safeRightPanelText(item.objective, "");
  const lines = [objective, reason]
    .flatMap(copy => (copy ?? "").split(/[\n\r]+/))
    .map(copy => copy.trim())
    .filter(Boolean);
  return [...new Set(lines)].slice(0, 4);
}

function StreamItem({ children, testId }: { children: React.ReactNode; testId?: string }) {
  return (
    <div className="grid gap-1.5 border-b border-border py-3 last:border-b-0" role="listitem" data-testid={testId}>
      {children}
    </div>
  );
}

export function UserActionStreamItems({
  items,
  unrecoverableMessage,
  onSupplement,
  onExplicitRetry,
  operationState,
  renderArtifacts,
  visibleArtifactIds = [],
  hideExplicitRetry = false,
}: {
  items: WorkbenchUserActionItem[];
  unrecoverableMessage?: string | null;
  onSupplement: () => void;
  onExplicitRetry: (item: WorkbenchUserActionItem) => void;
  operationState: (item: WorkbenchUserActionItem, action: WorkbenchUserActionName) => WorkbenchActionOperationState;
  renderArtifacts?: (artifactIds: string[]) => ReactNode;
  visibleArtifactIds?: string[];
  hideExplicitRetry?: boolean;
}) {
  return (
    <>
      {items.map(item => (
        <StreamItem key={item.itemId} testId={`workbench-user-action-stream-item-${item.itemId}`}>
          {item.kind === "provide_input" ? (
            <>
              <Typography.Text strong>
                {item.allowedActions.includes("supplement_input")
                  ? item.title.startsWith("可补充") ? "可补充研究信息" : "需要你补充信息才能继续"
                  : safeRightPanelText(item.title, "当前研究信息缺口")}
              </Typography.Text>
              {provideInputDetailLines(item, visibleArtifactIds).map(line => (
                <Typography.Text key={line}>{line}</Typography.Text>
              ))}
            </>
          ) : (
            <>
              <Typography.Text strong>{safeRightPanelText(item.title, "当前研究操作")}</Typography.Text>
              {item.objective ? <Typography.Text>{safeRightPanelText(item.objective, "当前研究步骤")}</Typography.Text> : null}
              {actionReasonPresentation(item, visibleArtifactIds) ? (
                <Typography.Text type="secondary">{actionReasonPresentation(item, visibleArtifactIds)}</Typography.Text>
              ) : null}
            </>
          )}
          {item.artifactRefs.length && renderArtifacts ? renderArtifacts(item.artifactRefs) : null}
          <div className="flex flex-wrap gap-2">
            {item.allowedActions.map(action => {
              const state = operationState(item, action);
              const busy = state === "submitting" || state === "pending" || state === "response_unknown";
              if (action === "supplement_input") {
                return (
                  <Button key={action} size="small" type="primary" disabled={busy} onClick={onSupplement}>
                    {USER_ACTION_LABELS[action]}
                  </Button>
                );
              }
              if (action === "explicit_retry") {
                if (hideExplicitRetry) return null;
                return (
                  <Popconfirm
                    key={action}
                    title="重试这一步？"
                    description={item.kind === "structured_output_retry_ready" ? "上次输出未通过检查。重试会创建一次新的执行，已有成果保留。" : "上次执行未成功完成。重试会创建一次新的执行。"}
                    okText="重试"
                    cancelText="取消"
                    onConfirm={() => onExplicitRetry(item)}
                  >
                    <Button
                      size="small"
                      type="primary"
                      danger
                      loading={state === "submitting"}
                      disabled={busy}
                      data-testid={`workbench-explicit-retry-${item.workUnitId ?? item.itemId}`}
                    >
                      {USER_ACTION_LABELS[action]}
                    </Button>
                  </Popconfirm>
                );
              }
              return null;
            })}
          </div>
          {item.allowedActions.map(action => {
            const stateCopy = actionStateMessage(action, operationState(item, action));
            return stateCopy ? (
              <Typography.Text key={`${action}-state`} type="secondary">
                {stateCopy}
              </Typography.Text>
            ) : null;
          })}
        </StreamItem>
      ))}
      {unrecoverableMessage != null ? (
        <StreamItem testId="workbench-unrecoverable-state">
          <Typography.Text strong>{safeRightPanelText(unrecoverableMessage, "当前研究暂不能继续，已有成果仍保留。")}</Typography.Text>
        </StreamItem>
      ) : null}
    </>
  );
}

function PendingDecisionStreamItems({
  decisions,
  deciding,
  onDecision
}: {
  decisions: CollaborationProjection["collaboration"]["pendingDecisions"];
  deciding: { gateId: string; action: CollaborationDecisionAction } | null;
  onDecision: (gateId: string, action: CollaborationDecisionAction) => void;
}) {
  return (
    <>
      {decisions.map(decision => {
        const isDeciding = deciding?.gateId === decision.gateId;
        const isExecutionPolicy = decision.kind === "execution_policy";
        const allowedActions = Array.isArray(decision.allowedActions) ? decision.allowedActions : [];
        return (
          <StreamItem key={decision.gateId} testId={`workbench-pending-decision-${decision.gateId}`}>
            <Typography.Text strong>
              {isExecutionPolicy ? "需要确认推进方式" : "审核未通过，需要你决定"}
            </Typography.Text>
            <Typography.Text>{decision.reason}</Typography.Text>
            <div className="flex flex-wrap gap-2">
              {allowedActions.includes("continue_run") ? (
                <Popconfirm
                  title="继续本轮一次？"
                  description="本轮将从当前安全边界继续。"
                  okText="继续一次"
                  cancelText="返回"
                  onConfirm={() => onDecision(decision.gateId, "continue_run")}
                >
                  <Button
                    size="small"
                    type="primary"
                    icon={<ReloadOutlined />}
                    loading={isDeciding && deciding.action === "continue_run"}
                    disabled={Boolean(deciding)}
                  >
                    继续一次
                  </Button>
                </Popconfirm>
              ) : null}
              {allowedActions.includes("start_revision_run") ? (
                <Popconfirm
                  title="重新执行一轮？"
                  description="会重新开始本轮研究。"
                  okText="重新执行一轮"
                  cancelText="返回"
                  onConfirm={() => onDecision(decision.gateId, "start_revision_run")}
                >
                  <Button
                    size="small"
                    type="primary"
                    icon={<ReloadOutlined />}
                    loading={isDeciding && deciding.action === "start_revision_run"}
                    disabled={Boolean(deciding)}
                  >
                    重新执行一轮
                  </Button>
                </Popconfirm>
              ) : null}
              {allowedActions.includes("terminate_run") ? (
                <Popconfirm
                  title="结束当前研究？"
                  description="当前研究将停止，已产生的记录和产物会保留。"
                  okText="结束研究"
                  cancelText="返回"
                  onConfirm={() => onDecision(decision.gateId, "terminate_run")}
                >
                  <Button
                    size="small"
                    danger
                    icon={<StopOutlined />}
                    loading={isDeciding && deciding.action === "terminate_run"}
                    disabled={Boolean(deciding)}
                  >
                    结束研究
                  </Button>
                </Popconfirm>
              ) : null}
            </div>
          </StreamItem>
        );
      })}
    </>
  );
}

function RecoveryStreamItem({
  projection,
  onReconcile
}: {
  projection: TaskRecoveryProjection;
  onReconcile: () => void;
}) {
  const busy = projection.state === "creating" || projection.state === "reconciling";
  return (
    <StreamItem testId="recovery-stream-item">
      <Typography.Text strong>{projection.display.title}</Typography.Text>
      <Typography.Text>{projection.display.reason}</Typography.Text>
      <Typography.Text type="secondary">未完成步骤：{projection.display.failedStep}</Typography.Text>
      {projection.display.retainedFacts.map((fact, index) => (
        <Typography.Text key={`${fact}-${index}`} type="secondary">
          {fact}
        </Typography.Text>
      ))}
      <Typography.Text type="secondary">{projection.display.nextStep}</Typography.Text>
      <div className="flex flex-wrap gap-2" data-testid="recovery-action-controls">
        {projection.authoritativeRefreshRequired ? (
          <Button size="small" disabled={busy} onClick={onReconcile} data-testid="recovery-action-reconcile">
            重新核对
          </Button>
        ) : null}
      </div>
    </StreamItem>
  );
}

export function WorkbenchInteractionPanel({
  taskId,
  model,
  artifacts,
  screen,
  collaboration = null,
  onCollaborationRefresh,
  executionPolicy = null,
  executionPolicyUnavailable = false,
  executionModeSaving = false,
  showExecutionControls = true,
  onExecutionModeChange,
  supplementNote,
  onSupplementNoteChange,
  controllerPendingMessage = null,
  controllerReceipt = null,
  onSubmitControllerSupplement,
  controllerSubmitting = false,
  onTaskReload,
  apiConnected = false,
  onSelectArtifact,
  continuingResearch = false,
  onContinueResearch,
  canStopExecution = false,
  stoppingExecution = false,
  onStopExecution,
  recoveryProjection = null,
  onRecoveryReconcile,
  recoveryVisible = false,
  planningAwaitingConfirmation = false,
  planningRevisionRequired = false,
  planningConfirming = false,
  planningConfirmDisabled = false,
  planningModifyDisabled = false,
  onConfirmPlanning,
  onModifyPlanning,
  canStartConfirmedResearch = false,
  startingConfirmedResearch = false,
  onStartConfirmedResearch,
  userActions,
  planningStatus = null,
  planningStatusReadUnavailable = false,
  planningStatusActive = false,
  workbenchUserState,
  onRetryPlanning,
  planningRetrying = false,
  onRecheckPlanningSession,
  showPvdFeedback = false,
}: WorkbenchInteractionPanelProps) {
  const { message } = App.useApp();
  const [maximized, setMaximized] = useState(false);
  const [pvdFeedbackVisible, setPvdFeedbackVisible] = useState(false);
  const [agentHistory, setAgentHistory] = useState<{ taskId: string; messages: AgentMessage[] }>({
    taskId: "",
    messages: []
  });
  const agentHistoryGenerationRef = useRef(0);
  const interactionScrollRef = useRef<HTMLDivElement | null>(null);
  const collaborationStreamRef = useRef<HTMLDivElement | null>(null);
  const scrollNearBottomRef = useRef(true);
  const scrollTaskIdRef = useRef<string | null>(null);
  const [deciding, setDeciding] = useState<{
    taskId: string;
    gateId: string;
    action: CollaborationDecisionAction;
  } | null>(null);
  const gateGenerationRef = useRef(0);
  const activeGateTaskIdRef = useRef(taskId);
  const decidingRef = useRef<{
    taskId: string;
    generation: number;
    gateId: string;
    action: CollaborationDecisionAction;
  } | null>(null);
  if (activeGateTaskIdRef.current !== taskId) {
    activeGateTaskIdRef.current = taskId;
    gateGenerationRef.current += 1;
    decidingRef.current = null;
  }
  const visibleDeciding = deciding?.taskId === taskId ? deciding : null;
  useEffect(() => {
    const generation = ++agentHistoryGenerationRef.current;
    if (!apiConnected || !taskId) return;
    void fetchAgentMessages(taskId)
      .then(result => {
        if (agentHistoryGenerationRef.current !== generation) return;
        setAgentHistory({ taskId, messages: result.messages });
      })
      .catch(() => {
        // Durable history is supplementary presentation input. Keep any already
        // visible same-task history and let the next authoritative Task revision retry.
      });
  }, [apiConnected, screen.revision, taskId]);
  const visibleAgentMessages = agentHistory.taskId === taskId ? agentHistory.messages : [];
  const planningWaitingView = projectResearchStartWaitingView(
    planningStatus,
    planningStatusReadUnavailable,
    { inWorkbench: true },
  );
  const planningProgress = planningStatus?.planningProgress;
  const serverPlanningElapsed = planningWaitingView.elapsedSeconds;
  const planningFrozen =
    planningWaitingView.terminal
    || planningWaitingView.progressCode === "design_ready"
    || planningStatus?.status === "completed"
    || planningStatus?.status === "failed"
    || planningStatus?.status === "external_result_unknown";
  const planningTimerFrozen = planningFrozen || !planningStatusActive;
  const [planningElapsedBase, setPlanningElapsedBase] = useState(serverPlanningElapsed);
  const [planningElapsedTick, setPlanningElapsedTick] = useState(0);
  if (serverPlanningElapsed !== planningElapsedBase) {
    setPlanningElapsedBase(serverPlanningElapsed);
    setPlanningElapsedTick(0);
  }
  useEffect(() => {
    if (planningTimerFrozen || serverPlanningElapsed == null) return undefined;
    const timer = window.setInterval(() => setPlanningElapsedTick((tick) => tick + 1), 1_000);
    return () => window.clearInterval(timer);
  }, [planningTimerFrozen, serverPlanningElapsed]);
  const planningElapsedLabel = formatElapsedSeconds(
    serverPlanningElapsed == null
      ? null
      : planningTimerFrozen
        ? Math.floor(serverPlanningElapsed)
        : Math.floor(serverPlanningElapsed) + planningElapsedTick,
  );
  const handleInteractionScroll = useCallback(() => {
    const viewport = interactionScrollRef.current;
    if (!viewport) return;
    scrollNearBottomRef.current = isCollaborationScrollNearBottom(viewport);
  }, []);
  useLayoutEffect(() => {
    scrollTaskIdRef.current = taskId;
    scrollNearBottomRef.current = true;
    const viewport = interactionScrollRef.current;
    if (viewport) {
      viewport.scrollTop = viewport.scrollHeight;
      scrollNearBottomRef.current = isCollaborationScrollNearBottom(viewport);
    }
    return () => {
      if (scrollTaskIdRef.current === taskId) scrollTaskIdRef.current = null;
    };
  }, [taskId]);
  useEffect(() => {
    const viewport = interactionScrollRef.current;
    const stream = collaborationStreamRef.current;
    if (!viewport || !stream || typeof ResizeObserver === "undefined") return undefined;
    let previousScrollHeight = 0;
    let followFrameId: number | null = null;
    const observerTaskId = taskId;
    const observer = new ResizeObserver(() => {
      const nextScrollHeight = stream.scrollHeight;
      const shouldFollow = shouldFollowCollaborationScroll({
        userNearBottom: scrollNearBottomRef.current,
        previousScrollHeight,
        nextScrollHeight
      });
      previousScrollHeight = nextScrollHeight;
      if (!shouldFollow) return;
      if (followFrameId !== null) window.cancelAnimationFrame(followFrameId);
      const scheduledScrollTop = viewport.scrollTop;
      followFrameId = window.requestAnimationFrame(() => {
        followFrameId = null;
        if (scrollTaskIdRef.current !== observerTaskId) return;
        const currentViewport = interactionScrollRef.current;
        if (!currentViewport) return;
        const latestNearBottom = scrollNearBottomRef.current;
        const userScrolledUpBeforeFrame = currentViewport.scrollTop < scheduledScrollTop;
        if (!latestNearBottom || userScrolledUpBeforeFrame) return;
        currentViewport.scrollTop = currentViewport.scrollHeight;
        scrollNearBottomRef.current = true;
      });
    });
    observer.observe(stream);
    return () => {
      observer.disconnect();
      if (followFrameId !== null) window.cancelAnimationFrame(followFrameId);
    };
  }, [taskId]);
  const actionSnapshot = currentWorkbenchActionSnapshot(userActions.snapshot, taskId, model.revision);
  // Action Authority stays on the server snapshot; this panel does not invent or suppress retries.
  const visibleActions: WorkbenchUserActionItem[] = actionSnapshot?.items ?? [];
  const recoveryLifecycleKind = actionSnapshot?.lifecycleKind ?? null;
  // Golden-path narration follows the same durable Task progress used by the left workflow.
  // Auxiliary collaboration reads may still supply decision/recovery controls, but never gate
  // whether active research is visible.
  const modeValue = executionPolicy?.taskId === taskId ? executionPolicy.currentMode : undefined;
  const activeResearchStep =
    screen.activeSubstage ??
    screen.leftStages
      .flatMap(stage => stage.substages ?? [])
      .find(substage => ["running", "queued", "reviewing", "preparing"].includes(substage.status)) ??
    null;
  const researchIsActive =
    Boolean(activeResearchStep) ||
    ["running", "queued", "reviewing", "preparing", "executing"].includes(screen.executionDisplayStatus);
  const orderedResearchSteps = screen.leftStages.flatMap(stage => stage.substages ?? []);
  const affectedSubstages = model.stages
    .flatMap(stage => stage.subStages ?? [])
    .filter(substage => substage.affected && substage.status === "completed");
  const activeResearchIndex = activeResearchStep
    ? orderedResearchSteps.findIndex(step => step.id === activeResearchStep.id)
    : -1;
  const canNarrateNextResearchStep = Boolean(
    activeResearchStep && ["running", "queued", "reviewing", "preparing"].includes(activeResearchStep.status)
  );
  const nextResearchStep =
    canNarrateNextResearchStep && activeResearchIndex >= 0
      ? (orderedResearchSteps
          .slice(activeResearchIndex + 1)
          .find(step => !["completed", "skipped"].includes(step.status)) ?? null)
      : null;
  const researchWorkUnitContexts =
    model.progressTrust.kind === "canonical"
      ? projectResearchWorkUnitContexts(model.progressTrust.projection.substageProgress)
      : model.collaborationNodes
          .filter(node => typeof node.workUnitId === "string" && node.workUnitId)
          .map(node => ({
            workUnitId: node.workUnitId as string,
            objective: node.objective ?? null
          }));
  const focusSupplement = () => {
    window.requestAnimationFrame(() => {
      document.querySelector<HTMLTextAreaElement>('[data-testid="workbench-supplement-draft"]')?.focus();
    });
  };
  const invalidateFromResearchActivity = useCallback(
    (scopes: string[]) => {
      // Research SSE is process-only. It may invalidate authoritative GETs, but never settles terminal, artifact, review, recovery, action, or receipt truth.
      const reload = authoritativeReloadPlan(scopes);
      if (reload.collaboration) onCollaborationRefresh?.();
      if (reload.controllerCommand) void userActions.controller.reconcile();
      if (reload.task) onTaskReload?.();
    },
    [onCollaborationRefresh, onTaskReload, userActions.controller.reconcile]
  );
  const submitControllerSupplement = async () => {
    const pendingMessage = supplementNote.trim();
    if (!pendingMessage || !onSubmitControllerSupplement || controllerSubmitting) return;
    await onSubmitControllerSupplement();
  };
  const decideGate = async (gateId: string, action: CollaborationDecisionAction) => {
    if (decidingRef.current || recoveryLifecycleKind !== "legacy_durable_run") return;
    const currentDecision = pendingDecisions.find((decision) => decision.gateId === gateId);
    if (!currentDecision?.allowedActions.includes(action)) return;
    const generation = gateGenerationRef.current;
    const fence = { taskId, generation, gateId, action };
    decidingRef.current = fence;
    setDeciding({ taskId, gateId, action });
    try {
      const coordinator = getWorkbenchTaskRequestCoordinator("workbench-page", "canonical", taskId);
      const result = (await coordinator.request(`action:collaboration-gate:${gateId}`, (signal: AbortSignal) =>
        decideTaskCollaborationGate(taskId, gateId, action, signal)
      )) as CollaborationDecisionResult | undefined;
      if (!result || activeGateTaskIdRef.current !== taskId || gateGenerationRef.current !== generation) return;
      message.success(result.userMessage);
      onCollaborationRefresh?.();
      onTaskReload?.();
    } catch (error) {
      if (activeGateTaskIdRef.current !== taskId || gateGenerationRef.current !== generation) return;
      message.error(error instanceof ApiError ? error.userMessage : "暂时无法提交决定，请稍后重试。");
      onCollaborationRefresh?.();
    } finally {
      if (decidingRef.current === fence) decidingRef.current = null;
      if (activeGateTaskIdRef.current === taskId && gateGenerationRef.current === generation) setDeciding(null);
    }
  };
  const pendingDecisions = (recoveryLifecycleKind === "legacy_durable_run"
    ? pendingDecisionsFromProjection(collaboration) : []) as CollaborationProjection["collaboration"]["pendingDecisions"];
  const visibleArtifactRows = projectRightPanelArtifacts(
    taskId,
    artifacts.map(artifact => ({ artifact, projection: projectWorkbenchArtifactState(artifact) })),
    model.resultSource,
    apiConnected
  ) as Array<{ id: string }>;
  const visibleArtifactIds = visibleArtifactRows.map(row => row.id);
  const artifactById = new Map(artifacts.map(artifact => [artifact.id, artifact]));
  const presentationArtifacts = visibleArtifactRows.flatMap(row => {
    const artifact = artifactById.get(row.id);
    return artifact
      ? [
          {
            id: artifact.id,
            createdAt: artifact.createdAt ?? null,
            sourceCapabilityCallId: artifact.sourceCapabilityCallId ?? null
          }
        ]
      : [];
  });
  const controls: CollaborationControlFact[] = [];
  // Optimistic pending bubble is only for the gap before durable history lands.
  // Once a controller_supplement user message is already in the stream (often while
  // receipt.state is still "received" / controller.busy), keep showing that message
  // and drop the pending echo — otherwise the panel briefly renders the same "你"
  // bubble twice until applied clears busy (#492).
  const pendingRepresentedByDurableMessage = Boolean(
    controllerPendingMessage &&
    visibleAgentMessages.some(item => {
      if (item.role !== "user") return false;
      if (controllerReceipt?.commandId && item.controllerCommandId === controllerReceipt.commandId) {
        return true;
      }
      return item.messageType === "controller_supplement" && item.text.trim() === controllerPendingMessage.trim();
    })
  );
  if (controllerPendingMessage && !pendingRepresentedByDurableMessage) {
    controls.push({
      id: "pending-user-submission",
      kind: "pending_user_submission",
      occurredAt: null,
      source: { pendingMessage: controllerPendingMessage }
    });
  }
  // Durable controller_supplement user messages carry controllerCommandId as
  // provenance only. They prove what the user said, not received/applied/rejected.
  // There is currently no message type that itself carries authoritative receipt
  // semantics, so never suppress controllerReceipt by ordinary controllerCommandId.
  if (controllerReceipt) {
    controls.push({
      id: `controller-receipt:${controllerReceipt.commandId}`,
      kind: "controller_receipt",
      occurredAt: controllerReceipt.submittedAt,
      workUnitId: controllerReceipt.appliedWorkUnitId,
      source: controllerReceipt
    });
  }
  for (const decision of pendingDecisions) {
    controls.push({
      id: `pending-decision:${decision.gateId}`,
      kind: "pending_decision",
      occurredAt: decision.createdAt,
      artifactRefs: decision.artifactRefs.map(ref => ref.id),
      source: decision
    });
  }
  if (planningAwaitingConfirmation && onConfirmPlanning && onModifyPlanning) {
    controls.push({
      id: "planning-confirmation",
      kind: "planning_confirmation",
      occurredAt: null,
      source: { state: "awaiting_confirmation" }
    });
  } else if (planningRevisionRequired && onModifyPlanning) {
    controls.push({
      id: "planning-revision-required",
      kind: "planning_confirmation",
      occurredAt: null,
      source: { state: "revision_requested" }
    });
  }
  if (canStartConfirmedResearch && onStartConfirmedResearch) {
    controls.push({ id: "start-confirmed-research", kind: "start_confirmed_research", occurredAt: null, source: null });
  }
  if (
    hasWorkbenchTaskAction(actionSnapshot, taskId, model.revision, "continue_research")
    && onContinueResearch
    && !actionSnapshot?.executionStopped
  ) {
    const confirmationTarget =
      orderedResearchSteps.find(step =>
        ["awaiting_user_confirmation", "awaiting_confirmation", "ready"].includes(step.status)
      ) ?? nextResearchStep;
    controls.push({
      id: "continue-research",
      kind: "continue_research",
      occurredAt: null,
      source: {
        nextObjective:
          confirmationTarget?.objective ??
          confirmationTarget?.label ??
          nextResearchStep?.objective ??
          nextResearchStep?.label ??
          null
      }
    });
  }
  if (actionSnapshot?.unrecoverableMessage != null && !model.interruption) {
    controls.push({
      id: "unrecoverable-state",
      kind: "unrecoverable_state",
      occurredAt: null,
      source: actionSnapshot.unrecoverableMessage
    });
  }
  if (recoveryProjection && recoveryVisible && recoveryLifecycleKind === "legacy_durable_run") {
    controls.push({
      id: `recovery:${recoveryProjection.technical.recoveryRequestId ?? recoveryProjection.state}`,
      kind: "recovery",
      occurredAt: null,
      source: recoveryProjection
    });
  }
  if (canStopExecution && onStopExecution && hasWorkbenchTaskAction(actionSnapshot, taskId, model.revision, "stop")) {
    controls.push({ id: "stop-research", kind: "stop_research", occurredAt: null, source: null });
  }

  const blockingProvideItems = visibleActions.filter(item =>
    item.kind === "provide_input" && item.allowedActions.includes("supplement_input") && !item.title.startsWith("可补充"),
  );
  const executionInterruption = workbenchUserState === "plan_generation_failed" ? null : model.interruption;
  const paused =
    executionInterruption?.kind === "user_stop"
    || executionInterruption?.kind === "execution_cancelled"
    || executionInterruption?.kind === "system_failure"
    || executionInterruption?.kind === "external_result_unknown"
    || Boolean(userActions.snapshot?.executionStopped);
  const retryItem = visibleActions.find((item) => item.allowedActions.includes("explicit_retry")) ?? null;
  const canContinue = Boolean(
    onContinueResearch
    && hasWorkbenchTaskAction(actionSnapshot, taskId, model.revision, "continue_research"),
  );
  const userStopped =
    executionInterruption?.kind === "user_stop"
    || executionInterruption?.kind === "execution_cancelled"
    || Boolean(userActions.snapshot?.executionStopped);
  const failurePause =
    executionInterruption?.kind === "system_failure"
    || executionInterruption?.kind === "external_result_unknown";
  const dockPlanningRetry =
    planningStatusActive
    && planningStatus?.status === "failed"
    && Boolean(onRetryPlanning);
  const dockExecutionAction: "continue" | "retry" | null =
    planningStatusActive || !paused
      ? null
      : failurePause && retryItem
        ? "retry"
        : userStopped && canContinue
          ? "continue"
          : retryItem
            ? "retry"
            : canContinue
              ? "continue"
              : null;
  const dockLeftAction: "continue" | "retry" | null = dockPlanningRetry
    ? "retry"
    : dockExecutionAction;
  const retryBusy = retryItem
    ? ["submitting", "pending", "response_unknown"].includes(userActions.operationState(retryItem, "explicit_retry").state)
    : false;

  function artifactReference(artifactIds: string[], compact = false): ReactNode {
    return (
      <ArtifactReferenceStreamItem
        taskId={taskId}
        artifacts={artifacts}
        taskResultSource={model.resultSource}
        apiConnected={apiConnected}
        onSelectArtifact={onSelectArtifact}
        artifactIds={artifactIds}
        compact={compact}
      />
    );
  }

  function renderPresentationNode(
    node: CollaborationPresentationNode,
    research: ResearchActivityPresentationApi
  ): ReactNode {
    const related = node.related
      .filter(item => !(node.kind === "research_activity" && item.kind === "artifact"))
      .map(item => (
        <div key={item.id} className="pl-3">
          {renderPresentationNode(item, research)}
        </div>
      ));
    const relatedContent = related.length ? <>{related}</> : undefined;

    if (node.kind === "research_activity") {
      return research.renderFact(node.id, relatedContent, {
        needsInput: node.related.some(
          item => item.kind === "action_required" && (item.source as WorkbenchUserActionItem).kind === "provide_input"
            && (item.source as WorkbenchUserActionItem).allowedActions.includes("supplement_input")
        )
      });
    }
    if (node.kind === "user_intervention" || node.kind === "research_result") {
      const messageItem = node.source as AgentMessage;
      const messageArtifacts = node.related
        .filter(item => item.kind === "artifact")
        .map(item => String((item.source as { id: string }).id));
      return (
        <AgentMessageStreamItem
          message={messageItem}
          visibleArtifactIds={visibleArtifactIds}
          relatedContent={messageArtifacts.length ? artifactReference(messageArtifacts) : relatedContent}
        />
      );
    }
    if (node.kind === "action_required") {
      return (
        <UserActionStreamItems
          items={[node.source as WorkbenchUserActionItem]}
          visibleArtifactIds={visibleArtifactIds}
          onSupplement={focusSupplement}
          onExplicitRetry={item => void userActions.explicitRetry(item, true)}
          hideExplicitRetry={dockLeftAction === "retry"}
          operationState={(item, action) =>
            action === "supplement_input"
              ? userActions.controller.operationState
              : userActions.operationState(item, action).state
          }
          renderArtifacts={artifactIds => artifactReference(artifactIds, true)}
        />
      );
    }
    if (node.kind === "artifact") {
      return artifactReference([String((node.source as { id: string }).id)]);
    }
    if (node.kind !== "control") return null;

    if (node.controlKind === "pending_user_submission") {
      return (
        <ControllerConversationStreamItems
          pendingMessage={(node.source as { pendingMessage: string }).pendingMessage}
          receipt={null}
        />
      );
    }
    if (node.controlKind === "controller_receipt") {
      return <ControllerConversationStreamItems pendingMessage={null} receipt={node.source as ControllerCommand} />;
    }
    if (node.controlKind === "pending_decision") {
      return (
        <PendingDecisionStreamItems
          decisions={[node.source as CollaborationProjection["collaboration"]["pendingDecisions"][number]]}
          deciding={visibleDeciding}
          onDecision={(gateId, action) => void decideGate(gateId, action)}
        />
      );
    }
    if (node.controlKind === "planning_confirmation") {
      const state = (node.source as { state?: string }).state;
      const awaitingConfirmation = state === "awaiting_confirmation";
      return (
        <StreamItem testId="workbench-planning-confirmation-stream-item">
          <Typography.Text strong>{awaitingConfirmation ? "研究方案已准备好" : "研究方案需要调整"}</Typography.Text>
          <Typography.Text type="secondary">
            {awaitingConfirmation
              ? "请确认研究目标和执行方向；建议确认后开始研究。"
              : "当前方案还不能执行；请调整后再确认。"}
          </Typography.Text>
          <div className="flex flex-wrap gap-2">
            {onModifyPlanning ? (
              <Button
                size="small"
                disabled={planningModifyDisabled}
                onClick={onModifyPlanning}
                data-testid="workbench-stream-edit-plan"
              >
                修改研究方案
              </Button>
            ) : null}
            {awaitingConfirmation && onConfirmPlanning ? (
              <Button
                size="small"
                type="primary"
                loading={planningConfirming}
                disabled={planningConfirmDisabled}
                onClick={onConfirmPlanning}
                data-testid="workbench-stream-confirm-start"
              >
                确认并开始
              </Button>
            ) : null}
          </div>
        </StreamItem>
      );
    }
    if (node.controlKind === "continue_research" && onContinueResearch) {
      const resumingStoppedResearch = Boolean(actionSnapshot?.executionStopped);
      const nextObjective = String((node.source as { nextObjective?: string | null })?.nextObjective ?? "").trim();
      return (
        <StreamItem testId="workbench-continue-research-stream-item">
          <Typography.Text strong>
            {resumingStoppedResearch
              ? "研究已停止"
              : modeValue === "stage_confirmation"
                ? "需要你确认是否进入下一阶段"
                : "需要你确认是否进入下一步"}
          </Typography.Text>
          {!resumingStoppedResearch ? (
            <Typography.Text type="secondary">
              {nextObjective
                ? `确认后将进入：${nextObjective}。已完成的内容会保留。`
                : modeValue === "stage_confirmation"
                  ? "本阶段已经完成；确认后进入下一阶段，已完成的内容会保留。"
                  : "当前工作已经完成；确认后进入下一步，已完成的内容会保留。"}
            </Typography.Text>
          ) : null}
          <div>
            <Button
              size="small"
              type="primary"
              icon={<ReloadOutlined />}
              loading={continuingResearch}
              disabled={continuingResearch}
              onClick={onContinueResearch}
              data-testid="workbench-continue-research"
            >
              {resumingStoppedResearch
                ? "继续研究"
                : modeValue === "stage_confirmation"
                  ? "确认并继续下一阶段"
                  : "继续下一步"}
            </Button>
          </div>
        </StreamItem>
      );
    }
    if (node.controlKind === "start_confirmed_research" && onStartConfirmedResearch) {
      return (
        <StreamItem testId="workbench-start-confirmed-research-stream-item">
          <Typography.Text strong>研究方案已确认</Typography.Text>
          <Typography.Text type="secondary">系统已准备好按确认的研究目标和范围开始执行。</Typography.Text>
          <div>
            <Button
              size="small"
              type="primary"
              loading={startingConfirmedResearch}
              disabled={startingConfirmedResearch}
              onClick={onStartConfirmedResearch}
              data-testid="workbench-start-confirmed-research"
            >
              开始研究
            </Button>
          </div>
        </StreamItem>
      );
    }
    if (node.controlKind === "unrecoverable_state") {
      return (
        <UserActionStreamItems
          items={[]}
          unrecoverableMessage={String(node.source)}
          visibleArtifactIds={visibleArtifactIds}
          onSupplement={focusSupplement}
          onExplicitRetry={item => void userActions.explicitRetry(item, true)}
          operationState={(item, action) =>
            action === "supplement_input"
              ? userActions.controller.operationState
              : userActions.operationState(item, action).state
          }
        />
      );
    }
    if (node.controlKind === "recovery") {
      return (
        <RecoveryStreamItem
          projection={node.source as TaskRecoveryProjection}
          onReconcile={onRecoveryReconcile ?? (() => undefined)}
        />
      );
    }
    if (node.controlKind === "stop_research" && onStopExecution) {
      return (
        <StreamItem testId="workbench-stop-stream-item">
          <Typography.Text strong>停止当前研究</Typography.Text>
          <Typography.Text type="secondary">已形成的内容会保留。</Typography.Text>
          <div>
            <Popconfirm
              title="停止当前研究？"
              description="停止后，已经形成的内容会保留。"
              okText="停止"
              cancelText="继续研究"
              onConfirm={onStopExecution}
            >
              <Button size="small" data-testid="workbench-collaboration-stop-run" danger loading={stoppingExecution}>
                停止
              </Button>
            </Popconfirm>
          </div>
        </StreamItem>
      );
    }
    if (node.controlKind === "supplement_terminal_reason") {
      return (
        <StreamItem testId="workbench-controller-supplement-terminal-reason">
          <Typography.Text type="secondary">{String(node.source)}</Typography.Text>
        </StreamItem>
      );
    }
    return null;
  }

  const waitingGateLines = [...new Set(blockingProvideItems.flatMap(item => provideInputDetailLines(item, visibleArtifactIds)))].slice(0, 4);
  const supplementPlaceholder = blockingProvideItems.length
    ? "请回答上方需要补充的具体问题；也可直接说明你希望如何继续"
    : "向研究助手说明你的需求（提问、补充、重试等）";
  const canSendComposer =
    Boolean(onSubmitControllerSupplement) &&
    Boolean(supplementNote.trim()) &&
    !controllerSubmitting;

  return (
    <div
      className={
        maximized
          ? "fixed inset-3 z-modal relative flex min-h-0 flex-col overflow-hidden bg-surface shadow-overlay"
          : "relative flex h-full min-h-0 flex-col overflow-hidden bg-surface"
      }
      data-execution-status={screen.executionDisplayStatus}
      data-live-research={screen.isLiveResearch ? "true" : "false"}
      data-testid="workbench-interaction-panel-root"
      data-workbench-user-state={workbenchUserState ?? ""}
    >
      <header className="shrink-0 border-b border-border bg-surface px-3 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3 flex-1">
            <Typography.Title level={5} className="m-0!">
              交互过程
            </Typography.Title>
            {showExecutionControls && (
              <DepthSegmentedControl<TaskExecutionMode>
                testId="workbench-execution-mode-control"
                ariaLabel="推进方式"
                startLabel="自动"
                endLabel="手动"
                rootClassName="max-w-[160px] flex-1"
                value={modeValue ?? "automatic_research"}
                options={EXECUTION_MODE_OPTIONS}
                disabled={
                  executionModeSaving ||
                  !apiConnected ||
                  executionPolicy?.taskId !== taskId ||
                  executionPolicyUnavailable ||
                  !onExecutionModeChange ||
                  ["completed", "failed", "cancelled"].includes(model.taskStatus)
                }
                onChange={value => onExecutionModeChange?.(value)}
              />
            )}
          </div>
          <Button
            type="text"
            size="small"
            aria-label={maximized ? "退出最大化" : "最大化交互过程"}
            icon={maximized ? <CompressOutlined /> : <ExpandOutlined />}
            onClick={() => setMaximized(value => !value)}
          />
        </div>
        {showExecutionControls && (
          <div className="mt-3 grid gap-1.5" data-testid="workbench-execution-mode-section">
            {executionPolicy?.taskId !== taskId || executionPolicyUnavailable ? (
              <Typography.Text type="secondary" className="text-xs">
                当前推进方式暂不可确认。
              </Typography.Text>
            ) : executionPolicy.pendingMode ? (
              <Typography.Text type="secondary" className="text-xs">
                已选择“
                {EXECUTION_MODE_OPTIONS.find(item => item.value === executionPolicy.pendingMode)?.label ??
                  "新的推进方式"}
                ”，将在当前步骤结束后生效。
              </Typography.Text>
            ) : null}
          </div>
        )}
      </header>
      <div
        ref={interactionScrollRef}
        className="min-h-0 flex-1 overflow-y-auto px-3 py-2 pb-28"
        data-testid="workbench-interaction-scroll"
        onScroll={handleInteractionScroll}
      >
        <div
          ref={collaborationStreamRef}
          className="grid gap-0"
          role="list"
          data-testid="continuous-interaction-stream"
        >
          {planningStatusActive ? <WorkbenchPlanningStatus planningStatus={planningStatus}
            planningWaitingView={planningWaitingView} planningElapsedLabel={planningElapsedLabel}
            onRecheckPlanningSession={onRecheckPlanningSession} /> : null}
          {affectedSubstages.length > 0 ? (
            <StreamItem testId="workbench-affected-impact">
              <Typography.Text strong>你修改了较早的研究产物。</Typography.Text>
              <Typography.Text type="secondary">
                以下已完成步骤可能受到影响：{affectedSubstages.map(substage => substage.label).join("、")}
              </Typography.Text>
              <Typography.Text type="secondary" className="text-xs">
                {affectedSubstages.map(substage => `${substage.label}：已完成 · 可能受上游修改影响`).join("；")}
              </Typography.Text>
              <Typography.Text type="secondary" className="text-xs">
                当前结果仍保留，系统不会自动重新运行。
              </Typography.Text>
            </StreamItem>
          ) : null}
          {!apiConnected && !planningStatusActive ? (
            <StreamItem testId="workbench-collaboration-offline-item">
              <Typography.Text strong>暂时无法读取任务进展</Typography.Text>
              <Typography.Text type="secondary">已有记录保留，恢复后会继续同步。</Typography.Text>
            </StreamItem>
          ) : null}
            <ResearchActivityStream
              taskId={taskId}
              enabled={apiConnected}
              active={!planningAwaitingConfirmation && (researchIsActive || planningStatusActive) && !planningFrozen && executionInterruption?.kind !== "user_stop" && executionInterruption?.kind !== "execution_cancelled"}
              halted={executionInterruption?.kind === "user_stop" || executionInterruption?.kind === "execution_cancelled" || Boolean(userActions.snapshot?.executionStopped)}
              haltedAt={executionInterruption?.controlFact?.occurredAt ?? null}
              activityStartedAt={planningStatusActive ? (planningProgress?.startedAt ?? null) : null}
              activityEndedAt={planningFrozen ? (planningProgress?.updatedAt ?? null) : null}
              activeObjective={activeResearchStep?.objective ?? activeResearchStep?.label ?? null}
              nextObjective={executionInterruption ? null : nextResearchStep?.objective ?? nextResearchStep?.label ?? null}
              workUnitContexts={researchWorkUnitContexts}
              artifacts={artifacts}
              durableActivities={
                workbenchUserState === "plan_generation_failed"
                  ? model.researchProcessHistory.filter((row) => row.eventId !== "durable:task:terminal")
                  : model.researchProcessHistory
              }
              canonicalCurrentState={model.researchCurrentState}
              onSelectArtifact={onSelectArtifact}
              onInvalidate={invalidateFromResearchActivity}
            >
              {research => {
                const presentationNodes = composeCollaborationPresentation({
                  researchFacts: research.facts,
                  messages: visibleAgentMessages,
                  actions: visibleActions,
                  artifacts: presentationArtifacts,
                  controls
                }) as CollaborationPresentationNode[];
                return (
                  <>
                    {executionInterruption || planningStatusActive ? null : research.summary}
                    {presentationNodes.map(node => (
                      <div key={node.id} data-collaboration-node-kind={node.kind}>
                        {renderPresentationNode(node, research)}
                      </div>
                    ))}
                    {research.connection}
                  </>
                );
              }}
            </ResearchActivityStream>
        </div>
      </div>

      <footer
        className="absolute inset-x-0 bottom-0 z-10 px-3 pb-3"
        data-testid="workbench-collaboration-input-zone"
      >
        {waitingGateLines.length ? (
          <div
            className="mb-2 grid gap-1 rounded-lg border border-warning/40 bg-warning-soft px-3 py-2"
            data-testid="workbench-waiting-gate-prompt"
          >
            <Typography.Text strong>需要你补充信息才能继续</Typography.Text>
            {waitingGateLines.map(line => (
              <Typography.Text key={line}>{line}</Typography.Text>
            ))}
          </div>
        ) : null}
        {showPvdFeedback && !pvdFeedbackVisible ? (
          <div className="mb-2">
            <Button
              size="small"
              type="default"
              onClick={() => setPvdFeedbackVisible(true)}
              data-testid="pvd-feedback-toggle-button"
            >
              提交实验反馈
            </Button>
          </div>
        ) : null}
        {pvdFeedbackVisible ? (
          <div className="mb-2">
            <PvdFeedbackPanel
              taskId={taskId}
              taskRevision={model.revision}
              onSubmitted={() => {
                setPvdFeedbackVisible(false);
                message.success("实验反馈已提交，系统将继续分析。");
                onTaskReload?.();
              }}
              onCancel={() => setPvdFeedbackVisible(false)}
            />
          </div>
        ) : null}
        <div className="rounded-2xl border border-white/10 bg-surface-muted px-3 py-2 shadow-[0_12px_32px_rgba(0,0,0,0.55)]">
          <Input.TextArea
            variant="borderless"
            value={supplementNote}
            onChange={event => onSupplementNoteChange(event.target.value)}
            placeholder={supplementPlaceholder}
            autoSize={{ minRows: 2, maxRows: 4 }}
            data-testid="workbench-supplement-draft"
            className="bg-transparent! p-0! text-sm! leading-6! [&_textarea]:bg-transparent! [&_.ant-input]:bg-transparent!"
            onPressEnter={event => {
              if (event.shiftKey || !canSendComposer) return;
              event.preventDefault();
              void submitControllerSupplement();
            }}
          />
          <div className="flex items-center justify-between gap-2">
            {dockLeftAction === "continue" ? (
              <Button
                size="small"
                loading={continuingResearch}
                disabled={continuingResearch}
                onClick={onContinueResearch}
                data-testid="workbench-dock-continue"
              >
                继续
              </Button>
            ) : dockLeftAction === "retry" && dockPlanningRetry && onRetryPlanning ? (
              <Button
                size="small"
                loading={planningRetrying}
                disabled={planningRetrying}
                onClick={onRetryPlanning}
                data-testid="workbench-dock-retry"
              >
                重试
              </Button>
            ) : dockLeftAction === "retry" && retryItem ? (
              <Button
                size="small"
                loading={retryBusy}
                disabled={retryBusy}
                onClick={() => void userActions.explicitRetry(retryItem, true)}
                data-testid="workbench-dock-retry"
              >
                重试
              </Button>
            ) : <span />}
            <Button
              type="primary"
              shape="circle"
              size="small"
              icon={<ArrowUpOutlined />}
              loading={controllerSubmitting}
              disabled={!canSendComposer}
              onClick={() => void submitControllerSupplement()}
              aria-label="发送"
              data-testid="workbench-running-submit-controller-supplement"
            />
          </div>
        </div>
      </footer>
    </div>
  );
}
