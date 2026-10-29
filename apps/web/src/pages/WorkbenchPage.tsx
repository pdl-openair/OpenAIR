import { useResearchStartDisplayContext } from "../hooks/useResearchStartDisplayContext";
import { WorkbenchOriginalInput } from "../components/workbench/WorkbenchOriginalInput";
/**
 * 主工作台（三栏）：左「任务流程」（一级阶段 + 嵌套二级节点）/ 中产物渲染 / 右侧对话推进。
 */
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  App as AntdApp,
  Alert,
  Button,
  Input,
  Divider,
  Segmented,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  PanelLeft,
  PanelLeftClose,
  PanelRight,
  PanelRightClose,
  PanelTop,
  PanelTopClose,
} from "lucide-react";
import type {
  TaskExecutionMode,
  TaskPlan,
  TaskPlanStage,
  TaskPlanUpdate,
} from "@openair/shared";
import { STAGE_LABELS, STATUS_LABELS, TASK_STAGE_ORDER } from "@openair/shared";
import { usePageHeader } from "../context/PageHeaderContext";
import { useAuth } from "../context/AuthContext";
import {
  agentReviseResearchDesign,
  confirmResearchDesign,
  confirmTaskPlan,
  fetchTaskExecutionPolicy,
  fetchTask,
  startTask,
  updateTaskExecutionPolicy,
  updateTaskPlan,
  type ControllerCommand,
  RecoveryApiError,
  type RecoveryRequestResponse,
  fetchRecoveryRequest,
  discoverRecoveryRequest,
  type ResearchStartConfirmationPayload,
  type ResearchStartSessionStatus,
} from "../api/client";
import { postAgentChatStream } from "../api/agent";
import type { AppliedCollaborationAction } from "../api/agent";
import { planningSessionAfterRegeneration } from "../lib/planRegenerationNavigation";
import {
  fetchCurrentTaskStartStatus,
  fetchTaskStartStatus,
  isTaskStartStatusFenceCurrent,
  readDurableStartIntent,
  rememberDurableStartIntent,
  settleDurableStartIntent,
  taskStartStatusMessage,
  type CanonicalTaskStartState,
  type CanonicalTaskStartStatus,
  type StartRequestReceipt,
  type TaskStartStatusFence,
} from "../api/taskStartStatus";
import { ArtifactVersionDrawer } from "../components/artifacts/ArtifactVersionDrawer";
import { resolveEvidenceGraphResearchRecordsArtifacts } from "../components/artifacts/evidenceGraph";
import {
  WORKFLOW_NODE_RESULT_PRESENTATION_ORDER,
  workflowNodeResultPresentationKind,
} from "../components/artifacts/WorkflowNodeResultPresentation";
import { FixtureDisclosureAlert } from "../components/FixtureDisclosureAlert";
import {
  ReplayResultDisclosure,
  ResultSourceTag,
} from "../components/ResultSourceIndicator";
import { isTerminalTaskStatus } from "../lib/terminalTaskActionTruth.mjs";
import { TASK_LOAD_ZH } from "../lib/taskLoadError.mjs";
import { WorkbenchResearchNavigation } from "../components/workbench/WorkbenchResearchNavigation";
import { ConfirmedResearchPlan } from "../components/workbench/ConfirmedResearchPlan";
import { WorkbenchInteractionPanel } from "../components/workbench/WorkbenchInteractionPanel";
import {
  ResearchStartPlanningSync,
  type PlanningSessionAccessError,
} from "../components/workbench/WorkbenchPlanningBootstrap";
import {
  WorkbenchPrimaryResearchDelivery,
  WorkbenchSupportingArtifact,
} from "../components/workbench/WorkbenchResearchDelivery";
import { ResearchInterruptionPanel } from "../components/workbench/ResearchInterruptionPanel";
import { SubstageArtifactFooter } from "../components/workbench/SubstageArtifactFooter";
import { SubstageProgressPanel } from "../components/workbench/SubstageProgressPanel";
import { buildSubstageArtifactDeepLink } from "../components/workbench/substageArtifactFooterCore.mjs";
import {
  hasWorkbenchTaskAction,
  researchInterruptionPresentation,
} from "../lib/workbenchInterruption.mjs";
import { DraftPlanEditorModal } from "../components/workbench/DraftPlanEditorModal";
import { isUnderlyingStage } from "../data/recordedRunAdapter";
import { useDurableRunProjection } from "../hooks/useDurableRunProjection";
import { useCollaborationProjection } from "../hooks/useCollaborationProjection";
import { useHydratedModel } from "../hooks/useHydratedWorkbenchModel";
import { useWorkbenchTaskModel } from "../hooks/useWorkbenchTaskModel";
import { useWorkbenchAuthority } from "../hooks/useWorkbenchAuthority";
import { useWorkbenchPanelWidths } from "../hooks/useWorkbenchPanelWidths";
import {
  useWorkbenchSessionFields,
  useWorkbenchSessionState,
} from "../hooks/useWorkbenchSessionState";
import { useTaskRecoveryProjection } from "../hooks/useTaskRecoveryProjection";
import { useWorkbenchUserActions } from "../hooks/useWorkbenchUserActions";
import {
  artifactsByRefs,
  artifactsForExplicitScope,
  artifactViewSnapshot,
  chooseFocusedArtifactId,
  defaultSubStageKey,
  reconcileArtifactViewSelection,
  resolveArtifactLocation,
  shouldAutoFollowCurrentArtifact,
  type ArtifactScope,
  type ArtifactViewOwnership,
} from "../lib/artifactView";
import {
  PAGE_PHASE_LABEL,
  PAGE_PHASE_ORDER,
  pagePhaseForUnderlying,
  type PagePhaseId,
  type PhaseNavStatus,
} from "../lib/pagePhases";
import {
  artifactHumanGate,
  buildWorkbenchDecisionState,
  isResearchStartDraftArtifact,
  readWorkbenchLocal,
  userVisibleWorkbenchArtifacts,
  writeWorkbenchLocal,
} from "../lib/workbenchUi";
import {
  userFacingDynamicText,
  userFacingErrorText,
} from "../lib/userFacingLanguage";
import {
  buildProgressSteps,
  type WorkbenchProgressStep,
} from "../lib/workbenchProgress";
import {
  researchDesignOverview,
  researchDraftNodeAction,
  researchDraftNodeTitle,
  researchDraftStageDescription,
  researchDraftStageTitle,
  researchDraftText,
} from "../lib/researchDesignPresentation";
import {
  readRecoveryControllerMessages,
  readRecoveryRequestCache,
  writeRecoveryRequestCache,
  type RecoveryRequestCacheEntry,
} from "../lib/taskMessageIdempotency";
import { cn } from "../lib/cn";
import {
  researchDesignEditSubstageKey,
  researchDesignEditDraftFrom,
  researchDesignEditDraftRebaseOnLatest,
  researchDesignEditRemainingCount,
  researchDesignEditDraftDirty,
  researchDesignEditHasBlankWork,
  researchDesignEditUpdateFrom,
  requiredCanonicalEditText,
  researchDesignAvailableSubStages,
  type ResearchDesignEditDraft,
} from "../lib/researchDesignEdit";
import { useResearchDesignCatalog } from "../hooks/useResearchDesignCatalog";
import { ResearchDesignWorkTypeSelect } from "../components/workbench/ResearchDesignWorkTypeSelect";
import type {
  RecoveryFailureContext,
  RecoveryFence,
} from "../lib/taskRecovery";
import {
  readCurrentArtifact,
  readCurrentTask,
  readResearchStartPending,
  readResearchStartPendingForSession,
  rememberCurrentArtifact,
  rememberResearchStartAccepted,
} from "../lib/userRouteState";
import {
  buildWorkbenchScreenView,
  mapScreenStatusToNav,
  phaseAggFromScreen,
  progressStepsFromScreen,
} from "../model/workbenchScreenView";
import {
  formingWorkbenchTaskModel,
  pagePhaseAggregateStatus,
  stageRowForPagePhase,
  type WorkbenchArtifact,
  type WorkbenchTaskModel,
  workbenchModelFromApiTask,
} from "../model/workbenchTask";
import { planningOccupiesWorkbenchShell } from "../lib/workbenchPlanningShell";
import {
  isPlanGenerationFailure,
  projectWorkbenchUserState,
} from "../lib/workbenchUserState";
import { retryResearchStartFromPending } from "../lib/retryResearchStartFromPending";
import { userFacingResearchStartError } from "../lib/researchStart";

const START_STATUS_CHECKING_MESSAGE =
  "系统正在核对当前任务版本是否可以安全启动研究。";

/** 产物正文（/content，UTF-8 文本）不适用、也无需加载的媒体格式。
 *  png/jpg 走 /raw 二进制流（<img>），其 /content 必然 utf-8 解码失败，
 *  被失败态挡住渲染（svg 是文本可加载，统一跳过保持一致）。 */
const NON_BODY_ARTIFACT_FORMATS = new Set([
  "pdf",
  "directory",
  "png",
  "jpg",
  "jpeg",
  "svg",
]);

type MobileWorkbenchPane = "stage" | "artifact" | "support";

/** Golden-path collaboration mode labels; truth values stay frozen. */
export const EXECUTION_MODE_OPTIONS: Array<{
  label: string;
  value: TaskExecutionMode;
}> = [
  { label: "自动研究", value: "automatic_research" },
  { label: "分阶段确认", value: "stage_confirmation" },
  { label: "手动推进", value: "manual_step" },
];

type RecoveryPhase = "idle" | "creating" | "confirming" | "reconciling";
type RecoverySession = {
  response: RecoveryRequestResponse | null;
  cache: RecoveryRequestCacheEntry | null;
  phase: RecoveryPhase;
  responseUnknown: boolean;
  error: RecoveryApiError | null;
};

const EMPTY_RECOVERY_SESSION: RecoverySession = {
  response: null,
  cache: null,
  phase: "idle",
  responseUnknown: false,
  error: null,
};

function recoveryFenceKey(fence: RecoveryFence): string {
  return `${fence.taskId}\u0000${fence.expectedTaskRevision}\u0000${fence.sourceRunId}`;
}

function isPagePhaseId(v: string | null): v is PagePhaseId {
  return v !== null && (PAGE_PHASE_ORDER as string[]).includes(v);
}

const EMPTY_EDITOR_SESSION = {
  mode: false,
  draft: null as ResearchDesignEditDraft | null,
  error: null as string | null,
  historyVersion: null as number | null,
  undoingVersion: null as number | null,
  instruction: "",
};

type ResearchDesignEditConversationItem = {
  id: string;
  role: "user" | "assistant";
  text: string;
  state?: "success" | "failed";
};

function researchDesignHistoryTime(value: string): string {
  const normalized = value?.trim() ?? "";
  return normalized
    ? normalized.replace("T", " ").replace(/Z$/, "").slice(0, 16)
    : "";
}

type ResearchDesignHistoryEntry =
  WorkbenchTaskModel["researchDesignHistory"][number];

type ResearchDesignSemanticChange = {
  key: string;
  label: string;
  before: string;
  after: string;
};

function researchDesignHistoryInstanceLabels(
  plan: TaskPlan,
): Map<string, string> {
  const labels = new Map<string, string>();
  for (const stage of plan.stages) {
    (stage.substages ?? []).forEach((substage, index) => {
      if (!substage.instanceId) return;
      const stageLabel = STAGE_LABELS[stage.stageKey] ?? stage.title;
      const workLabel = substage.label?.trim() || `研究工作 ${index + 1}`;
      labels.set(substage.instanceId, `${stageLabel} · ${workLabel}`);
    });
  }
  return labels;
}

function researchDesignSemanticDiff(
  previous: ResearchDesignHistoryEntry | null,
  current: ResearchDesignHistoryEntry,
  plan: TaskPlan,
): ResearchDesignSemanticChange[] {
  if (!previous) return [];
  const labels = researchDesignHistoryInstanceLabels(plan);
  const previousById = new Map(
    previous.instances.map((instance) => [instance.instanceId, instance]),
  );
  const currentById = new Map(
    current.instances.map((instance) => [instance.instanceId, instance]),
  );
  const orderedIds = [
    ...current.instances.map((instance) => instance.instanceId),
    ...previous.instances
      .map((instance) => instance.instanceId)
      .filter((id) => !currentById.has(id)),
  ];
  const changes: ResearchDesignSemanticChange[] = [];

  if (previous.goal.trim() !== current.goal.trim()) {
    changes.push({
      key: "research-goal",
      label: "研究目标",
      before: previous.goal.trim(),
      after: current.goal.trim(),
    });
  }

  orderedIds.forEach((instanceId, index) => {
    const before = previousById.get(instanceId);
    const after = currentById.get(instanceId);
    const stageKey = after?.stageKey ?? before?.stageKey;
    const fallbackStageLabel = stageKey
      ? (STAGE_LABELS[stageKey] ?? "研究阶段")
      : "研究阶段";
    const label =
      labels.get(instanceId) ?? `${fallbackStageLabel} · 研究工作 ${index + 1}`;
    const beforeObjective = before?.objective.trim() ?? "";
    const afterObjective = after?.objective.trim() ?? "";
    if (beforeObjective === afterObjective) return;
    changes.push({
      key: `research-work-${index}`,
      label,
      before: beforeObjective || "未包含",
      after: afterObjective || "已移除",
    });
  });

  return changes;
}

function researchDesignRestoreUpdateFrom(
  plan: TaskPlan,
  expectedRevision: number,
  target: ResearchDesignHistoryEntry,
): TaskPlanUpdate {
  const historical = target.instances.filter(
    (instance) => instance.instanceId && instance.objective.trim(),
  );
  if (historical.length < 1)
    throw new Error("historical_research_design_shape_mismatch");
  const targetObjectives = new Map(
    historical.map((instance) => [
      instance.instanceId,
      instance.objective.trim(),
    ]),
  );
  const currentById = new Set(
    plan.stages.flatMap((stage) =>
      (stage.substages ?? [])
        .map((substage) => substage.instanceId)
        .filter((id): id is string => Boolean(id)),
    ),
  );
  const stagesByKey = new Map(
    plan.stages.map((stage) => [stage.stageKey, stage]),
  );
  let addedSeq = 1;
  const stages = TASK_STAGE_ORDER.map((stageKey) => {
    const stage = stagesByKey.get(stageKey);
    const existing = (stage?.substages ?? []).flatMap((substage) => {
      if (!substage.instanceId || !targetObjectives.has(substage.instanceId))
        return [];
      return [
        {
          instanceId: substage.instanceId,
          substage: substage.substage,
          label: requiredCanonicalEditText(substage.label, "substage_label"),
          action: requiredCanonicalEditText(
            targetObjectives.get(substage.instanceId),
            "historical_substage_objective",
          ),
          expectedArtifact: requiredCanonicalEditText(
            substage.expectedArtifact,
            "expected_artifact",
          ),
        },
      ];
    });
    const added = historical
      .filter(
        (instance) =>
          instance.stageKey === stageKey &&
          !currentById.has(instance.instanceId),
      )
      .map((instance) => {
        const objective = requiredCanonicalEditText(
          instance.objective,
          "historical_substage_objective",
        );
        const localKey = `restore-step-${addedSeq}`;
        addedSeq += 1;
        return {
          instanceId: null,
          substage: localKey,
          label: objective,
          action: objective,
          expectedArtifact: objective,
        };
      });
    return {
      stageKey,
      title: requiredCanonicalEditText(
        stage?.title?.trim() || STAGE_LABELS[stageKey],
        "stage_title",
      ),
      description: requiredCanonicalEditText(
        stage?.description?.trim() || STAGE_LABELS[stageKey],
        "stage_description",
      ),
      substages: [...existing, ...added],
    };
  });
  if (!stages.some((stage) => stage.substages.length > 0)) {
    throw new Error("historical_research_design_shape_mismatch");
  }
  return {
    expectedRevision,
    goalSummary: requiredCanonicalEditText(target.goal, "historical_goal"),
    understanding: {
      evidenceBoundaries: [...(plan.understanding?.evidenceBoundaries ?? [])],
    },
    clarifications: [...(plan.clarifications ?? [])],
    stages,
  };
}

function uniqueArtifacts(artifacts: WorkbenchArtifact[]): WorkbenchArtifact[] {
  const seen = new Set<string>();
  const out: WorkbenchArtifact[] = [];
  for (const artifact of artifacts) {
    if (seen.has(artifact.id)) continue;
    seen.add(artifact.id);
    out.push(artifact);
  }
  return out;
}

function useWorkbenchShellState() {
  return {
    leftPanel: useState(false),
    header: useState(false),
    rightPanel: useState(false),
    mobilePane: useState<MobileWorkbenchPane>("artifact"),
  };
}

export default function WorkbenchPage() {
  const { taskId } = useParams<{ taskId: string }>();
  const shellState = useWorkbenchShellState();
  const effectiveTaskId = taskId === "current" ? readCurrentTask() : taskId;
  return (
    <WorkbenchTaskSession
      key={effectiveTaskId ?? "no-task"}
      effectiveTaskId={effectiveTaskId}
      shellState={shellState}
    />
  );
}

function WorkbenchTaskSession({
  effectiveTaskId,
  shellState,
}: {
  effectiveTaskId: string | undefined;
  shellState: ReturnType<typeof useWorkbenchShellState>;
}) {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const [searchParams, setSearchParams] = useSearchParams();
  const legacyArtifactFallbackRef = useRef<{
    routeTaskId: string | undefined;
    artifactId: string | null;
    consumed: boolean;
  } | null>(null);
  if (legacyArtifactFallbackRef.current?.routeTaskId !== taskId) {
    legacyArtifactFallbackRef.current = {
      routeTaskId: taskId,
      artifactId: taskId === "current" ? (readCurrentArtifact() ?? null) : null,
      consumed: false,
    };
  }
  const legacyArtifactFallback = legacyArtifactFallbackRef.current!;
  const routeArtifactParam = searchParams.get("artifact");
  const legacyArtifactParam = legacyArtifactFallback.consumed
    ? null
    : legacyArtifactFallback.artifactId;
  const planningSessionId = searchParams.get("planningSessionId");
  const artifactParam = routeArtifactParam ?? legacyArtifactParam;
  const { logout } = useAuth();
  const load = useWorkbenchTaskModel(effectiveTaskId);
  const reloadDisplayMetadata = load.silentReload;
  const loadSource = load.status === "ready" ? load.source : null;
  const visibleStartContext = useResearchStartDisplayContext(
    effectiveTaskId,
    planningSessionId,
    reloadDisplayMetadata,
  );
  const durableProjection = useDurableRunProjection(
    effectiveTaskId,
    load.status === "ready" && load.source === "api",
  );
  const [artifactSelection, setArtifactSelection] = useState<{
    viewingPhase: PagePhaseId | null;
    selectedSubStage: string | null;
    focusedArtifactId: string | null;
    artifactScope: ArtifactScope;
    artifactViewOwnership: ArtifactViewOwnership;
    artifactViewTaskId: string | null;
  }>({
    viewingPhase: null,
    selectedSubStage: null,
    focusedArtifactId: null,
    artifactScope: "stage",
    artifactViewOwnership: "following_current",
    artifactViewTaskId: null,
  });
  const {
    viewingPhase,
    selectedSubStage: requestedSubStage,
    focusedArtifactId: requestedArtifactId,
    artifactScope,
    artifactViewOwnership,
    artifactViewTaskId,
  } = artifactSelection;
  const [supplementNote, setSupplementNote] = useState("");
  const supplementDraftVersionRef = useRef(0);
  const [collaborationSubmitting, setCollaborationSubmitting] = useState(false);
  const [leftPanelCollapsed, setLeftPanelCollapsed] = shellState.leftPanel;
  const [headerCollapsed, setHeaderCollapsed] = shellState.header;
  const [rightPanelCollapsed, setRightPanelCollapsed] = shellState.rightPanel;
  const { leftPanelWidth, rightPanelWidth, dragStateRef } =
    useWorkbenchPanelWidths(effectiveTaskId);
  const [isMobileWorkbench, setIsMobileWorkbench] = useState(false);
  const [mobilePane, setMobilePane] = shellState.mobilePane;
  const [runAction, setRunAction] = useState<
    "start" | "stop" | "retryTask" | "retryStage" | "advance" | null
  >(null);
  const [modeSaving, setModeSaving] = useState(false);
  const [versionDrawerOpen, setVersionDrawerOpen] = useState(false);
  const [planEditorOpen, setPlanEditorOpen] = useState(false);
  const [planSaving, setPlanSaving] = useState(false);
  const [planConflict, setPlanConflict] = useState(false);
  const [researchDesignEditPanel, setResearchDesignEditPanel] = useState<
    "conversation" | "history"
  >("conversation");
  const [researchDesignAgentSubmitting, setResearchDesignAgentSubmitting] =
    useState(false);
  const [researchDesignEditConversation, setResearchDesignEditConversation] =
    useState<ResearchDesignEditConversationItem[]>([]);
  const [planningStatus, setPlanningStatus] =
    useState<ResearchStartSessionStatus | null>(null);
  const [planningStatusReadUnavailable, setPlanningStatusReadUnavailable] =
    useState(false);
  const [planningStatusSessionId, setPlanningStatusSessionId] = useState<
    string | null
  >(null);
  const [planningSessionAccessError, setPlanningSessionAccessError] =
    useState<PlanningSessionAccessError>(null);
  const [planningRecheckSignal, setPlanningRecheckSignal] = useState(0);
  const [planningRetrying, setPlanningRetrying] = useState(false);
  const handlePlanningStatusChange = useCallback(
    (
      snapshot: ResearchStartSessionStatus | null | undefined,
      readUnavailable: boolean,
      accessError: PlanningSessionAccessError,
    ) => {
      setPlanningStatusSessionId(planningSessionId);
      // `undefined` retains the last good snapshot so elapsed / progress do not flash.
      if (snapshot !== undefined) {
        setPlanningStatus(snapshot);
      }
      setPlanningStatusReadUnavailable(readUnavailable);
      setPlanningSessionAccessError(accessError);
    },
    [planningSessionId],
  );
  const handlePlanningTaskRecheck = useCallback(() => {
    load.silentReload();
  }, [load.silentReload]);
  const handleRecheckPlanningSession = useCallback(() => {
    setPlanningRecheckSignal((signal) => signal + 1);
  }, []);
  const handleRetryPlanning = useCallback(async () => {
    const pending = readResearchStartPendingForSession(
      planningSessionId ?? undefined,
    );
    if (!pending || !effectiveTaskId) {
      message.warning("找不到可重试的研究起点信息，请返回科研起点重新发起。");
      return;
    }
    setPlanningRetrying(true);
    try {
      const accepted = await retryResearchStartFromPending(pending);
      rememberResearchStartAccepted({
        clientRequestId: pending.clientRequestId,
        sessionId: accepted.sessionId,
        reservedTaskId: accepted.reservedTaskId,
      });
      setPlanningStatus(null);
      setPlanningStatusReadUnavailable(false);
      setPlanningSessionAccessError(null);
      const nextSearchParams = new URLSearchParams(searchParams);
      nextSearchParams.set("planningSessionId", accepted.sessionId);
      if (accepted.reservedTaskId !== effectiveTaskId) {
        navigate(
          `/workbench/${encodeURIComponent(accepted.reservedTaskId)}?${nextSearchParams.toString()}`,
          { replace: true },
        );
      } else {
        setSearchParams(nextSearchParams, { replace: true });
      }
      load.silentReload();
    } catch (error) {
      message.error(userFacingResearchStartError(error));
    } finally {
      setPlanningRetrying(false);
    }
  }, [
    effectiveTaskId,
    load.silentReload,
    navigate,
    planningSessionId,
    searchParams,
    setSearchParams,
  ]);
  const planningStateMatchesRoute =
    planningStatusSessionId === planningSessionId;
  const visiblePlanningStatus = planningStateMatchesRoute
    ? planningStatus
    : null;
  const visiblePlanningStatusReadUnavailable =
    planningStateMatchesRoute && planningStatusReadUnavailable;
  // Once the durable task left planning, drop the sticky query so refresh
  // cannot replay the planning bootstrap shell.
  useEffect(() => {
    if (!planningSessionId || load.status !== "ready") return;
    const status = load.model.taskStatus;
    const creation = load.model.creationStatus;
    const planNotYetFormed =
      !load.model.creationPlan && !load.model.researchDesign;
    const leftPlanning =
      creation === "confirmed" ||
      status === "running" ||
      status === "not_started" ||
      status === "completed" ||
      status === "cancelled" ||
      (status === "failed" && !planNotYetFormed);
    if (!leftPlanning) return;
    const nextSearchParams = new URLSearchParams(searchParams);
    if (!nextSearchParams.has("planningSessionId")) return;
    nextSearchParams.delete("planningSessionId");
    setSearchParams(nextSearchParams, { replace: true });
  }, [planningSessionId, load, searchParams, setSearchParams]);
  // A failed plan that never formed should keep the original research-start session.
  useEffect(() => {
    if (planningSessionId || load.status !== "ready") return;
    const model = load.model;
    if (model.creationPlan || model.researchDesign) return;
    if (model.taskStatus !== "failed") return;
    const pending = readResearchStartPending();
    const reserved = pending?.reservedTaskId ?? pending?.taskId;
    if (!pending?.sessionId || !effectiveTaskId || reserved !== effectiveTaskId)
      return;
    const nextSearchParams = new URLSearchParams(searchParams);
    nextSearchParams.set("planningSessionId", pending.sessionId);
    setSearchParams(nextSearchParams, { replace: true });
  }, [effectiveTaskId, planningSessionId, load, searchParams, setSearchParams]);
  const visiblePlanningSessionAccessError = planningStateMatchesRoute
    ? planningSessionAccessError
    : null;
  const planningOccupiesShell = planningOccupiesWorkbenchShell({
    planningSessionId,
    taskId: effectiveTaskId,
    sessionAccessError: visiblePlanningSessionAccessError,
    loadStatus: load.status,
    loadErrorKind: load.status === "error" ? load.errorKind : undefined,
    taskStatus: load.status === "ready" ? load.model.taskStatus : undefined,
    creationStatus:
      load.status === "ready" ? load.model.creationStatus : undefined,
  });
  const viewInitKeyRef = useRef<string | null>(null);
  const interactionScrollTopByContextRef = useRef<Map<string, number>>(
    new Map(),
  );
  const startSubmitInFlightTaskRef = useRef<string | null>(null);
  const startStatusGenerationRef = useRef(0);
  const currentStartStatusGenerationRef = useRef(0);
  const startStatusTaskIdRef = useRef<string | null>(effectiveTaskId ?? null);
  const startStatusTaskRevisionRef = useRef<number | null>(
    load.status === "ready" && load.model.taskId === effectiveTaskId
      ? load.model.revision
      : null,
  );
  const syncArtifactRoute = useCallback(
    (artifactId: string | null, replace = false) => {
      if (legacyArtifactFallbackRef.current) {
        legacyArtifactFallbackRef.current.consumed = true;
      }
      rememberCurrentArtifact(artifactId);
      const nextSearchParams = new URLSearchParams(searchParams);
      if (!replace) nextSearchParams.delete("view");
      if (artifactId) nextSearchParams.set("artifact", artifactId);
      else nextSearchParams.delete("artifact");
      if (nextSearchParams.toString() === searchParams.toString()) return;
      setSearchParams(nextSearchParams, { replace });
    },
    [searchParams, setSearchParams],
  );
  const startStatusRouteCheckRef = useRef<string | null>(null);
  const recoveryGenerationRef = useRef(0);
  const recoveryFenceRef = useRef<string>("");
  const effectiveStartTaskRevision =
    load.status === "ready" && load.model.taskId === effectiveTaskId
      ? load.model.revision
      : null;
  useLayoutEffect(() => {
    if (startStatusTaskRevisionRef.current === effectiveStartTaskRevision)
      return;
    startStatusTaskRevisionRef.current = effectiveStartTaskRevision;
    startStatusGenerationRef.current += 1;
    currentStartStatusGenerationRef.current += 1;
    startStatusRouteCheckRef.current = null;
  }, [effectiveStartTaskRevision]);

  const readCurrentStartStatus = useCallback(async () => {
    if (!effectiveTaskId || effectiveStartTaskRevision === null)
      throw new Error("task_not_ready");
    const status = await fetchCurrentTaskStartStatus(effectiveTaskId);
    if (
      status.taskId !== effectiveTaskId ||
      status.taskRevision !== effectiveStartTaskRevision
    ) {
      throw new Error("start_status_identity_mismatch");
    }
    return status;
  }, [effectiveTaskId, effectiveStartTaskRevision]);
  const { data: currentTaskStartStatus, refresh: refreshStartAuthority } =
    useWorkbenchAuthority({
      read: readCurrentStartStatus,
      enabled: Boolean(
        effectiveTaskId &&
        effectiveStartTaskRevision !== null &&
        loadSource === "api",
      ),
    });
  const refreshCurrentTaskStartStatus = useCallback(
    async (
      requestedTaskId: string,
      requestedRevision: number,
    ): Promise<CanonicalTaskStartStatus | null> => {
      const generation = ++currentStartStatusGenerationRef.current;
      const status = await refreshStartAuthority();
      if (
        generation !== currentStartStatusGenerationRef.current ||
        startStatusTaskIdRef.current !== requestedTaskId ||
        startStatusTaskRevisionRef.current !== requestedRevision
      ) {
        return null;
      }
      return status;
    },
    [refreshStartAuthority],
  );

  useEffect(() => {
    if (
      !effectiveTaskId ||
      effectiveStartTaskRevision === null ||
      !currentTaskStartStatus ||
      currentTaskStartStatus.taskId !== effectiveTaskId ||
      currentTaskStartStatus.taskRevision !== effectiveStartTaskRevision ||
      !["accepted", "running"].includes(currentTaskStartStatus.state) ||
      readDurableStartIntent(effectiveTaskId)
    ) {
      return;
    }
    const timeout = window.setTimeout(() => {
      void refreshCurrentTaskStartStatus(
        effectiveTaskId,
        effectiveStartTaskRevision,
      );
    }, 1000);
    return () => window.clearTimeout(timeout);
  }, [
    currentTaskStartStatus,
    effectiveTaskId,
    effectiveStartTaskRevision,
    refreshCurrentTaskStartStatus,
  ]);

  useLayoutEffect(() => {
    startStatusTaskIdRef.current = effectiveTaskId ?? null;
    return () => {
      startStatusTaskIdRef.current = null;
      startStatusGenerationRef.current += 1;
      currentStartStatusGenerationRef.current += 1;
    };
  }, [effectiveTaskId]);

  const isCurrentStartStatusOperation = useCallback(
    (fence: TaskStartStatusFence) =>
      isTaskStartStatusFenceCurrent(fence, {
        taskId: startStatusTaskIdRef.current,
        selectionRequestId: readDurableStartIntent(fence.taskId),
        taskRevision: startStatusTaskRevisionRef.current,
        generation: startStatusGenerationRef.current,
      }),
    [],
  );

  const recoverCanonicalStartStatus = useCallback(
    async (
      requestedTaskId: string,
      selectionRequestId: string,
    ): Promise<CanonicalTaskStartState | null> => {
      if (
        startStatusTaskIdRef.current !== requestedTaskId ||
        readDurableStartIntent(requestedTaskId) !== selectionRequestId
      ) {
        return null;
      }
      const fence: TaskStartStatusFence = {
        taskId: requestedTaskId,
        selectionRequestId,
        taskRevision: startStatusTaskRevisionRef.current,
        generation: ++startStatusGenerationRef.current,
      };
      startStatusRouteCheckRef.current = `${requestedTaskId}\u0000${selectionRequestId}`;
      let receipt: StartRequestReceipt | null = null;
      let state: CanonicalTaskStartState = "unknown";
      try {
        for (;;) {
          receipt = await fetchTaskStartStatus(
            requestedTaskId,
            selectionRequestId,
          );
          state = receipt.state;
          if (!["accepted", "running"].includes(state)) break;
          if (!isCurrentStartStatusOperation(fence)) return null;
          await new Promise((resolve) => setTimeout(resolve, 1000));
        }
      } catch {
        state = "unknown";
        receipt = null;
      }
      if (!isCurrentStartStatusOperation(fence)) return null;

      // Request recovery is observational only.  It never writes the current Task
      // authority.  Once this receipt settles, explicitly re-read Task-level truth.
      settleDurableStartIntent(requestedTaskId, selectionRequestId, state);
      const currentRevision = startStatusTaskRevisionRef.current;
      const currentStatus =
        currentRevision !== null
          ? await refreshCurrentTaskStartStatus(
              requestedTaskId,
              currentRevision,
            )
          : null;

      load.reload();
      durableProjection.refresh();
      if (currentStatus) {
        if (currentStatus.state === "committed") {
          message.success(
            taskStartStatusMessage(
              currentStatus.state,
              currentStatus.blockingReasonCode,
            ),
          );
        } else if (!currentStatus.startAllowed) {
          message.warning(
            currentStatus.reasonSummary ??
              taskStartStatusMessage(
                currentStatus.state,
                currentStatus.blockingReasonCode,
              ),
          );
        } else {
          message.info(
            taskStartStatusMessage(
              currentStatus.state,
              currentStatus.blockingReasonCode,
            ),
          );
        }
      } else if (receipt?.state === "committed") {
        message.success(
          taskStartStatusMessage(receipt.state, receipt.blockingReasonCode),
        );
      } else if (receipt) {
        message.warning(
          taskStartStatusMessage(receipt.state, receipt.blockingReasonCode),
        );
      } else {
        message.warning(taskStartStatusMessage("unknown"));
      }
      return state;
    },
    [
      durableProjection.refresh,
      isCurrentStartStatusOperation,
      load.reload,
      message,
      refreshCurrentTaskStartStatus,
    ],
  );

  useEffect(() => {
    if (!effectiveTaskId || load.status !== "ready" || load.source !== "api")
      return;
    const selectionRequestId = readDurableStartIntent(effectiveTaskId);
    if (!selectionRequestId) return;
    const routeCheckKey = `${effectiveTaskId}\u0000${selectionRequestId}`;
    if (startStatusRouteCheckRef.current === routeCheckKey) return;
    void recoverCanonicalStartStatus(effectiveTaskId, selectionRequestId);
  }, [
    effectiveTaskId,
    load.status,
    load.status === "ready" ? load.source : undefined,
    recoverCanonicalStartStatus,
  ]);

  useEffect(() => {
    durableProjection.refresh();
  }, [
    durableProjection.refresh,
    load.status === "ready" ? load.model.updatedAt : undefined,
  ]);

  const readExecutionPolicy = useCallback(async () => {
    if (!effectiveTaskId) throw new Error("task_not_ready");
    const policy = await fetchTaskExecutionPolicy(effectiveTaskId);
    if (policy.taskId !== effectiveTaskId)
      throw new Error("execution_policy_identity_mismatch");
    return policy;
  }, [effectiveTaskId]);
  const {
    data: executionPolicy,
    unavailable: executionPolicyUnavailable,
    refresh: refreshPolicyAuthority,
  } = useWorkbenchAuthority({
    read: readExecutionPolicy,
    enabled: loadSource === "api",
    refreshSignal: load.status === "ready" ? load.model.updatedAt : undefined,
    retainOnError: true,
  });
  const refreshExecutionPolicy = useCallback(
    async () => Boolean(await refreshPolicyAuthority()),
    [refreshPolicyAuthority],
  );

  useEffect(() => {
    const query = window.matchMedia("(max-width: 760px)");
    const sync = () => setIsMobileWorkbench(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  const startPanelDrag =
    (side: "left" | "right") => (event: React.MouseEvent) => {
      event.preventDefault();
      dragStateRef.current = {
        side,
        startX: event.clientX,
        startWidth: side === "left" ? leftPanelWidth : rightPanelWidth,
      };
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    };

  const {
    hydratedModel,
    hydrate,
    retryArtifact,
    loadingArtifactIds,
    getArtifactFailureReason,
    replaceModel,
  } = useHydratedModel(load);
  const formingModel = useMemo(() => {
    if (!planningOccupiesShell || !effectiveTaskId || load.status === "ready") {
      return null;
    }
    return formingWorkbenchTaskModel({
      taskId: effectiveTaskId,
      title: visibleStartContext?.value?.displayTitle || "新研究",
    });
  }, [
    effectiveTaskId,
    load.status,
    planningOccupiesShell,
    visibleStartContext?.value?.displayTitle,
  ]);
  const model =
    load.status === "ready" ? (hydratedModel ?? load.model) : formingModel;
  const viewingConfirmedPlan = searchParams.get("view") === "research-plan" &&
    Boolean(model?.confirmedResearchDesign);
  const activeTaskStartStatus =
    model &&
    currentTaskStartStatus?.taskId === model.taskId &&
    currentTaskStartStatus.taskRevision === model.revision
      ? currentTaskStartStatus
      : null;
  const canonicalResearchDesignEditable = Boolean(
    model?.creationPlan?.planningSource === "research_design_v1_2" &&
    (model.creationStatus === "awaiting_confirmation" ||
      model.creationStatus === "revision_requested") &&
    (model.taskStatus === "draft" ||
      model.taskStatus === "awaiting_confirmation"),
  );
  const screenRevision = model?.progressRevision ?? model?.revision ?? null;
  const apiConnectedForCollab =
    load.status === "ready" && load.source === "api";
  const collaborationState = useCollaborationProjection(
    model?.taskId,
    apiConnectedForCollab,
    screenRevision,
  );

  const editorSession = useWorkbenchSessionFields(
    canonicalResearchDesignEditable ? "editable" : "closed",
    EMPTY_EDITOR_SESSION,
  );
  const [researchDesignEditMode, setResearchDesignEditMode] =
    editorSession.field("mode");
  const [researchDesignEditDraft, setResearchDesignEditDraft] =
    editorSession.field("draft");
  const researchDesignCatalog = useResearchDesignCatalog(
    model?.skillPack ?? model?.creationPlan?.skillPack ?? "sdr",
    researchDesignEditMode,
  );
  const [researchDesignEditError, setResearchDesignEditError] =
    editorSession.field("error");
  const [
    selectedResearchDesignHistoryVersion,
    setSelectedResearchDesignHistoryVersion,
  ] = editorSession.field("historyVersion");
  const [researchDesignUndoingVersion, setResearchDesignUndoingVersion] =
    editorSession.field("undoingVersion");
  const [researchDesignAgentInstruction, setResearchDesignAgentInstruction] =
    editorSession.field("instruction");
  /** Primary Workbench screen truth comes from the Task durable progress projection. */
  const screen = useMemo(() => {
    if (!model) return null;
    return buildWorkbenchScreenView(model);
  }, [model]);
  const refreshWorkbenchAuthority = useCallback(async () => {
    await load.reload();
    collaborationState.refresh();
    durableProjection.refresh();
  }, [collaborationState.refresh, durableProjection.refresh, load.reload]);
  const reloadTaskFromResearchActivity = useCallback(() => {
    void refreshExecutionPolicy();
    void load.reload();
    collaborationState.refresh();
  }, [collaborationState.refresh, load.reload, refreshExecutionPolicy]);
  const userActions = useWorkbenchUserActions({
    taskId: model?.taskId ?? "",
    enabled: apiConnectedForCollab && Boolean(model),
    revision: model?.revision ?? null,
    onAuthoritativeRefresh: refreshWorkbenchAuthority,
  });
  const isLegacyRecoveryLifecycle =
    userActions.lifecycleKind === "legacy_durable_run";

  const recoveryFence = useMemo<RecoveryFence>(
    () => ({
      taskId: model?.taskId ?? "",
      expectedTaskRevision: model?.revision ?? -1,
      sourceRunId: isLegacyRecoveryLifecycle
        ? (durableProjection.run?.runId ?? "")
        : "",
    }),
    [
      durableProjection.run?.runId,
      isLegacyRecoveryLifecycle,
      model?.revision,
      model?.taskId,
    ],
  );
  const currentRecoveryFenceKey = recoveryFenceKey(recoveryFence);
  const auxiliaryCollaborationFailure = collaborationState.projection?.failure;
  const recoveryFailure = useMemo<RecoveryFailureContext>(() => {
    const failure = auxiliaryCollaborationFailure;
    return {
      failedStep: failure?.failedStep
        ? userFacingDynamicText(failure.failedStep, "当前研究步骤")
        : null,
      reason: failure?.externalAction
        ? userFacingDynamicText(
            failure.externalAction,
            "本次执行结果暂时无法确认。",
          )
        : null,
      retainedWork: failure?.resultTrustAndRetention
        ? userFacingDynamicText(
            failure.resultTrustAndRetention,
            "已完成的研究内容将继续保留。",
          )
        : null,
    };
  }, [auxiliaryCollaborationFailure]);
  const recoveryMessageIds = useMemo(
    () =>
      recoveryFence.taskId &&
      recoveryFence.sourceRunId &&
      recoveryFence.expectedTaskRevision >= 0
        ? readRecoveryControllerMessages(
            recoveryFence.taskId,
            recoveryFence.expectedTaskRevision,
            recoveryFence.sourceRunId,
          )
        : [],
    [recoveryFence],
  );
  const [recoverySession, setRecoverySession] = useWorkbenchSessionState(
    currentRecoveryFenceKey,
    EMPTY_RECOVERY_SESSION,
  );
  const hasPendingRecoveryCache = Boolean(
    recoverySession.cache &&
    recoverySession.cache.taskId === recoveryFence.taskId &&
    recoverySession.cache.expectedTaskRevision ===
      recoveryFence.expectedTaskRevision &&
    recoverySession.cache.sourceRunId === recoveryFence.sourceRunId,
  );
  const recoveryProjection = useTaskRecoveryProjection({
    fence: recoveryFence,
    response: recoverySession.response,
    responseUnknown: recoverySession.responseUnknown,
    phase: recoverySession.phase,
    eligibleForCreate: false,
    hasCurrentMessageId: recoveryMessageIds.length > 0,
    hasPendingCache: hasPendingRecoveryCache,
    failure: recoveryFailure,
    error: recoverySession.error,
  });

  useLayoutEffect(() => {
    recoveryGenerationRef.current += 1;
    recoveryFenceRef.current = currentRecoveryFenceKey;
    return () => {
      recoveryGenerationRef.current += 1;
      recoveryFenceRef.current = "";
    };
  }, [currentRecoveryFenceKey]);

  const beginResearchDesignEdit = () => {
    if (
      !model?.creationPlan ||
      !canonicalResearchDesignEditable ||
      load.status !== "ready" ||
      load.source !== "api"
    )
      return;
    setResearchDesignEditDraft(
      researchDesignEditDraftFrom(model.creationPlan, model.revision),
    );
    setResearchDesignEditError(null);
    setPlanConflict(false);
    setSelectedResearchDesignHistoryVersion(
      model.researchDesign?.version ?? null,
    );
    setResearchDesignEditMode(true);
  };

  const completeResearchDesignEdit = async () => {
    const draft = researchDesignEditDraft;
    if (
      !draft ||
      !model ||
      !canonicalResearchDesignEditable ||
      load.status !== "ready" ||
      load.source !== "api" ||
      planSaving
    )
      return;
    if (!researchDesignEditDraftDirty(draft)) {
      setResearchDesignEditMode(false);
      setResearchDesignEditDraft(null);
      setResearchDesignEditError(null);
      setPlanConflict(false);
      return;
    }
    if (researchDesignEditHasBlankWork(draft)) {
      setResearchDesignEditError("研究目标和研究工作不能为空。");
      return;
    }
    if (researchDesignEditRemainingCount(draft) < 1) {
      setResearchDesignEditError("至少保留一个研究工作。");
      return;
    }

    setPlanSaving(true);
    setResearchDesignEditError(null);
    try {
      const update = researchDesignEditUpdateFrom(draft);
      const updated = await updateTaskPlan(model.taskId, update);
      let refreshedModel = workbenchModelFromApiTask(updated);
      replaceModel(refreshedModel);
      try {
        const refreshed = await fetchTask(model.taskId);
        if (refreshed && (refreshed.revision ?? 0) >= (updated.revision ?? 0)) {
          refreshedModel = workbenchModelFromApiTask(refreshed);
          replaceModel(refreshedModel);
        }
      } catch {
        message.warning("方案已保存；当前页面已使用服务端返回的最新版本。");
      }
      setResearchDesignEditMode(false);
      setResearchDesignEditDraft(null);
      setPlanConflict(false);
      message.success("研究方案已保存。");
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      if (detail.includes("task_revision_conflict")) {
        setPlanConflict(true);
        setResearchDesignEditError(
          "研究方案刚刚已被更新。你的当前修改仍保留，正在载入最新版本。",
        );
        try {
          const latest = await fetchTask(model.taskId);
          if (latest) {
            const latestModel = workbenchModelFromApiTask(latest);
            replaceModel(latestModel);
            if (
              latestModel.creationPlan?.planningSource ===
              "research_design_v1_2"
            ) {
              setResearchDesignEditDraft((current) =>
                current
                  ? researchDesignEditDraftRebaseOnLatest(
                      current,
                      latestModel.creationPlan!,
                      latestModel.revision,
                    )
                  : current,
              );
              setResearchDesignEditError(
                "已载入最新研究方案，并保留你的当前修改。请检查后再次点击“完成编辑”，将基于最新版本继续。",
              );
            }
          }
        } catch {
          // Keep the local draft and conflict visible when the latest task cannot be loaded yet.
        }
      } else {
        setResearchDesignEditError(
          userFacingErrorText(detail, "本次研究方案保存未完成，请重试。"),
        );
      }
    } finally {
      setPlanSaving(false);
    }
  };

  const submitResearchDesignAgentInstruction = async () => {
    const instruction = researchDesignAgentInstruction.trim();
    const draft = researchDesignEditDraft;
    if (
      !instruction ||
      !draft ||
      !model ||
      !researchDesignEditMode ||
      !canonicalResearchDesignEditable
    )
      return;
    if (
      load.status !== "ready" ||
      load.source !== "api" ||
      researchDesignAgentSubmitting
    )
      return;
    if (researchDesignEditDraftDirty(draft)) {
      setResearchDesignEditError(
        "你还有未保存的手动修改。请先完成编辑，再让 Agent 基于最新方案调整。",
      );
      return;
    }

    const userMessageId = crypto.randomUUID();
    setResearchDesignEditConversation((current) => [
      ...current,
      { id: userMessageId, role: "user", text: instruction },
    ]);
    setResearchDesignAgentSubmitting(true);
    setResearchDesignEditError(null);
    try {
      const updated = await agentReviseResearchDesign(model.taskId, {
        instruction,
        expectedRevision: model.revision,
      });
      let refreshedModel = workbenchModelFromApiTask(updated);
      try {
        const refreshed = await fetchTask(model.taskId);
        if (refreshed && (refreshed.revision ?? 0) >= (updated.revision ?? 0)) {
          refreshedModel = workbenchModelFromApiTask(refreshed);
        }
      } catch {
        // The endpoint response is already canonical task truth; keep it if the reload is unavailable.
      }
      replaceModel(refreshedModel);
      if (
        refreshedModel.creationPlan?.planningSource === "research_design_v1_2"
      ) {
        setResearchDesignEditDraft(
          researchDesignEditDraftFrom(
            refreshedModel.creationPlan,
            refreshedModel.revision,
          ),
        );
      }
      setSelectedResearchDesignHistoryVersion(
        refreshedModel.researchDesign?.version ?? null,
      );
      setResearchDesignEditConversation((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          text: "已按你的要求调整研究方案。",
          state: "success",
        },
      ]);
      setResearchDesignAgentInstruction("");
      setPlanConflict(false);
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      const conflict = detail.includes("task_revision_conflict");
      setResearchDesignEditConversation((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          text: conflict
            ? "方案刚刚已更新，这次要求没有生效。请刷新最新方案后重试。"
            : "这次要求没有生效，请重试。",
          state: "failed",
        },
      ]);
      setResearchDesignEditError(
        conflict
          ? "方案版本已变化；Agent 没有覆盖最新方案。你的当前页面内容仍保留。"
          : userFacingErrorText(detail, "这次 Agent 修改没有生效，请重试。"),
      );
      if (conflict) {
        try {
          const latest = await fetchTask(model.taskId);
          if (latest) {
            const latestModel = workbenchModelFromApiTask(latest);
            replaceModel(latestModel);
            if (
              latestModel.creationPlan?.planningSource ===
              "research_design_v1_2"
            ) {
              setResearchDesignEditDraft(
                researchDesignEditDraftFrom(
                  latestModel.creationPlan,
                  latestModel.revision,
                ),
              );
            }
          }
        } catch {
          // Conflict remains visible; do not fabricate a newer canonical revision.
        }
      }
    } finally {
      setResearchDesignAgentSubmitting(false);
    }
  };

  const restoreResearchDesignVersion = async (
    target: ResearchDesignHistoryEntry,
  ) => {
    const draft = researchDesignEditDraft;
    if (
      !model?.creationPlan ||
      !canonicalResearchDesignEditable ||
      load.status !== "ready" ||
      load.source !== "api" ||
      researchDesignUndoingVersion !== null ||
      target.version === model.researchDesign?.version
    )
      return;
    if (draft && researchDesignEditDraftDirty(draft)) {
      setResearchDesignEditError(
        "你还有未保存的手动修改。请先完成编辑，再撤销到历史版本。",
      );
      return;
    }

    setResearchDesignUndoingVersion(target.version);
    setResearchDesignEditError(null);
    setPlanConflict(false);
    try {
      const update = researchDesignRestoreUpdateFrom(
        model.creationPlan,
        model.revision,
        target,
      );
      const updated = await updateTaskPlan(model.taskId, update);
      let refreshedModel = workbenchModelFromApiTask(updated);
      try {
        const refreshed = await fetchTask(model.taskId);
        if (refreshed && (refreshed.revision ?? 0) >= (updated.revision ?? 0)) {
          refreshedModel = workbenchModelFromApiTask(refreshed);
        }
      } catch {
        message.warning("撤销已保存；当前页面已使用服务端返回的最新版本。");
      }
      replaceModel(refreshedModel);
      if (
        refreshedModel.creationPlan?.planningSource === "research_design_v1_2"
      ) {
        setResearchDesignEditDraft(
          researchDesignEditDraftFrom(
            refreshedModel.creationPlan,
            refreshedModel.revision,
          ),
        );
      }
      setSelectedResearchDesignHistoryVersion(
        refreshedModel.researchDesign?.version ?? null,
      );
      setPlanConflict(false);
      message.success(
        `已撤销到版本 ${target.version} 的内容，并保存为新的研究方案版本。`,
      );
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      if (detail.includes("task_revision_conflict")) {
        setPlanConflict(true);
        setResearchDesignEditError(
          "撤销没有生效：研究方案在你查看历史期间已更新。已重新载入最新版本，请确认后再试。",
        );
        try {
          const latest = await fetchTask(model.taskId);
          if (latest) {
            const latestModel = workbenchModelFromApiTask(latest);
            replaceModel(latestModel);
            if (
              latestModel.creationPlan?.planningSource ===
              "research_design_v1_2"
            ) {
              setResearchDesignEditDraft(
                researchDesignEditDraftFrom(
                  latestModel.creationPlan,
                  latestModel.revision,
                ),
              );
            }
          }
        } catch {
          // Keep the CAS conflict visible; never overwrite newer canonical truth.
        }
      } else if (detail.includes("historical_research_design_shape_mismatch")) {
        setResearchDesignEditError(
          "这个历史版本与当前研究工作结构不一致，未执行撤销。",
        );
      } else {
        setResearchDesignEditError(
          userFacingErrorText(detail, "本次撤销没有完成，请重试。"),
        );
      }
    } finally {
      setResearchDesignUndoingVersion(null);
    }
  };

  const saveDraftPlan = async (
    update: import("@openair/shared").TaskPlanUpdate,
  ) => {
    if (
      !model ||
      load.status !== "ready" ||
      load.source !== "api" ||
      planSaving
    )
      return;
    setPlanSaving(true);
    try {
      const updated = await updateTaskPlan(model.taskId, update);
      replaceModel(workbenchModelFromApiTask(updated));
      try {
        const refreshed = await fetchTask(model.taskId);
        if (!refreshed) throw new Error("task_not_found_after_plan_save");
        if ((refreshed.revision ?? 0) >= (updated.revision ?? 0)) {
          replaceModel(workbenchModelFromApiTask(refreshed));
        }
        setPlanEditorOpen(false);
        message.success("草案已保存。");
      } catch {
        setPlanEditorOpen(false);
        message.warning("草案已保存，但最新页面加载失败，请刷新页面。");
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      if (detail.includes("task_revision_conflict")) {
        setPlanConflict(true);
        message.error("草案已被其他修改更新，请重新加载草案后再编辑。");
      } else {
        try {
          const refreshed = await fetchTask(model.taskId);
          if (refreshed) {
            replaceModel(workbenchModelFromApiTask(refreshed));
          }
        } catch {
          // Keep the editor and the user's unsaved draft intact even if revision recovery cannot complete yet.
        }
        message.error(
          userFacingErrorText(detail, "本次研究方案保存未完成，请重试。"),
        );
      }
    } finally {
      setPlanSaving(false);
    }
  };

  const reloadDraftPlan = async (): Promise<
    import("@openair/shared").TaskPlan
  > => {
    if (!model || load.status !== "ready" || load.source !== "api") {
      throw new Error("task_not_ready");
    }
    try {
      const refreshed = await fetchTask(model.taskId);
      if (!refreshed) throw new Error("task_not_found");
      const refreshedModel = workbenchModelFromApiTask(refreshed);
      if (!refreshedModel.creationPlan)
        throw new Error("creation_plan_not_found");
      replaceModel(refreshedModel);
      setPlanConflict(false);
      return refreshedModel.creationPlan;
    } catch (error) {
      message.error("重新加载草案失败，请刷新页面后重试。");
      throw error;
    }
  };

  const pickDefaultSub = useCallback(
    (phase: PagePhaseId) => {
      if (!model) return null;
      const row = stageRowForPagePhase(model, phase);
      return defaultSubStageKey(row?.subStages ?? []);
    },
    [model],
  );

  useEffect(() => {
    if (load.status !== "ready") return;
    const m = load.model;
    const requestedArtifact = artifactParam
      ? (userVisibleWorkbenchArtifacts(m.artifacts).find(
          (artifact) => artifact.id === artifactParam,
        ) ?? null)
      : null;
    const normalizedArtifactId = requestedArtifact?.id ?? null;
    if (requestedArtifact) {
      // A valid explicit URL is authoritative over stale session state. This
      // also migrates the legacy `/workbench/current` session selection once;
      // afterward browser history and refresh are governed by the URL alone.
      syncArtifactRoute(requestedArtifact.id, true);
    } else if (artifactParam !== null && !requestedArtifact) {
      // An Artifact outside the hydrated Task (including hidden/unknown ids)
      // must never survive as either route or local selection authority.
      syncArtifactRoute(null, true);
    } else if (artifactParam === null) {
      // A browser back/re-entry to a URL without `artifact` also clears the
      // legacy session pointer so refresh cannot resurrect a stale selection.
      syncArtifactRoute(null, true);
    }

    const initKey = `${m.taskId}:${normalizedArtifactId ?? ""}`;
    if (viewInitKeyRef.current === initKey) return;
    viewInitKeyRef.current = initKey;
    const local = readWorkbenchLocal(m.taskId);
    if (normalizedArtifactId && requestedArtifact) {
      const location = resolveArtifactLocation(m, normalizedArtifactId);
      const artPhase = pagePhaseForUnderlying(requestedArtifact.relatedStage);
      setArtifactSelection({
        artifactViewTaskId: m.taskId,
        viewingPhase: artPhase,
        selectedSubStage:
          location.kind === "located" ? location.substageInstanceId : null,
        focusedArtifactId: normalizedArtifactId,
        artifactScope: location.kind === "located" ? "substage" : "task",
        artifactViewOwnership: "pinned",
      });
      return;
    }

    const phase = isPagePhaseId(local.lastViewPhaseId)
      ? local.lastViewPhaseId
      : pagePhaseForUnderlying(m.currentUnderlyingStage);

    const row = stageRowForPagePhase(m, phase);
    const subKeys = new Set(row?.subStages.map((s) => s.id) ?? []);
    const subByKey = new Map(
      row?.subStages.map((s) => [s.subStage, s.id]) ?? [],
    );
    const restored = local.lastSubStageKey
      ? subKeys.has(local.lastSubStageKey)
        ? local.lastSubStageKey
        : subByKey.get(local.lastSubStageKey)
      : null;
    const initSubStage =
      restored ?? defaultSubStageKey(row?.subStages ?? []) ?? null;
    setArtifactSelection({
      artifactViewTaskId: m.taskId,
      viewingPhase: phase,
      selectedSubStage: initSubStage,
      focusedArtifactId: null,
      artifactScope: initSubStage ? "substage" : "stage",
      artifactViewOwnership: restored ? "pinned" : "following_current",
    });
  }, [
    load.status,
    load.status === "ready" ? load.model : undefined,
    effectiveTaskId,
    artifactParam,
    syncArtifactRoute,
  ]);

  useEffect(() => {
    const prevHtml = document.documentElement.style.overflow;
    const prevBody = document.body.style.overflow;
    document.documentElement.style.overflow = "hidden";
    document.body.style.overflow = "hidden";
    return () => {
      document.documentElement.style.overflow = prevHtml;
      document.body.style.overflow = prevBody;
    };
  }, []);

  const progressSteps = useMemo((): WorkbenchProgressStep[] => {
    const stoppedByUser =
      model?.interruption?.kind === "user_stop" ||
      model?.interruption?.kind === "execution_cancelled" ||
      Boolean(userActions.snapshot?.executionStopped);
    const coerceStopped = (
      steps: WorkbenchProgressStep[],
    ): WorkbenchProgressStep[] => {
      if (!stoppedByUser) return steps;
      return steps.map((step) => {
        if (step.status !== "running" && step.projectionStatus !== "running")
          return step;
        return {
          ...step,
          status: "failed",
          projectionStatus: "stopped",
        };
      });
    };
    if (screen?.isLiveResearch || screen?.progressTrust.kind === "invalid") {
      return coerceStopped(
        progressStepsFromScreen(screen, (stageKey) => {
          const stage = (
            isUnderlyingStage(stageKey) ? stageKey : "problem_modeling"
          ) as keyof typeof STAGE_LABELS;
          return {
            id: pagePhaseForUnderlying(stage),
            label: STAGE_LABELS[stage] ?? stageKey,
          };
        }) as WorkbenchProgressStep[],
      );
    }
    // Legacy / no projection: keep non-orchestration display; do not invent live four-stage status.
    return coerceStopped(model ? buildProgressSteps(model) : []);
  }, [screen, model, userActions.snapshot?.executionStopped]);

  const phaseAgg = useMemo((): Record<PagePhaseId, PhaseNavStatus> | null => {
    if (screen?.isLiveResearch || screen?.progressTrust.kind === "invalid") {
      return phaseAggFromScreen(screen, (stageKey: string) => {
        const stage = (
          isUnderlyingStage(stageKey) ? stageKey : "problem_modeling"
        ) as Parameters<typeof pagePhaseForUnderlying>[0];
        return pagePhaseForUnderlying(stage);
      }) as Record<PagePhaseId, PhaseNavStatus>;
    }
    return model ? pagePhaseAggregateStatus(model) : null;
  }, [screen, model]);

  const stagesByPhase = useMemo(() => {
    const out: Record<PagePhaseId, ReturnType<typeof stageRowForPagePhase>> = {
      problem: undefined,
      method: undefined,
      experiment: undefined,
      report: undefined,
    };
    if (!model) return out;
    for (const p of PAGE_PHASE_ORDER) {
      out[p] = stageRowForPagePhase(model, p);
    }
    // Overlay stage status from the unique screen so left nav cannot diverge by revision.
    if (screen?.isLiveResearch || screen?.progressTrust.kind === "invalid") {
      for (const left of screen.leftStages) {
        const stage = (
          isUnderlyingStage(left.stageKey) ? left.stageKey : "problem_modeling"
        ) as Parameters<typeof pagePhaseForUnderlying>[0];
        const phase = pagePhaseForUnderlying(stage);
        const row = out[phase];
        if (!row) continue;
        const screenSubs = (left.substages ?? []).map((sub, index) => {
          const existing =
            row.subStages.find((candidate) => candidate.id === sub.id) ??
            row.subStages[index];
          return {
            id: sub.id,
            subStage: sub.substageKey,
            label: sub.label,
            instanceSequence: sub.instanceSequence,
            objective: sub.objective ?? null,
            status: mapScreenStatusToNav(sub.status) as PhaseNavStatus,
            projectionStatus: sub.status,
            statusReasonCode: existing?.statusReasonCode ?? null,
            artifactRefs: existing?.artifactRefs ?? [],
          };
        });
        const stoppedByUser =
          model.interruption?.kind === "user_stop" ||
          model.interruption?.kind === "execution_cancelled" ||
          Boolean(userActions.snapshot?.executionStopped);
        const stageStatus =
          stoppedByUser &&
          (left.status === "running" || left.status === "queued")
            ? "stopped"
            : left.status;
        out[phase] = {
          ...row,
          label: left.label,
          pagePhaseLabel: left.label,
          status: mapScreenStatusToNav(stageStatus) as PhaseNavStatus,
          projectionStatus: stageStatus,
          planState: left.planState,
          subStages: screenSubs.length
            ? screenSubs.map((sub) => {
                if (
                  !stoppedByUser ||
                  (sub.projectionStatus !== "running" &&
                    sub.status !== "running")
                ) {
                  return sub;
                }
                return {
                  ...sub,
                  status: "failed" as PhaseNavStatus,
                  projectionStatus: "stopped",
                };
              })
            : row.subStages.map((sub) => ({
                ...sub,
                // Prefer durable projectionStatus already on the row.
                status: mapScreenStatusToNav(
                  stoppedByUser &&
                    (sub.projectionStatus === "running" ||
                      sub.status === "running")
                    ? "stopped"
                    : (sub.projectionStatus ?? sub.status),
                ) as PhaseNavStatus,
                projectionStatus:
                  stoppedByUser &&
                  (sub.projectionStatus === "running" ||
                    sub.status === "running")
                    ? "stopped"
                    : sub.projectionStatus,
              })),
        };
      }
    }
    return out;
  }, [model, screen, userActions.snapshot?.executionStopped]);

  const authoritativeCurrentSubstageIds = useMemo(
    () => (screen?.activeSubstages ?? []).map((substage) => substage.id),
    [screen?.activeSubstages],
  );
  const currentPagePhases = useMemo(() => {
    const phases = (screen?.activeSubstages ?? [])
      .map((substage) =>
        isUnderlyingStage(substage.stageKey)
          ? pagePhaseForUnderlying(substage.stageKey)
          : null,
      )
      .filter((phase): phase is PagePhaseId => Boolean(phase));
    const unique = phases.filter(
      (phase, index) => phases.indexOf(phase) === index,
    );
    return unique.length > 0
      ? unique
      : model
        ? [pagePhaseForUnderlying(model.currentUnderlyingStage)]
        : [];
  }, [model, screen?.activeSubstages]);
  const currentPagePhase = currentPagePhases[0] ?? null;
  const vp: PagePhaseId = viewingPhase ?? currentPagePhase ?? "problem";
  const viewStateMatchesModel = Boolean(
    model && artifactViewTaskId === model.taskId,
  );
  const viewStageRow = model ? stageRowForPagePhase(model, vp) : undefined;
  const currentStageRow =
    model && currentPagePhase
      ? stageRowForPagePhase(model, currentPagePhase)
      : undefined;

  const taskArtifacts = useMemo(
    () =>
      viewStateMatchesModel
        ? userVisibleWorkbenchArtifacts(model?.artifacts ?? [])
        : [],
    [model, viewStateMatchesModel],
  );
  const effectiveArtifactSelection = useMemo(() => {
    if (!model || !viewStateMatchesModel) return null;
    const snapshot = {
      ...artifactViewSnapshot(model),
      artifactIds: taskArtifacts.map((artifact) => artifact.id),
    };
    const selection = {
      taskId: model.taskId,
      revision: model.revision,
      researchDesignKey: snapshot.researchDesignKey,
      stageKey:
        viewStageRow?.stage ??
        currentStageRow?.stage ??
        model.currentUnderlyingStage,
      selectedSubstageId: requestedSubStage,
      focusedArtifactId: requestedArtifactId,
      scope: artifactScope,
      ownership: artifactViewOwnership,
    };
    return reconcileArtifactViewSelection({
      selection,
      snapshot,
      initialSelection: selection,
    });
  }, [
    model,
    viewStateMatchesModel,
    taskArtifacts,
    viewStageRow,
    currentStageRow,
    requestedSubStage,
    requestedArtifactId,
    artifactScope,
    artifactViewOwnership,
  ]);
  const selectedSubStage =
    effectiveArtifactSelection?.selectedSubstageId ?? null;
  const focusedArtifactId =
    effectiveArtifactSelection?.focusedArtifactId ?? null;

  const selectedSubRow = useMemo(
    () =>
      viewStateMatchesModel
        ? (viewStageRow?.subStages.find((s) => s.id === selectedSubStage) ??
          null)
        : null,
    [selectedSubStage, viewStageRow, viewStateMatchesModel],
  );
  const interactionScrollContextKey = model
    ? [
        model.taskId,
        vp,
        selectedSubRow?.id ??
          (viewStateMatchesModel ? selectedSubStage : "no-substage"),
      ].join(":")
    : null;

  useLayoutEffect(() => {
    if (rightPanelCollapsed || !interactionScrollContextKey) return undefined;
    const savedTop = interactionScrollTopByContextRef.current.get(
      interactionScrollContextKey,
    );
    if (typeof savedTop !== "number") return undefined;

    const restore = () => {
      const scroll = document.querySelector(
        '[data-testid="workbench-interaction-scroll"]',
      ) as HTMLElement | null;
      if (!scroll) return;
      const maxTop = Math.max(0, scroll.scrollHeight - scroll.clientHeight);
      scroll.scrollTop = Math.min(Math.max(savedTop, 0), maxTop);
    };
    const frame = window.requestAnimationFrame(restore);
    const timers = [50, 150, 300].map((delay) =>
      window.setTimeout(restore, delay),
    );
    return () => {
      window.cancelAnimationFrame(frame);
      timers.forEach((timer) => window.clearTimeout(timer));
    };
  }, [rightPanelCollapsed, interactionScrollContextKey]);

  const phaseArtifacts = useMemo(() => {
    if (!model || !viewStageRow || !viewStateMatchesModel) return [];
    return userVisibleWorkbenchArtifacts(
      uniqueArtifacts([
        ...artifactsByRefs(model, viewStageRow.artifactRefs),
        ...model.artifacts.filter(
          (artifact) => artifact.relatedStage === viewStageRow.stage,
        ),
      ]),
    ).filter((artifact) => {
      const location = resolveArtifactLocation(model, artifact.id);
      return (
        !isResearchStartDraftArtifact(artifact) &&
        location.kind === "located" &&
        location.stageKey === viewStageRow.stage
      );
    });
  }, [model, viewStageRow, viewStateMatchesModel]);

  const subArtifacts = useMemo(() => {
    if (!model || !selectedSubRow) return [];
    return userVisibleWorkbenchArtifacts(
      uniqueArtifacts([
        ...artifactsByRefs(model, selectedSubRow.artifactRefs),
        ...model.artifacts.filter(
          (artifact) => artifact.subStageInstanceId === selectedSubRow.id,
        ),
      ]),
    ).filter((artifact) => {
      const location = resolveArtifactLocation(model, artifact.id);
      return (
        !isResearchStartDraftArtifact(artifact) &&
        location.kind === "located" &&
        location.substageInstanceId === selectedSubRow.id
      );
    });
  }, [model, selectedSubRow]);

  const currentAcceptedWorkflowNodeProducts = useMemo(() => {
    if (!model) return [];
    const byKind = new Map<string, WorkbenchArtifact[]>();
    for (const artifact of taskArtifacts) {
      const kind = workflowNodeResultPresentationKind(artifact);
      if (!kind || !artifactHumanGate(artifact).canUseDownstream) continue;
      if (resolveArtifactLocation(model, artifact.id).kind !== "located")
        continue;
      const rows = byKind.get(kind) ?? [];
      rows.push(artifact);
      byKind.set(kind, rows);
    }
    return WORKFLOW_NODE_RESULT_PRESENTATION_ORDER.flatMap((kind) => {
      const rows = byKind.get(kind) ?? [];
      return rows.length === 1 ? rows : [];
    });
  }, [model, taskArtifacts]);

  const currentAcceptedWorkflowNodeProductIds = useMemo(
    () =>
      new Set(
        currentAcceptedWorkflowNodeProducts.map((artifact) => artifact.id),
      ),
    [currentAcceptedWorkflowNodeProducts],
  );

  const visibleArtifacts = useMemo(
    () =>
      artifactsForExplicitScope({
        scope: artifactScope,
        substageArtifacts: subArtifacts,
        stageArtifacts: phaseArtifacts,
        taskArtifacts,
      }),
    [artifactScope, phaseArtifacts, subArtifacts, taskArtifacts],
  );

  const focusedArtifact = useMemo(() => {
    if (!model || !viewStateMatchesModel) return null;
    const selectedId = chooseFocusedArtifactId({
      focusedArtifactId,
      deepLinkedArtifactId: artifactParam,
      visibleArtifactIds: visibleArtifacts.map((artifact) => artifact.id),
      allArtifactIds: taskArtifacts.map((artifact) => artifact.id),
      formatsById: new Map(
        visibleArtifacts.map((artifact) => [artifact.id, artifact.format]),
      ),
    });
    return selectedId
      ? (taskArtifacts.find((artifact) => artifact.id === selectedId) ?? null)
      : null;
  }, [
    artifactParam,
    focusedArtifactId,
    model,
    taskArtifacts,
    viewStateMatchesModel,
    visibleArtifacts,
  ]);

  const latestCurrentOutput = useMemo(() => {
    if (!model || !currentStageRow) return null;
    const currentCallIds = new Set(
      model.capabilityCalls
        .filter(
          (call) =>
            call.stageKey === currentStageRow.stage &&
            (call.status === "completed" || call.status === "succeeded"),
        )
        .map((call) => call.id),
    );
    const currentOutputIds = new Set(
      model.capabilityCalls
        .filter((call) => currentCallIds.has(call.id))
        .flatMap((call) => [
          ...(call.outputArtifactRefs ?? []),
          ...(call.artifactRefs ?? []),
        ]),
    );
    return (
      [...taskArtifacts]
        .reverse()
        .find(
          (artifact) =>
            !isResearchStartDraftArtifact(artifact) &&
            artifact.relatedStage === currentStageRow.stage &&
            ((artifact.sourceCapabilityCallId != null &&
              currentCallIds.has(artifact.sourceCapabilityCallId)) ||
              currentOutputIds.has(artifact.id)),
        ) ?? null
    );
  }, [currentStageRow, model, taskArtifacts]);

  useEffect(() => {
    if (!model || load.status !== "ready") return;
    if (!focusedArtifact || focusedArtifact.content) return;
    if (NON_BODY_ARTIFACT_FORMATS.has(focusedArtifact.format ?? "")) return;
    void hydrate(model.taskId, focusedArtifact.id);
  }, [focusedArtifact, hydrate, load.status, model]);

  useEffect(() => {
    if (!model || load.status !== "ready" || !focusedArtifact) return;
    for (const source of resolveEvidenceGraphResearchRecordsArtifacts(focusedArtifact, model)) {
      if (!source.content && !NON_BODY_ARTIFACT_FORMATS.has(source.format ?? ""))
        void hydrate(model.taskId, source.id);
    }
  }, [focusedArtifact, hydrate, load.status, model]);

  useEffect(() => {
    if (!model || load.status !== "ready" || loadSource !== "api") return;
    for (const artifact of currentAcceptedWorkflowNodeProducts) {
      if (
        !artifact.content &&
        !NON_BODY_ARTIFACT_FORMATS.has(artifact.format ?? "")
      ) {
        void hydrate(model.taskId, artifact.id);
      }
    }
  }, [
    currentAcceptedWorkflowNodeProducts,
    hydrate,
    load.status,
    loadSource,
    model,
  ]);

  // issue #302: 产物版本管理 —— 编辑 / 退回重生成 可用性（工作台入口曾被中间栏精简误删，现恢复）
  const focusedArtifactFormat = (focusedArtifact?.format ?? "").toLowerCase();
  const isFocusedArtifactEditable = Boolean(
    focusedArtifact &&
    load.status === "ready" &&
    load.source === "api" &&
    (["markdown", "md", "text", "txt"].includes(focusedArtifactFormat) ||
      focusedArtifact.type === "research_start_draft" ||
      (focusedArtifact.path ?? "").toLowerCase().endsWith(".md")),
  );
  // During a canonical interruption, recovery must use a current scoped action.
  // Preserve the existing Artifact version operation outside that interruption.
  const canRegenerateFocused = Boolean(
    !(
      model?.progressTrust.kind === "canonical" &&
      researchInterruptionPresentation(model)
    ) &&
    focusedArtifact &&
    load.status === "ready" &&
    load.source === "api" &&
    focusedArtifact.artifactType === "stage_summary",
  );

  const isHistoricalView = Boolean(
    viewStateMatchesModel &&
    model &&
    currentPagePhases.length > 0 &&
    !currentPagePhases.includes(vp),
  );
  const persistNav = useCallback(
    (phase: PagePhaseId, sub: string | null) => {
      if (!model) return;
      const prev = readWorkbenchLocal(model.taskId);
      writeWorkbenchLocal(model.taskId, {
        ...prev,
        lastViewPhaseId: phase,
        lastSubStageKey: sub,
      });
    },
    [model],
  );

  const selectArtifact = useCallback(
    (
      artifactId: string,
      nextScope?: ArtifactScope,
      nextOwnership: ArtifactViewOwnership = "pinned",
      syncRoute = true,
    ) => {
      if (!model) return;
      const artifact = taskArtifacts.find((item) => item.id === artifactId);
      if (!artifact) return;
      if (nextOwnership === "pinned") {
        const body = document.querySelector<HTMLElement>(".workbench-center-scroll");
        if (body) body.scrollTop = 0;
      }

      const phase = pagePhaseForUnderlying(artifact.relatedStage);
      const location = resolveArtifactLocation(model, artifactId);
      const subKey =
        location.kind === "located" ? location.substageInstanceId : null;
      const scope =
        nextScope ?? (location.kind === "located" ? "substage" : "task");

      setArtifactSelection((previous) => ({
        ...previous,
        viewingPhase: phase,
        selectedSubStage: subKey,
        focusedArtifactId: artifactId,
        artifactScope: scope,
        artifactViewOwnership: nextOwnership,
      }));
      if (syncRoute) syncArtifactRoute(artifactId);
      if (isMobileWorkbench) setMobilePane("artifact");
      persistNav(phase, subKey);
    },
    [
      model,
      taskArtifacts,
      syncArtifactRoute,
      isMobileWorkbench,
      setMobilePane,
      persistNav,
    ],
  );

  const onSelectPhase = (p: PagePhaseId) => {
    const sub = pickDefaultSub(p);
    setArtifactSelection((previous) => ({
      ...previous,
      viewingPhase: p,
      selectedSubStage: sub,
      focusedArtifactId: null,
      artifactScope: sub ? "substage" : "stage",
      artifactViewOwnership: "pinned",
    }));
    syncArtifactRoute(null);
    if (isMobileWorkbench) setMobilePane("artifact");
    persistNav(p, sub);
  };

  const onSelectSubStage = (subStage: string) => {
    setArtifactSelection((previous) => ({
      ...previous,
      selectedSubStage: subStage,
      focusedArtifactId: null,
      artifactScope: "substage",
      artifactViewOwnership: "pinned",
    }));
    syncArtifactRoute(null);
    if (isMobileWorkbench) setMobilePane("artifact");
    persistNav(vp, subStage);
  };

  const onSelectArtifactChip = (artifactId: string) => {
    selectArtifact(artifactId);
  };

  const latestCurrentOutputLocation = useMemo(
    () =>
      latestCurrentOutput && model
        ? resolveArtifactLocation(model, latestCurrentOutput.id)
        : null,
    [latestCurrentOutput, model],
  );

  useEffect(() => {
    if (!latestCurrentOutput || viewingConfirmedPlan) return;
    if (
      !shouldAutoFollowCurrentArtifact({
        ownership: artifactViewOwnership,
        latestArtifactId: latestCurrentOutput.id,
        focusedArtifactId: focusedArtifact?.id ?? null,
        deepLinkedArtifactId: artifactParam,
        location: latestCurrentOutputLocation,
      })
    )
      return;
    selectArtifact(
      latestCurrentOutput.id,
      "substage",
      "following_current",
      false,
    );
  }, [
    artifactParam,
    viewingConfirmedPlan,
    artifactViewOwnership,
    focusedArtifact?.id,
    latestCurrentOutput,
    latestCurrentOutputLocation,
    selectArtifact,
  ]);

  const onExecutionModeChange = async (mode: TaskExecutionMode) => {
    if (!model || load.status !== "ready" || load.source !== "api") {
      message.info("当前项目暂不能切换推进方式。");
      return;
    }
    if (isTerminalTaskStatus(model.taskStatus)) {
      message.info("任务已结束，不能切换推进方式。");
      return;
    }
    if (
      executionPolicy?.taskId !== model.taskId ||
      executionPolicyUnavailable
    ) {
      message.info("推进方式暂不可确认。");
      return;
    }
    let patchError: unknown = null;
    try {
      setModeSaving(true);
      await updateTaskExecutionPolicy(model.taskId, mode);
    } catch (err) {
      patchError = err;
    } finally {
      const policyConfirmed = await refreshExecutionPolicy();
      void load.reload();
      if (!policyConfirmed) {
        message.warning("推进方式暂无法确认，请稍后重试。");
      } else if (patchError) {
        message.warning("当前推进方式已重新同步。");
      } else {
        message.success("推进方式已更新。");
      }
      setModeSaving(false);
    }
  };

  const onConfirmDraftStart = async () => {
    if (!model || load.status !== "ready" || load.source !== "api") {
      message.info("当前项目暂不能从这里开始研究。");
      return;
    }
    if (
      !model.creationPlan ||
      model.creationStatus !== "awaiting_confirmation"
    ) {
      message.info("请先生成开始建议，再确认开始。");
      return;
    }
    const requestedTaskId = model.taskId;
    let planConfirmed = false;
    let selectionRequestId: string | null = null;
    try {
      setRunAction("advance");
      const confirmedTask =
        model.researchDesign?.status === "draft"
          ? await confirmResearchDesign(requestedTaskId, {
              designId: model.researchDesign.designId,
              version: model.researchDesign.version,
              confirmationId: crypto.randomUUID(),
              decision: "approved",
              expectedTaskRevision: model.revision,
            })
          : await confirmTaskPlan(
              requestedTaskId,
              researchStartConfirmPayload(),
            );
      planConfirmed = true;
      if (typeof confirmedTask.revision !== "number") {
        throw new Error("confirmed task revision missing");
      }
      if (startStatusTaskIdRef.current !== requestedTaskId) return;

      if (model.researchDesign?.status === "draft") {
        if (
          !["automatic_research", "stage_confirmation"].includes(
            executionPolicy?.currentMode ?? "",
          )
        ) {
          load.reload();
          durableProjection.refresh();
          message.success("研究方案已确认。当前推进方式需要手动开始下一步。");
          return;
        }
        selectionRequestId = `research-design:auto-start:${model.researchDesign.designId}:v${model.researchDesign.version}`;
        rememberDurableStartIntent(requestedTaskId, selectionRequestId);
        await recoverCanonicalStartStatus(requestedTaskId, selectionRequestId);
        return;
      }

      selectionRequestId = crypto.randomUUID();
      rememberDurableStartIntent(requestedTaskId, selectionRequestId);
      const fence: TaskStartStatusFence = {
        taskId: requestedTaskId,
        selectionRequestId,
        taskRevision: startStatusTaskRevisionRef.current,
        generation: ++startStatusGenerationRef.current,
      };
      await startTask(
        requestedTaskId,
        confirmedTask.revision,
        selectionRequestId,
        "initial_start",
      );
      if (!isCurrentStartStatusOperation(fence)) return;
      await recoverCanonicalStartStatus(requestedTaskId, selectionRequestId);
    } catch {
      if (planConfirmed && selectionRequestId) {
        await recoverCanonicalStartStatus(requestedTaskId, selectionRequestId);
      } else if (startStatusTaskIdRef.current === requestedTaskId) {
        message.warning(
          planConfirmed
            ? "草案已确认，但启动准备未完成。请刷新后重试。"
            : "草案确认失败，请刷新后重试。",
        );
      }
    } finally {
      if (startStatusTaskIdRef.current === requestedTaskId) setRunAction(null);
    }
  };

  const isCurrentRecoveryOperation = (generation: number, fenceKey: string) =>
    recoveryGenerationRef.current === generation &&
    recoveryFenceRef.current === fenceKey;

  const cacheForDiscoveredRecovery = (
    response: RecoveryRequestResponse,
    cacheHint: RecoveryRequestCacheEntry | null,
  ): RecoveryRequestCacheEntry => {
    const matchingHint =
      cacheHint &&
      cacheHint.taskId === response.taskId &&
      cacheHint.sourceRunId === response.sourceRunId &&
      cacheHint.expectedTaskRevision === response.expectedTaskRevision
        ? cacheHint
        : null;
    return {
      taskId: response.taskId,
      expectedTaskRevision: response.expectedTaskRevision,
      sourceRunId: response.sourceRunId,
      recoveryRequestId: response.recoveryRequestId,
      request: matchingHint?.request ?? {
        sourceRunId: response.sourceRunId,
        recoveryKind: response.recoveryKind,
        expectedTaskRevision: response.expectedTaskRevision,
        messageIds: recoveryMessageIds.length
          ? recoveryMessageIds
          : [`discovered:${response.recoveryRequestId}`],
        idempotencyKey: `discovered:${response.recoveryRequestId}`,
      },
    };
  };

  const discoverExistingRecovery = async (
    generation: number,
    fenceKey: string,
    cacheHint: RecoveryRequestCacheEntry | null,
  ): Promise<RecoveryRequestResponse | null> => {
    if (
      !isLegacyRecoveryLifecycle ||
      !recoveryFence.taskId ||
      !recoveryFence.sourceRunId ||
      recoveryFence.expectedTaskRevision < 0
    ) {
      return null;
    }
    if (isCurrentRecoveryOperation(generation, fenceKey)) {
      setRecoverySession({
        response: null,
        cache: cacheHint,
        phase: "reconciling",
        responseUnknown: false,
        error: null,
      });
    }
    try {
      const response = await discoverRecoveryRequest(
        recoveryFence.taskId,
        recoveryFence.sourceRunId,
        recoveryFence.expectedTaskRevision,
      );
      if (!isCurrentRecoveryOperation(generation, fenceKey)) return null;
      if (
        response.taskId !== recoveryFence.taskId ||
        response.sourceRunId !== recoveryFence.sourceRunId ||
        response.expectedTaskRevision !== recoveryFence.expectedTaskRevision
      ) {
        setRecoverySession({
          response: null,
          cache: cacheHint,
          phase: "idle",
          responseUnknown: true,
          error: null,
        });
        return null;
      }
      const cache = cacheForDiscoveredRecovery(response, cacheHint);
      writeRecoveryRequestCache(cache);
      setRecoverySession({
        response,
        cache,
        phase: "idle",
        responseUnknown: false,
        error: null,
      });
      return response;
    } catch (error) {
      if (!isCurrentRecoveryOperation(generation, fenceKey)) return null;
      const recoveryError =
        error instanceof RecoveryApiError
          ? error
          : new RecoveryApiError("恢复请求暂不可确认。", {
              responseUnknown: true,
            });
      if (recoveryError.status === 404 && !cacheHint) {
        setRecoverySession(EMPTY_RECOVERY_SESSION);
        return null;
      }
      setRecoverySession({
        response: null,
        cache: cacheHint,
        phase: "idle",
        responseUnknown:
          recoveryError.responseUnknown || recoveryError.status === 404,
        error:
          recoveryError.responseUnknown || recoveryError.status === 404
            ? null
            : recoveryError,
      });
      return null;
    }
  };

  const reconcileRecoveryRequest = async (
    cache: RecoveryRequestCacheEntry,
    generation: number,
    fenceKey: string,
  ): Promise<void> => {
    if (!cache.recoveryRequestId) {
      await discoverExistingRecovery(generation, fenceKey, cache);
      return;
    }
    if (isCurrentRecoveryOperation(generation, fenceKey)) {
      setRecoverySession({
        response: null,
        cache,
        phase: "reconciling",
        responseUnknown: false,
        error: null,
      });
    }
    try {
      const response = await fetchRecoveryRequest(
        cache.taskId,
        cache.recoveryRequestId,
      );
      if (!isCurrentRecoveryOperation(generation, fenceKey)) return;
      if (
        response.taskId !== cache.taskId ||
        response.sourceRunId !== cache.sourceRunId ||
        response.expectedTaskRevision !== cache.expectedTaskRevision
      ) {
        setRecoverySession({
          response: null,
          cache,
          phase: "idle",
          responseUnknown: true,
          error: null,
        });
        return;
      }
      const nextCache = cacheForDiscoveredRecovery(response, cache);
      writeRecoveryRequestCache(nextCache);
      setRecoverySession({
        response,
        cache: nextCache,
        phase: "idle",
        responseUnknown: false,
        error: null,
      });
    } catch (error) {
      if (!isCurrentRecoveryOperation(generation, fenceKey)) return;
      const recoveryError =
        error instanceof RecoveryApiError
          ? error
          : new RecoveryApiError("恢复请求暂不可确认。", {
              responseUnknown: true,
            });
      if (recoveryError.status === 404) {
        await discoverExistingRecovery(generation, fenceKey, cache);
        return;
      }
      setRecoverySession({
        response: null,
        cache,
        phase: "idle",
        responseUnknown: recoveryError.responseUnknown,
        error: recoveryError.responseUnknown ? null : recoveryError,
      });
    }
  };

  const reconcileRecovery = async (): Promise<void> => {
    const cache = recoverySession.cache;
    if (!cache || !hasPendingRecoveryCache) return;
    const generation = recoveryGenerationRef.current;
    const fenceKey = currentRecoveryFenceKey;
    if (!cache.recoveryRequestId) {
      await discoverExistingRecovery(generation, fenceKey, cache);
      return;
    }
    await reconcileRecoveryRequest(cache, generation, fenceKey);
  };

  useEffect(() => {
    if (
      !isLegacyRecoveryLifecycle ||
      !recoveryFence.taskId ||
      !recoveryFence.sourceRunId ||
      recoveryFence.expectedTaskRevision < 0
    )
      return;
    const cache = readRecoveryRequestCache(
      recoveryFence.taskId,
      recoveryFence.expectedTaskRevision,
      recoveryFence.sourceRunId,
    );
    const generation = recoveryGenerationRef.current;
    const fenceKey = currentRecoveryFenceKey;
    void discoverExistingRecovery(generation, fenceKey, cache);
  }, [currentRecoveryFenceKey, isLegacyRecoveryLifecycle]);

  const onSubmitControllerSupplement =
    async (): Promise<ControllerCommand | null> => {
      const text = supplementNote.trim();
      if (
        !model ||
        load.status !== "ready" ||
        load.source !== "api" ||
        !text ||
        userActions.controller.busy ||
        collaborationSubmitting
      )
        return null;
      const submittedDraftVersion = supplementDraftVersionRef.current;
      const submittedTaskId = model.taskId;
      let planRegeneration: AppliedCollaborationAction | null = null;
      setCollaborationSubmitting(true);
      try {
        const result = await postAgentChatStream(
          model.taskId,
          text,
          "workbench_chat",
          () => undefined,
        );
        if (startStatusTaskIdRef.current !== submittedTaskId) return null;
        if (result.error && !(result.reply || "").trim()) {
          message.warning(
            "研究助手暂时无法回复，请稍后重试。未发送的内容会保留。",
          );
          return null;
        }
        planRegeneration = result.appliedAction ?? null;
        if (supplementDraftVersionRef.current === submittedDraftVersion)
          setSupplementNote("");
      } catch {
        if (startStatusTaskIdRef.current !== submittedTaskId) return null;
        message.warning(
          "研究助手暂时无法回复，请稍后重试。未发送的内容会保留。",
        );
        return null;
      } finally {
        setCollaborationSubmitting(false);
      }
      reloadTaskFromResearchActivity();
      await userActions.refresh().catch(() => null);
      if (startStatusTaskIdRef.current !== submittedTaskId) return null;
      const nextPlanning = planningSessionAfterRegeneration(
        searchParams,
        planRegeneration,
      );
      if (nextPlanning) setSearchParams(nextPlanning, { replace: true });
      return null;
    };

  const onSupplementNoteChange = (value: string) => {
    supplementDraftVersionRef.current += 1;
    setSupplementNote(value);
  };

  const onStartConfirmedTask = async () => {
    if (!model || load.status !== "ready" || load.source !== "api") {
      message.info("当前页面只能在已连接 API 的真实任务中开始执行。");
      return;
    }
    if (!activeTaskStartStatus) {
      message.info(START_STATUS_CHECKING_MESSAGE);
      return;
    }
    if (!activeTaskStartStatus.startAllowed) {
      message.warning(
        activeTaskStartStatus.reasonSummary ??
          taskStartStatusMessage(
            activeTaskStartStatus.state,
            activeTaskStartStatus.blockingReasonCode,
          ),
      );
      return;
    }
    if (runAction || startSubmitInFlightTaskRef.current === model.taskId) {
      message.info("任务启动请求正在处理中，请勿重复提交。");
      return;
    }
    if (
      model.taskStatus !== "not_started" ||
      model.creationStatus !== "confirmed"
    ) {
      message.info("任务当前不处于可开始状态，请刷新后重试。");
      return;
    }
    if (durableProjection.run) {
      durableProjection.refresh();
      load.reload();
      message.info("开始请求已提交，正在确认执行状态。");
      return;
    }
    const requestedTaskId = model.taskId;
    const selectionRequestId =
      readDurableStartIntent(requestedTaskId) ?? crypto.randomUUID();
    try {
      startSubmitInFlightTaskRef.current = requestedTaskId;
      setRunAction("start");
      rememberDurableStartIntent(requestedTaskId, selectionRequestId);
      const fence: TaskStartStatusFence = {
        taskId: requestedTaskId,
        selectionRequestId,
        taskRevision: startStatusTaskRevisionRef.current,
        generation: ++startStatusGenerationRef.current,
      };
      await startTask(
        requestedTaskId,
        model.revision,
        selectionRequestId,
        "initial_start",
      );
      if (!isCurrentStartStatusOperation(fence)) return;
      await recoverCanonicalStartStatus(requestedTaskId, selectionRequestId);
    } catch {
      await recoverCanonicalStartStatus(requestedTaskId, selectionRequestId);
    } finally {
      if (startSubmitInFlightTaskRef.current === requestedTaskId) {
        startSubmitInFlightTaskRef.current = null;
      }
      if (startStatusTaskIdRef.current === requestedTaskId) setRunAction(null);
    }
  };

  const onContinueCanonicalResearch = async () => {
    if (
      !model ||
      load.status !== "ready" ||
      load.source !== "api" ||
      runAction ||
      !userActions.snapshot ||
      !hasWorkbenchTaskAction(
        userActions.snapshot,
        model.taskId,
        model.revision,
        "continue_research",
      )
    ) {
      message.info("当前研究状态已变化，请刷新后再继续。");
      return;
    }
    const requestedTaskId = model.taskId;
    if (userActions.snapshot.executionStopped) {
      try {
        setRunAction("advance");
        const resumed = await userActions.resume();
        load.reload();
        durableProjection.refresh();
        if (resumed) message.success("研究已继续");
      } finally {
        setRunAction(null);
      }
      return;
    }
    const selectionRequestId =
      readDurableStartIntent(requestedTaskId) ?? crypto.randomUUID();
    try {
      setRunAction("advance");
      rememberDurableStartIntent(requestedTaskId, selectionRequestId);
      const fence: TaskStartStatusFence = {
        taskId: requestedTaskId,
        selectionRequestId,
        taskRevision: startStatusTaskRevisionRef.current,
        generation: ++startStatusGenerationRef.current,
      };
      await startTask(
        requestedTaskId,
        model.revision,
        selectionRequestId,
        "continue_research",
      );
      if (!isCurrentStartStatusOperation(fence)) return;
      await recoverCanonicalStartStatus(requestedTaskId, selectionRequestId);
    } catch {
      await recoverCanonicalStartStatus(requestedTaskId, selectionRequestId);
    } finally {
      if (startStatusTaskIdRef.current === requestedTaskId) setRunAction(null);
    }
  };

  const runTaskAction = async () => {
    if (
      !model ||
      load.status !== "ready" ||
      load.source !== "api" ||
      runAction
    ) {
      message.info("当前页面只能在已连接 API 的真实任务中执行该动作。");
      return;
    }
    setRunAction("stop");
    try {
      await userActions.stop();
    } finally {
      setRunAction(null);
    }
  };

  const headerAction = useMemo(
    () => (
      <Space size={4}>
        <Tooltip title={leftPanelCollapsed ? "显示研究阶段" : "隐藏研究阶段"}>
          <Button
            type="text"
            size="small"
            className="max-[760px]:hidden"
            icon={
              leftPanelCollapsed ? (
                <PanelLeft size={16} aria-hidden />
              ) : (
                <PanelLeftClose size={16} aria-hidden />
              )
            }
            aria-label={leftPanelCollapsed ? "显示研究阶段" : "隐藏研究阶段"}
            data-testid="toggle-left-panel"
            onClick={() => setLeftPanelCollapsed((collapsed) => !collapsed)}
          />
        </Tooltip>
        <Tooltip title={headerCollapsed ? "显示任务栏" : "隐藏任务栏"}>
          <Button
            type="text"
            size="small"
            icon={
              headerCollapsed ? (
                <PanelTop size={16} aria-hidden />
              ) : (
                <PanelTopClose size={16} aria-hidden />
              )
            }
            aria-label={headerCollapsed ? "显示任务栏" : "隐藏任务栏"}
            data-testid="toggle-workbench-header"
            onClick={() => setHeaderCollapsed((collapsed) => !collapsed)}
          />
        </Tooltip>
        <Tooltip title={rightPanelCollapsed ? "显示协作区" : "隐藏协作区"}>
          <Button
            type="text"
            size="small"
            className="max-[760px]:hidden"
            icon={
              rightPanelCollapsed ? (
                <PanelRight size={16} aria-hidden />
              ) : (
                <PanelRightClose size={16} aria-hidden />
              )
            }
            aria-label={rightPanelCollapsed ? "显示协作区" : "隐藏协作区"}
            data-testid="toggle-right-panel"
            onClick={() => setRightPanelCollapsed((collapsed) => !collapsed)}
          />
        </Tooltip>
        <Divider vertical classNames={{ rail: "bg-border" }} />
      </Space>
    ),
    [headerCollapsed, leftPanelCollapsed, rightPanelCollapsed],
  );
  usePageHeader({ action: headerAction });

  if (taskId === "current" && !effectiveTaskId) {
    return (
      <div className="flex h-full items-center justify-center bg-bg px-6">
        <div className="grid w-full max-w-2xl gap-4 rounded-lg border border-border bg-surface p-6 shadow-sm">
          <Typography.Title level={3} className="m-0! text-text">
            还没有打开的项目
          </Typography.Title>
          <Typography.Paragraph className="m-0! text-text-secondary">
            当前没有正在查看的项目。你可以回到项目看板选择一个项目，或回到首页开始新项目。
          </Typography.Paragraph>
          <Space wrap>
            <Button type="primary" onClick={() => navigate("/workbench")}>
              去项目看板
            </Button>
            <Button onClick={() => navigate("/?intent=create")}>
              返回科研起点
            </Button>
          </Space>
        </div>
      </div>
    );
  }

  if (
    !planningOccupiesShell &&
    (load.status === "loading" ||
      (load.status === "ready" && viewingPhase === null))
  ) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 bg-bg">
        <Spin size="large" />
        <Typography.Paragraph type="secondary">
          加载工作台…
        </Typography.Paragraph>
      </div>
    );
  }

  if (!planningOccupiesShell && load.status === "error") {
    const isAuthError = load.errorKind === "unauthorized";
    const isForbidden = load.errorKind === "forbidden";
    const surfaceMessage = isAuthError
      ? TASK_LOAD_ZH.unauthorized
      : isForbidden
        ? TASK_LOAD_ZH.forbidden
        : load.message;
    return (
      <div style={{ padding: 24 }}>
        <Alert
          type={isAuthError ? "warning" : "error"}
          showIcon
          title={surfaceMessage}
          description={isForbidden ? undefined : surfaceMessage}
          data-testid="workbench-load-error"
        />
        <div style={{ marginTop: 16, display: "flex", gap: 8 }}>
          <Link to="/workbench">
            <Button>返回看板</Button>
          </Link>
          {isAuthError && (
            <Button
              type="primary"
              onClick={() => {
                logout();
                window.location.href = "/login";
              }}
            >
              重新登录
            </Button>
          )}
          {!isAuthError && <Button onClick={() => load.reload()}>重试</Button>}
        </div>
      </div>
    );
  }

  if (!model) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 bg-bg">
        <Spin size="large" />
        <Typography.Paragraph type="secondary">
          加载工作台…
        </Typography.Paragraph>
      </div>
    );
  }
  const m: WorkbenchTaskModel = model;
  const showPrimaryDelivery = artifactViewOwnership !== "pinned";
  const supportingFocusedArtifact =
    focusedArtifact &&
    (!showPrimaryDelivery ||
      !currentAcceptedWorkflowNodeProductIds.has(focusedArtifact.id))
      ? focusedArtifact
      : null;
  const visibleControllerPendingMessage =
    userActions.controller.intent?.taskId === m.taskId &&
    userActions.controller.busy
      ? userActions.controller.intent.text
      : null;
  const visibleControllerReceipt =
    userActions.controller.receipt?.taskId === m.taskId
      ? userActions.controller.receipt
      : null;
  const displayTaskTitle = m.title.trim() || "新研究";
  const isDraft = m.taskStatus === "draft";
  const interruptionView = researchInterruptionPresentation(m);
  const isPlanningDraft = isDraft && !m.creationPlan && !m.researchDesign;
  const planNotYetFormed = !m.creationPlan && !m.researchDesign;
  const planGenerationFailed = isPlanGenerationFailure({
    planNotYetFormed,
    taskStatus: m.taskStatus,
    creationStatus: m.creationStatus,
    planningStatus: visiblePlanningStatus?.status ?? null,
  });
  const retainPlanningFailureContext =
    planNotYetFormed &&
    (m.taskStatus === "failed" || visiblePlanningStatus?.status === "failed");
  const isPlanConfirmationPending =
    Boolean(m.creationPlan) &&
    m.creationStatus === "awaiting_confirmation" &&
    (m.taskStatus === "draft" || m.taskStatus === "awaiting_confirmation");
  const isPlanRevisionRequired =
    Boolean(m.creationPlan) &&
    m.creationStatus === "revision_requested" &&
    m.taskStatus === "draft";
  const isPlanEditableDraft =
    isPlanConfirmationPending || isPlanRevisionRequired;
  const isCanonicalResearchDraft =
    isPlanEditableDraft &&
    m.creationPlan?.planningSource === "research_design_v1_2";
  const isConfirmedCanonicalResearchDesign =
    m.creationPlan?.planningSource === "research_design_v1_2" &&
    m.creationStatus === "confirmed" &&
    m.taskStatus === "not_started";
  const isCanonicalResearchDesignVisible =
    isCanonicalResearchDraft || isConfirmedCanonicalResearchDesign;
  const isPreExecutionPlan = isPlanningDraft || isPlanEditableDraft;
  const isNotStartedConfirmed =
    m.taskStatus === "not_started" && m.creationStatus === "confirmed";
  const isTemplatePlanningFallback =
    m.creationPlan?.planningSource === "deterministic_fallback";

  const decisionState = buildWorkbenchDecisionState({
    model: m,
    stage: viewStageRow,
    selectedArtifact: focusedArtifact,
    isHistoricalView,
    taskStatusLabel: STATUS_LABELS[m.taskStatus],
  });
  // Built once above from the Task durable progress projection.
  const screenView = screen ?? buildWorkbenchScreenView(m);
  const planningStatusActive =
    isPlanningDraft || planningOccupiesShell || retainPlanningFailureContext;
  const workbenchUserState = projectWorkbenchUserState({
    planningStatusActive,
    planningStatus: visiblePlanningStatus,
    planNotYetFormed,
    isPlanEditableDraft,
    isNotStartedConfirmed,
    taskStatus: m.taskStatus,
    creationStatus: m.creationStatus,
    executionDisplayStatus: screenView.executionDisplayStatus,
    interruptionKind: m.interruption?.kind ?? null,
    executionStopped: userActions.snapshot?.executionStopped,
    awaitingUserConfirmation:
      screenView.executionDisplayStatus === "needs_input",
  });
  const actionUnavailableReason =
    loadSource !== "api"
      ? "当前是示例或恢复视图，只展示状态；执行动作请回到真实任务工作台。"
      : isHistoricalView
        ? "正在查看历史阶段，请先返回当前阶段。"
        : null;
  const researchStartArtifact =
    m.artifacts.find((artifact) => isResearchStartDraftArtifact(artifact)) ??
    null;
  const structuredResearchStart =
    researchStartArtifact?.structuredResearchStart ?? null;
  const structuredBoundaries =
    structuredResearchStart?.non_citable_boundaries ?? [
      "当前尚未读取外部论文、数据或代码，不能把本草案作为可引用科研结论。",
    ];
  const canConfirmDraftStart = isPlanConfirmationPending;
  const researchStartConfirmPayload = (): ResearchStartConfirmationPayload => ({
    researchGoalConfirmed: true,
    existingMaterialsConfirmed: true,
    missingMaterialsDecision: "accept_limitations",
    nonCitableBoundariesAccepted: true,
    nextStepLimitationsConfirmed: true,
    acceptedLimitations: structuredBoundaries,
    notes: supplementNote.trim() || null,
  });
  const rightPanelName = "协作区";
  const showLeftPanel = !isMobileWorkbench || mobilePane === "stage";
  const showCenterPanel = !isMobileWorkbench || mobilePane === "artifact";
  const showRightPanel = !isMobileWorkbench || mobilePane === "support";
  const renderLeftCollapsed = !isMobileWorkbench && leftPanelCollapsed;
  const renderRightCollapsed = !isMobileWorkbench && rightPanelCollapsed;
  const canonicalResearchDraftStages = isCanonicalResearchDesignVisible
    ? (m.creationPlan?.stages ?? []).filter(
        (stage) => (stage.substages ?? []).length > 0,
      )
    : [];

  const draftPlanStageSummaries = (m.creationPlan?.stages ?? [])
    .map((stage) => {
      const title = researchDraftStageTitle(stage);
      if (title) return title;
      const description = researchDraftText(stage.description);
      if (description) return description;
      const first = stage.substages?.[0];
      return first
        ? researchDraftNodeAction(first) || researchDraftNodeTitle(first)
        : "";
    })
    .filter(Boolean)
    .slice(0, 4);

  const renderLegacyDraftStartConfirmation = () => (
    <section
      className="mx-auto grid w-full max-w-2xl gap-5 py-10"
      data-testid="workbench-draft-start-confirmation"
    >
      <div className="grid gap-2">
        <Typography.Title level={4} className="m-0!">
          {isPlanRevisionRequired ? "研究方案需要调整" : "研究方案已准备好"}
        </Typography.Title>
        <Typography.Text type="secondary">
          {isPlanRevisionRequired
            ? "请修改需要调整的内容。"
            : isTemplatePlanningFallback
              ? "当前方案根据已知信息整理。"
              : "请确认研究目标与执行方向。"}
        </Typography.Text>
      </div>
      <div className="grid gap-2 rounded-lg border border-border bg-surface px-4 py-3">
        <Typography.Text type="secondary" className="text-xs">
          研究目标
        </Typography.Text>
        <Typography.Text>{m.goalSummary || displayTaskTitle}</Typography.Text>
        {draftPlanStageSummaries.length ? (
          <>
            <Typography.Text type="secondary" className="mt-2 text-xs">
              计划开展
            </Typography.Text>
            <ol className="m-0 grid gap-1.5 pl-5 text-sm text-text-secondary">
              {draftPlanStageSummaries.map((summary, index) => (
                <li key={`${index}-${summary}`} className="leading-relaxed">
                  {summary}
                </li>
              ))}
            </ol>
          </>
        ) : null}
      </div>
    </section>
  );

  const renderCanonicalResearchDraft = () => {
    const plan = m.creationPlan;
    if (!plan) return null;
    const overview = researchDesignOverview(plan);

    return (
      <section
        className="grid w-full gap-8 py-4"
        data-testid="workbench-draft-start-confirmation"
        data-planning-source={plan.planningSource ?? ""}
      >
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-4">
          <div className="grid gap-1">
            <Typography.Text type="secondary" className="text-xs">
              研究草案
            </Typography.Text>
            <Typography.Title level={3} className="m-0!">
              研究方案
            </Typography.Title>
            {isPlanRevisionRequired ? (
              <Typography.Text type="secondary">
                当前方案需要调整后再确认。
              </Typography.Text>
            ) : isTemplatePlanningFallback ? (
              <Typography.Text type="secondary">
                请检查方案是否符合你的研究目标。
              </Typography.Text>
            ) : null}
          </div>
          {researchDesignEditMode ? (
            <div
              className="flex flex-wrap justify-end gap-2"
              data-testid="workbench-draft-primary-actions"
            >
              <Button
                type="primary"
                loading={planSaving}
                disabled={loadSource !== "api" || researchDesignAgentSubmitting}
                onClick={() => void completeResearchDesignEdit()}
                data-testid="workbench-draft-complete-edit"
              >
                完成编辑
              </Button>
            </div>
          ) : isConfirmedCanonicalResearchDesign ? (
            <div
              className="flex flex-wrap items-center justify-end gap-2"
              data-testid="workbench-canonical-confirmed-actions"
            >
              <Typography.Text type="secondary">
                版本 {m.researchDesign?.version ?? "-"}
              </Typography.Text>
              <Tag className="m-0">当前</Tag>
              <Tag color="success" className="m-0">
                已确认
              </Tag>
            </div>
          ) : isCanonicalResearchDraft ? (
            <div
              className="flex flex-wrap justify-end gap-2"
              data-testid="workbench-draft-primary-actions"
            >
              <Button
                disabled={planSaving || loadSource !== "api"}
                onClick={beginResearchDesignEdit}
              >
                修改研究方案
              </Button>
              <Button
                type="primary"
                loading={runAction === "advance"}
                disabled={
                  !canConfirmDraftStart ||
                  loadSource !== "api" ||
                  Boolean(runAction)
                }
                onClick={() => void onConfirmDraftStart()}
              >
                确认并开始
              </Button>
            </div>
          ) : null}
        </div>

        {researchDesignEditMode && researchDesignEditError ? (
          <Alert
            type={planConflict ? "warning" : "error"}
            showIcon
            message={researchDesignEditError}
            data-testid="workbench-research-design-edit-error"
          />
        ) : null}

        <section
          className="grid gap-3 border-b border-border pb-7"
          data-testid="workbench-research-design-goal"
        >
          <Typography.Title level={4} className="m-0!">
            研究目标
          </Typography.Title>
          {researchDesignEditMode && researchDesignEditDraft ? (
            <Input.TextArea
              value={researchDesignEditDraft.goalSummary}
              autoSize={{ minRows: 3, maxRows: 8 }}
              onChange={(event) => {
                setResearchDesignEditDraft((current) =>
                  current
                    ? { ...current, goalSummary: event.target.value }
                    : current,
                );
                setResearchDesignEditError(null);
              }}
              className="text-base! leading-7!"
              data-testid="workbench-research-design-edit-goal"
            />
          ) : (
            <Typography.Paragraph className="m-0 text-base leading-7 text-text">
              {plan.goalSummary || m.goalSummary || displayTaskTitle}
            </Typography.Paragraph>
          )}
        </section>

        {!researchDesignEditMode ? (
          <div
            className="grid gap-7 border-b border-border pb-7"
            data-testid="workbench-research-design-overview"
          >
            {overview.primaryResearchQuestions.length ? (
              <section
                className="grid gap-2"
                data-testid="workbench-research-design-primary-questions"
              >
                <Typography.Title level={4} className="m-0!">
                  主研究问题
                </Typography.Title>
                <ul className="m-0 grid gap-1.5 pl-5 text-sm text-text-secondary">
                  {overview.primaryResearchQuestions.map((question) => (
                    <li key={question} className="leading-relaxed">
                      {question}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            {overview.targetOutputs.length ? (
              <section
                className="grid gap-2"
                data-testid="workbench-research-design-target-outputs"
              >
                <Typography.Title level={4} className="m-0!">
                  关键目标产出
                </Typography.Title>
                <ul className="m-0 grid gap-1.5 pl-5 text-sm text-text-secondary">
                  {overview.targetOutputs.map((output) => (
                    <li key={output} className="leading-relaxed">
                      {output}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            {overview.evidenceBoundaries.length ? (
              <section
                className="grid gap-2"
                data-testid="workbench-research-design-evidence-boundaries"
              >
                <Typography.Title level={4} className="m-0!">
                  证据边界
                </Typography.Title>
                <ul className="m-0 grid gap-1.5 pl-5 text-sm text-text-secondary">
                  {overview.evidenceBoundaries.map((boundary) => (
                    <li key={boundary} className="leading-relaxed">
                      {boundary}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            {overview.researchPath.length ? (
              <section
                className="grid gap-2"
                data-testid="workbench-research-design-path"
              >
                <Typography.Title level={4} className="m-0!">
                  简洁研究路径
                </Typography.Title>
                <ol className="m-0 grid gap-1.5 pl-5 text-sm text-text-secondary">
                  {overview.researchPath.map((step) => (
                    <li key={step} className="leading-relaxed">
                      {step}
                    </li>
                  ))}
                </ol>
              </section>
            ) : null}

            {overview.auxiliaryResearchDimensions.length ? (
              <details
                className="text-sm text-text-secondary"
                data-testid="workbench-research-design-auxiliary-dimensions"
              >
                <summary className="cursor-pointer text-text">
                  辅助研究维度
                </summary>
                <ul className="mb-0 mt-2 grid gap-1.5 pl-5">
                  {overview.auxiliaryResearchDimensions.map((dimension) => (
                    <li key={dimension} className="leading-relaxed">
                      {dimension}
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </div>
        ) : null}

        <div
          className="grid gap-4"
          data-testid="workbench-research-design-stages"
        >
          {(researchDesignEditMode && researchDesignEditDraft
            ? TASK_STAGE_ORDER.map(
                (stageKey): TaskPlanStage =>
                  plan.stages.find((stage) => stage.stageKey === stageKey) ?? {
                    stageKey,
                    subStages: [],
                    substages: [],
                  },
              )
            : canonicalResearchDraftStages
          ).map((stage, stageIndex) => {
            const stageTitle = researchDraftStageTitle(stage);
            const removedInstanceIds = new Set(
              researchDesignEditDraft?.removedInstanceIds ?? [],
            );
            const existingWork = (stage.substages ?? [])
              .map((substage, substageIndex) => {
                if (
                  substage.instanceId &&
                  removedInstanceIds.has(substage.instanceId)
                )
                  return null;
                const title = researchDraftNodeTitle(substage);
                const action = researchDraftNodeAction(substage);
                if (!researchDesignEditMode && !title && !action) return null;
                if (researchDesignEditMode && !substage.instanceId) return null;
                return {
                  key:
                    substage.instanceId ??
                    `${stage.stageKey}-${substage.substage}`,
                  editKey: researchDesignEditSubstageKey(
                    stage,
                    substage,
                    substageIndex,
                  ),
                  label: title,
                  action: action && action !== title ? action : "",
                  instanceId: substage.instanceId ?? null,
                  addedLocalKey: null as string | null,
                };
              })
              .filter(
                (
                  item,
                ): item is {
                  key: string;
                  editKey: string;
                  label: string;
                  action: string;
                  instanceId: string | null;
                  addedLocalKey: string | null;
                } => Boolean(item),
              );
            const addedWork =
              researchDesignEditMode && researchDesignEditDraft
                ? researchDesignEditDraft.addedSteps
                    .filter((step) => step.stageKey === stage.stageKey)
                    .map((step) => ({
                      key: step.localKey,
                      editKey: step.localKey,
                      label: step.label,
                      action: "",
                      instanceId: null as string | null,
                      addedLocalKey: step.localKey,
                    }))
                : [];
            const availableWorkTypes = researchDesignEditDraft
              ? researchDesignAvailableSubStages(
                  researchDesignEditDraft,
                  stage.stageKey,
                  researchDesignCatalog.catalog,
                )
              : [];
            const researchWork = [...existingWork, ...addedWork];
            const stageDescription = researchDraftStageDescription(
              stage,
              researchWork.map((work) => work.action || work.label),
            );
            const canDeleteWork = Boolean(
              researchDesignEditMode &&
              researchDesignEditDraft &&
              researchDesignEditRemainingCount(researchDesignEditDraft) > 1,
            );

            return (
              <article
                key={stage.stageKey}
                id={`research-draft-stage-${stage.stageKey}`}
                className="grid gap-5 border-t border-border pt-7 first:border-t-0 first:pt-0"
                data-testid={`workbench-research-design-stage-${stageIndex}`}
              >
                <div className="grid gap-1.5">
                  <Typography.Text
                    type="secondary"
                    className="text-xs font-medium"
                  >
                    阶段 {stageIndex + 1}
                  </Typography.Text>
                  {stageTitle ? (
                    <Typography.Title level={3} className="m-0! text-xl!">
                      {stageTitle}
                    </Typography.Title>
                  ) : null}
                </div>

                {stageDescription ? (
                  <div
                    className="grid gap-1.5"
                    data-testid={`workbench-research-design-stage-description-${stageIndex}`}
                  >
                    <Typography.Text strong className="text-sm">
                      阶段说明
                    </Typography.Text>
                    <Typography.Paragraph className="m-0 leading-relaxed text-text-secondary">
                      {stageDescription}
                    </Typography.Paragraph>
                  </div>
                ) : null}

                {researchWork.length || researchDesignEditMode ? (
                  <div
                    className="grid gap-1.5"
                    data-testid={`workbench-research-design-stage-work-${stageIndex}`}
                  >
                    <Typography.Text strong className="text-sm">
                      研究工作
                    </Typography.Text>
                    <ol className="m-0 grid gap-3 pl-5 text-sm text-text-secondary">
                      {researchWork.map((work, workIndex) => (
                        <li
                          key={work.key}
                          id={`research-draft-substage-${work.key}`}
                          className="leading-relaxed"
                        >
                          {researchDesignEditMode && researchDesignEditDraft ? (
                            <div className="grid gap-1.5">
                              <div className="flex items-center justify-between gap-2">
                                <Typography.Text className="text-xs">
                                  {work.label || "研究工作"}
                                </Typography.Text>
                                <Button
                                  type="link"
                                  size="small"
                                  className="h-auto p-0!"
                                  disabled={!canDeleteWork}
                                  onClick={() => {
                                    setResearchDesignEditDraft((current) => {
                                      if (!current) return current;
                                      if (
                                        researchDesignEditRemainingCount(
                                          current,
                                        ) <= 1
                                      ) {
                                        setResearchDesignEditError(
                                          "至少保留一个研究工作。",
                                        );
                                        return current;
                                      }
                                      if (work.addedLocalKey) {
                                        return {
                                          ...current,
                                          addedSteps: current.addedSteps.filter(
                                            (step) =>
                                              step.localKey !==
                                              work.addedLocalKey,
                                          ),
                                        };
                                      }
                                      if (!work.instanceId) return current;
                                      return {
                                        ...current,
                                        removedInstanceIds:
                                          current.removedInstanceIds.includes(
                                            work.instanceId,
                                          )
                                            ? current.removedInstanceIds
                                            : [
                                                ...current.removedInstanceIds,
                                                work.instanceId,
                                              ],
                                      };
                                    });
                                    setResearchDesignEditError(null);
                                  }}
                                  data-testid={`workbench-research-design-edit-delete-work-${stageIndex}-${workIndex}`}
                                >
                                  删除
                                </Button>
                              </div>
                              {work.addedLocalKey ? (
                                <ResearchDesignWorkTypeSelect
                                  value={
                                    researchDesignEditDraft.addedSteps.find(
                                      (step) =>
                                        step.localKey === work.addedLocalKey,
                                    )?.substage || undefined
                                  }
                                  options={researchDesignAvailableSubStages(
                                    researchDesignEditDraft,
                                    stage.stageKey,
                                    researchDesignCatalog.catalog,
                                    work.addedLocalKey,
                                  )}
                                  onSelect={(entry) => {
                                    setResearchDesignEditDraft((current) =>
                                      current
                                        ? {
                                            ...current,
                                            addedSteps: current.addedSteps.map(
                                              (step) =>
                                                step.localKey ===
                                                work.addedLocalKey
                                                  ? {
                                                      ...step,
                                                      substage: entry.key,
                                                      label: entry.label,
                                                    }
                                                  : step,
                                            ),
                                          }
                                        : current,
                                    );
                                    setResearchDesignEditError(null);
                                  }}
                                  testId={`workbench-research-design-edit-type-${stageIndex}-${workIndex}`}
                                />
                              ) : null}
                              <Input.TextArea
                                placeholder={
                                  work.addedLocalKey
                                    ? "填写这个子目标的具体工作说明"
                                    : undefined
                                }
                                value={
                                  work.addedLocalKey
                                    ? (researchDesignEditDraft.addedSteps.find(
                                        (step) =>
                                          step.localKey === work.addedLocalKey,
                                      )?.objective ?? "")
                                    : (researchDesignEditDraft.objectives[
                                        work.editKey
                                      ] ?? "")
                                }
                                autoSize={{ minRows: 2, maxRows: 7 }}
                                onChange={(event) => {
                                  const value = event.target.value;
                                  setResearchDesignEditDraft((current) => {
                                    if (!current) return current;
                                    if (work.addedLocalKey) {
                                      return {
                                        ...current,
                                        addedSteps: current.addedSteps.map(
                                          (step) =>
                                            step.localKey === work.addedLocalKey
                                              ? { ...step, objective: value }
                                              : step,
                                        ),
                                      };
                                    }
                                    return {
                                      ...current,
                                      objectives: {
                                        ...current.objectives,
                                        [work.editKey]: value,
                                      },
                                    };
                                  });
                                  setResearchDesignEditError(null);
                                }}
                                data-testid={`workbench-research-design-edit-work-${stageIndex}-${workIndex}`}
                              />
                            </div>
                          ) : (
                            <>
                              <span className="text-text">{work.label}</span>
                              {work.action ? (
                                <span>：{work.action}</span>
                              ) : null}
                            </>
                          )}
                        </li>
                      ))}
                    </ol>
                    {researchDesignEditMode ? (
                      <>
                        <Button
                          type="dashed"
                          size="small"
                          className="justify-self-start"
                          disabled={
                            researchDesignCatalog.loading ||
                            Boolean(researchDesignCatalog.error) ||
                            !availableWorkTypes.length
                          }
                          onClick={() => {
                            setResearchDesignEditDraft((current) =>
                              current
                                ? {
                                    ...current,
                                    nextAddedStepSeq:
                                      current.nextAddedStepSeq + 1,
                                    addedSteps: [
                                      ...current.addedSteps,
                                      {
                                        localKey: `new-step-${current.nextAddedStepSeq}`,
                                        stageKey: stage.stageKey,
                                        substage: "",
                                        label: "",
                                        objective: "",
                                      },
                                    ],
                                  }
                                : current,
                            );
                            setResearchDesignEditError(null);
                          }}
                          data-testid={`workbench-research-design-edit-add-step-${stageIndex}`}
                        >
                          新增研究工作
                        </Button>
                        <Typography.Text type="secondary" className="text-xs">
                          {researchDesignCatalog.loading
                            ? "正在读取工作类型…"
                            : researchDesignCatalog.error ||
                              (!availableWorkTypes.length
                                ? "当前阶段没有可新增的工作类型"
                                : "选择工作类型，再填写具体说明。")}
                        </Typography.Text>
                      </>
                    ) : null}
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>
      </section>
    );
  };

  const renderResearchDesignEditCollaboration = () => {
    const manualDraftDirty = Boolean(
      researchDesignEditDraft &&
      researchDesignEditDraftDirty(researchDesignEditDraft),
    );
    const selectedHistoryEntry =
      m.researchDesignHistory.find(
        (entry) => entry.version === selectedResearchDesignHistoryVersion,
      ) ??
      m.researchDesignHistory[0] ??
      null;
    const selectedHistoryPrevious = selectedHistoryEntry
      ? ([...m.researchDesignHistory]
          .filter((entry) => entry.version < selectedHistoryEntry.version)
          .sort((left, right) => right.version - left.version)[0] ?? null)
      : null;
    const selectedHistoryChanges =
      selectedHistoryEntry && m.creationPlan
        ? researchDesignSemanticDiff(
            selectedHistoryPrevious,
            selectedHistoryEntry,
            m.creationPlan,
          )
        : [];
    return (
      <div
        className="flex min-h-0 flex-1 flex-col"
        data-testid="workbench-research-design-edit-collaboration"
      >
        <div className="border-b border-border px-4 py-4">
          <Typography.Title level={5} className="m-0!">
            研究协作
          </Typography.Title>
          <Segmented
            block
            className="mt-3"
            value={researchDesignEditPanel}
            onChange={(value) =>
              setResearchDesignEditPanel(value as "conversation" | "history")
            }
            options={[
              { label: "对话", value: "conversation" },
              { label: "修改记录", value: "history" },
            ]}
            data-testid="workbench-research-design-edit-tabs"
          />
        </div>

        {researchDesignEditPanel === "conversation" ? (
          <>
            <div
              className="min-h-0 flex-1 overflow-y-auto px-4 py-4"
              data-testid="workbench-research-design-agent-conversation"
            >
              {researchDesignEditConversation.length ? (
                <div className="grid gap-3">
                  {researchDesignEditConversation.map((item) => (
                    <div
                      key={item.id}
                      className={cn(
                        "grid gap-1 rounded-lg px-3 py-2 text-sm leading-relaxed",
                        item.role === "user"
                          ? "bg-accent-soft"
                          : "bg-surface-soft",
                      )}
                      data-role={item.role}
                      data-state={item.state ?? ""}
                    >
                      <Typography.Text type="secondary" className="text-xs">
                        {item.role === "user" ? "你" : "Agent"}
                      </Typography.Text>
                      <Typography.Text>{item.text}</Typography.Text>
                    </div>
                  ))}
                </div>
              ) : (
                <Typography.Paragraph
                  type="secondary"
                  className="m-0 text-sm leading-relaxed"
                >
                  告诉 Agent 你想如何调整当前研究方案。Agent
                  修改成功后，中心会重新读取最新方案。
                </Typography.Paragraph>
              )}
            </div>
            <div className="grid gap-2 border-t border-border p-4">
              {manualDraftDirty ? (
                <Typography.Text type="warning" className="text-xs">
                  你还有未保存的手动修改。完成编辑后再让 Agent
                  基于最新版本调整。
                </Typography.Text>
              ) : null}
              <Input.TextArea
                value={researchDesignAgentInstruction}
                onChange={(event) =>
                  setResearchDesignAgentInstruction(event.target.value)
                }
                onPressEnter={(event) => {
                  if (event.shiftKey) return;
                  event.preventDefault();
                  void submitResearchDesignAgentInstruction();
                }}
                autoSize={{ minRows: 3, maxRows: 7 }}
                placeholder="告诉 Agent 你想如何修改方案"
                disabled={researchDesignAgentSubmitting}
                data-testid="workbench-research-design-agent-instruction"
              />
              <Button
                type="primary"
                loading={researchDesignAgentSubmitting}
                disabled={
                  !researchDesignAgentInstruction.trim() ||
                  manualDraftDirty ||
                  loadSource !== "api"
                }
                onClick={() => void submitResearchDesignAgentInstruction()}
                data-testid="workbench-research-design-agent-submit"
              >
                发送修改要求
              </Button>
              <Typography.Text type="secondary" className="text-xs">
                当前对话只显示本次编辑会话；方案修改以服务端上的最新研究方案版本为准。
              </Typography.Text>
            </div>
          </>
        ) : (
          <div
            className="min-h-0 flex-1 overflow-y-auto px-4 py-4"
            data-testid="workbench-research-design-history"
          >
            {m.researchDesignHistory.length ? (
              <div className="grid gap-5">
                <div
                  className="grid gap-1"
                  data-testid="workbench-research-design-history-list"
                >
                  {m.researchDesignHistory.map((entry) => {
                    const selected =
                      entry.version === selectedHistoryEntry?.version;
                    return (
                      <button
                        key={`${entry.version}-${entry.createdAt}`}
                        type="button"
                        className={cn(
                          "grid w-full gap-1 border-b border-border px-2 py-3 text-left last:border-b-0",
                          selected
                            ? "bg-surface-soft"
                            : "hover:bg-surface-soft",
                        )}
                        onClick={() =>
                          setSelectedResearchDesignHistoryVersion(entry.version)
                        }
                        data-testid={`workbench-research-design-history-version-${entry.version}`}
                      >
                        <span className="flex items-center justify-between gap-2">
                          <Typography.Text strong>
                            版本 {entry.version}
                          </Typography.Text>
                          {entry.version === m.researchDesign?.version ? (
                            <Tag className="m-0">当前</Tag>
                          ) : null}
                        </span>
                        {researchDesignHistoryTime(entry.createdAt) ? (
                          <Typography.Text type="secondary" className="text-xs">
                            {researchDesignHistoryTime(entry.createdAt)}
                          </Typography.Text>
                        ) : null}
                      </button>
                    );
                  })}
                </div>

                {selectedHistoryEntry ? (
                  <section
                    className="grid gap-3 border-t border-border pt-4"
                    data-testid="workbench-research-design-history-detail"
                  >
                    <div className="grid gap-1">
                      <Typography.Text strong>
                        版本 {selectedHistoryEntry.version} 的变化
                      </Typography.Text>
                      <Typography.Text type="secondary" className="text-xs">
                        {selectedHistoryPrevious
                          ? `版本 ${selectedHistoryPrevious.version} → 版本 ${selectedHistoryEntry.version}`
                          : "这是最初版本，没有更早版本可比较。"}
                      </Typography.Text>
                    </div>

                    {selectedHistoryPrevious ? (
                      selectedHistoryChanges.length ? (
                        <div
                          className="grid gap-3"
                          data-testid="workbench-research-design-semantic-diff"
                        >
                          {selectedHistoryChanges.map((change) => (
                            <div
                              key={change.key}
                              className="grid gap-1.5 rounded-lg border border-border p-3"
                            >
                              <Typography.Text strong className="text-sm">
                                {change.label}
                              </Typography.Text>
                              <Typography.Text
                                type="secondary"
                                className="text-xs"
                              >
                                修改前
                              </Typography.Text>
                              <Typography.Paragraph className="m-0 text-sm leading-relaxed text-text-secondary">
                                {change.before}
                              </Typography.Paragraph>
                              <Typography.Text
                                type="secondary"
                                className="text-xs"
                              >
                                修改后
                              </Typography.Text>
                              <Typography.Paragraph className="m-0 text-sm leading-relaxed text-text">
                                {change.after}
                              </Typography.Paragraph>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <Typography.Text type="secondary" className="text-sm">
                          这一版本没有改变当前允许编辑的研究目标或研究工作内容。
                        </Typography.Text>
                      )
                    ) : null}

                    {selectedHistoryEntry.version !==
                    m.researchDesign?.version ? (
                      <div className="grid gap-2 border-t border-border pt-3">
                        {manualDraftDirty ? (
                          <Typography.Text type="warning" className="text-xs">
                            你还有未保存的手动修改。完成编辑后才能撤销历史版本。
                          </Typography.Text>
                        ) : null}
                        <Button
                          loading={
                            researchDesignUndoingVersion ===
                            selectedHistoryEntry.version
                          }
                          disabled={
                            manualDraftDirty ||
                            researchDesignUndoingVersion !== null ||
                            loadSource !== "api"
                          }
                          onClick={() =>
                            void restoreResearchDesignVersion(
                              selectedHistoryEntry,
                            )
                          }
                          data-testid="workbench-research-design-restore"
                        >
                          撤销到此版本
                        </Button>
                        <Typography.Text
                          type="secondary"
                          className="text-xs leading-relaxed"
                        >
                          撤销不会删除历史记录；成功后会保存成一个新的研究方案版本。
                        </Typography.Text>
                      </div>
                    ) : null}
                  </section>
                ) : null}
              </div>
            ) : (
              <Typography.Text type="secondary">
                当前没有可展示的真实版本记录。
              </Typography.Text>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="relative w-full h-full px-4 pb-4">
      <div className="flex h-full min-h-0 w-full flex-col overflow-hidden rounded-xl bg-surface">
        {m.creationPlan && !isCanonicalResearchDesignVisible ? (
          <DraftPlanEditorModal
            open={planEditorOpen}
            revision={m.revision}
            plan={m.creationPlan}
            saving={planSaving}
            conflict={planConflict}
            onCancel={() => {
              setPlanConflict(false);
              setPlanEditorOpen(false);
            }}
            onReloadDraft={reloadDraftPlan}
            onSave={(update) => void saveDraftPlan(update)}
          />
        ) : null}
        {planningSessionId &&
        (isPreExecutionPlan ||
          planningOccupiesShell ||
          retainPlanningFailureContext) ? (
          <ResearchStartPlanningSync
            sessionId={planningSessionId}
            taskId={m.taskId}
            onCompleted={
              planningOccupiesShell ? handlePlanningTaskRecheck : load.reload
            }
            onStatusChange={handlePlanningStatusChange}
            refreshTaskWhileMissing={
              planningOccupiesShell && load.status !== "ready"
            }
            recheckSignal={planningRecheckSignal}
          />
        ) : null}
        {!headerCollapsed ? (
          <header className="flex shrink-0 flex-col gap-1 border-b border-border-strong max-[760px]:px-3 px-3 pt-3">
            {m.resultSource === "replay" ? <ReplayResultDisclosure /> : null}
            {loadSource === "fixture" ? (
              <FixtureDisclosureAlert reason="local_fixture" />
            ) : null}
            <div
              className="flex w-full flex-col items-start justify-center gap-0.5"
              data-testid="workbench-task-context"
            >
              <div className="flex w-full items-start justify-between gap-3">
                <Typography.Title
                  level={3}
                  className="m-0 mb-1! max-w-full text-lg! font-semibold leading-title text-text"
                  ellipsis={{ tooltip: displayTaskTitle }}
                >
                  {displayTaskTitle}
                </Typography.Title>
                {!isPreExecutionPlan && !planNotYetFormed ? (
                  <div className="flex shrink-0 flex-row flex-nowrap items-center justify-end gap-3.5 max-[760px]:w-full max-[760px]:flex-wrap max-[760px]:justify-start max-[760px]:gap-2">
                    <div className="inline-flex items-center gap-1.5 whitespace-nowrap">
                      <span className="text-sm text-text-muted">阶段</span>
                      <Tag color="processing" className="m-0">
                        {currentStageRow?.pagePhaseLabel ??
                          PAGE_PHASE_LABEL[vp]}
                      </Tag>
                    </div>
                    <span
                      className="h-5 w-px shrink-0 bg-border-strong max-[760px]:hidden"
                      aria-hidden
                    />
                    <div
                      className="inline-flex items-center gap-1.5 whitespace-nowrap"
                      data-testid="workbench-top-status"
                      data-revision={screenView.revision}
                    >
                      <span className="text-sm text-text-muted">状态</span>
                      <Tag
                        color={
                          interruptionView
                            ? interruptionView.tone === "error"
                              ? "red"
                              : interruptionView.tone === "info"
                                ? "default"
                                : "warning"
                            : decisionState.statusColor
                        }
                        className="m-0"
                        data-execution-status={
                          screenView.executionDisplayStatus
                        }
                        data-interruption-kind={interruptionView?.kind ?? ""}
                      >
                        {interruptionView
                          ? interruptionView.statusLabel
                          : screenView.isLiveResearch ||
                              screenView.progressTrust.kind === "invalid"
                            ? screenView.executionStatusLabel
                            : decisionState.statusLabel}
                      </Tag>
                    </div>
                    <ResultSourceTag source={m.resultSource} />
                    <Tooltip title="导出将在产物稳定后开放">
                      <Button size="small" disabled>
                        导出
                      </Button>
                    </Tooltip>
                  </div>
                ) : null}
              </div>

              <WorkbenchOriginalInput
                taskId={m.taskId}
                sessionId={planningSessionId}
                context={visibleStartContext}
              />
            </div>
            {isMobileWorkbench ? (
              <div className="grid gap-1.5" data-testid="workbench-mobile-tabs">
                <Segmented<MobileWorkbenchPane>
                  block
                  size="small"
                  value={mobilePane}
                  options={[
                    {
                      label: "研究阶段",
                      value: "stage",
                    },
                    {
                      label: "当前产物",
                      value: "artifact",
                    },
                    {
                      label: "协作区",
                      value: "support",
                    },
                  ]}
                  onChange={setMobilePane}
                />
              </div>
            ) : null}
          </header>
        ) : null}
        <div className="flex min-h-0 flex-1 flex-row overflow-hidden">
          {showLeftPanel ? (
            <WorkbenchResearchNavigation
              collapsed={renderLeftCollapsed}
              mobile={isMobileWorkbench}
              width={!leftPanelCollapsed ? leftPanelWidth : undefined}
              revision={screenView.revision}
              executionStatus={screenView.executionDisplayStatus}
              planAvailable={Boolean(m.confirmedResearchDesign)}
              planSelected={viewingConfirmedPlan}
              onViewPlan={() => {
                const body = document.querySelector<HTMLElement>(".workbench-center-scroll");
                if (body) body.scrollTop = 0;
                const next = new URLSearchParams(searchParams);
                next.set("view", "research-plan");
                setSearchParams(next);
                if (isMobileWorkbench) setMobilePane("artifact");
              }}
              flow={{
                      language: "zh-CN",
                      phaseAgg: phaseAgg ?? {
                          problem: "not_started",
                          method: "not_started",
                          experiment: "not_started",
                          report: "not_started",
                        },
                      stagesByPhase,
                      progressSteps,
                      viewingPhase: vp,
                      currentPagePhase,
                      currentPagePhases,
                      currentSubStageIds: authoritativeCurrentSubstageIds,
                      selectedSubStage,
                      onSelectPhase,
                      onSelectSubStage,
              }}
            />
          ) : null}

          {!isMobileWorkbench && !leftPanelCollapsed ? (
            <div
              className="relative z-resize w-1.5 shrink-0 cursor-col-resize bg-surface-muted transition-colors after:absolute after:top-1/2 after:left-1/2 after:h-7 after:w-0.5 after:-translate-x-1/2 after:-translate-y-1/2 after:rounded-sm after:bg-text-secondary after:opacity-40 after:transition-all hover:after:h-12 hover:after:bg-accent-strong hover:after:opacity-100 active:after:h-12 active:after:bg-accent-strong active:after:opacity-100"
              role="separator"
              aria-orientation="vertical"
              aria-label="调整研究阶段栏宽度"
              onMouseDown={startPanelDrag("left")}
              data-testid="left-panel-resizer"
            />
          ) : null}

          {showCenterPanel ? (
            <main
              className={cn(
                "flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden",
                isCanonicalResearchDraft ? "bg-surface" : "bg-surface p-2.5",
                isMobileWorkbench && "w-full",
              )}
              data-testid="workbench-center"
              data-revision={screenView.revision}
              data-execution-status={screenView.executionDisplayStatus}
            >
              <div
                className="workbench-center-scroll min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain"
                data-testid="workbench-center-scroll-body"
              >
                <div
                  className={cn(
                    "mx-auto flex min-h-full w-full flex-col",
                    isCanonicalResearchDraft
                      ? "px-8 py-6 max-[760px]:px-4 max-[760px]:py-4"
                      : "px-4 py-3 max-[760px]:px-1",
                  )}
                >
                  {planNotYetFormed ? null : (
                    <ResearchInterruptionPanel
                      model={m}
                      artifacts={taskArtifacts}
                    />
                  )}
                  {showPrimaryDelivery && !viewingConfirmedPlan ? (
                    <WorkbenchPrimaryResearchDelivery
                      model={m}
                      nodeProducts={currentAcceptedWorkflowNodeProducts}
                    />
                  ) : null}

                  {isConfirmedCanonicalResearchDesign &&
                  isNotStartedConfirmed &&
                  activeTaskStartStatus?.state === "blocked" &&
                  activeTaskStartStatus.reasonSummary ? (
                    <Alert
                      className="mb-4"
                      type="warning"
                      showIcon
                      title={activeTaskStartStatus.reasonSummary}
                      data-testid="workbench-confirmed-start-blocker"
                    />
                  ) : null}

                  {viewingConfirmedPlan && m.confirmedResearchDesign ? (
                    <ConfirmedResearchPlan design={m.confirmedResearchDesign} />
                  ) : isCanonicalResearchDraft &&
                  !m.finalResult &&
                  currentAcceptedWorkflowNodeProducts.length === 0 ? (
                    renderCanonicalResearchDraft()
                  ) : supportingFocusedArtifact ? (
                    <WorkbenchSupportingArtifact
                      artifact={supportingFocusedArtifact}
                      taskId={m.taskId}
                      model={m}
                      apiConnected={loadSource === "api"}
                      editable={isFocusedArtifactEditable}
                      loading={
                        loadingArtifactIds.has(supportingFocusedArtifact.id) &&
                        !supportingFocusedArtifact.content
                      }
                      unavailableReason={getArtifactFailureReason(
                        supportingFocusedArtifact.id,
                      )}
                      showHeading={Boolean(
                        showPrimaryDelivery &&
                        (m.finalResult ||
                          currentAcceptedWorkflowNodeProducts.length > 0),
                      )}
                      onOpenVersions={() => setVersionDrawerOpen(true)}
                      onRetry={() =>
                        retryArtifact(m.taskId, supportingFocusedArtifact.id)
                      }
                    />
                  ) : isPlanEditableDraft ? (
                    renderLegacyDraftStartConfirmation()
                  ) : isNotStartedConfirmed &&
                    activeTaskStartStatus?.state !== "blocked" ? (
                    <div
                      className="mx-auto flex w-full max-w-2xl py-10"
                      data-testid="workbench-confirmed-task-start-panel"
                    >
                      <Typography.Text type="secondary">
                        {activeTaskStartStatus
                          ? activeTaskStartStatus.startAllowed
                            ? "研究已准备好；请在右侧操作区开始研究。"
                            : (activeTaskStartStatus.reasonSummary ??
                              taskStartStatusMessage(
                                activeTaskStartStatus.state,
                                activeTaskStartStatus.blockingReasonCode,
                              ))
                          : START_STATUS_CHECKING_MESSAGE}
                      </Typography.Text>
                    </div>
                  ) : (
                    <div
                      className="flex flex-1 items-center justify-center py-10"
                      data-testid="workbench-artifact-empty"
                    >
                      <div className="grid gap-2 text-center">
                        <Typography.Text type="secondary">
                          {taskArtifacts.length
                            ? "当前所选步骤还没有可查看的产物；已有成果仍可打开。"
                            : planNotYetFormed
                              ? "暂无产物"
                              : "当前尚无可查看的研究产物。"}
                        </Typography.Text>
                      </div>
                    </div>
                  )}
                </div>
              </div>
              {!viewingConfirmedPlan && selectedSubRow && artifactScope === "substage" ? (
                <SubstageProgressPanel
                  taskId={m.taskId}
                  workUnitIds={selectedSubRow.workUnitIds}
                  running={["ready", "queued", "running"].includes(
                    selectedSubRow.projectionStatus ?? "",
                  )}
                />
              ) : null}
              {!viewingConfirmedPlan && selectedSubRow && artifactScope === "substage" ? (
                <SubstageArtifactFooter
                  taskId={m.taskId}
                  substageInstanceId={selectedSubRow.id}
                  artifactRefs={selectedSubRow.artifactRefs}
                  artifacts={subArtifacts}
                  workUnits={m.workUnits}
                  selection={{
                    taskId: m.taskId,
                    substageInstanceId: selectedSubRow.id,
                    artifactId:
                      focusedArtifact &&
                      subArtifacts.some(
                        (artifact) => artifact.id === focusedArtifact.id,
                      )
                        ? focusedArtifact.id
                        : null,
                  }}
                  deepLinkedArtifactId={artifactParam}
                  artifactHref={(artifactId) =>
                    buildSubstageArtifactDeepLink({
                      pathname: window.location.pathname,
                      search: searchParams.toString(),
                      artifactId,
                    })
                  }
                  onSelectArtifact={(artifactId) =>
                    selectArtifact(artifactId, "substage", "pinned")
                  }
                />
              ) : null}
            </main>
          ) : null}

          {!isMobileWorkbench && !rightPanelCollapsed ? (
            <div
              className="relative z-resize w-1.5 shrink-0 cursor-col-resize bg-surface-muted transition-colors after:absolute after:top-1/2 after:left-1/2 after:h-7 after:w-0.5 after:-translate-x-1/2 after:-translate-y-1/2 after:rounded-sm after:bg-text-secondary after:opacity-40 after:transition-all hover:after:h-12 hover:after:bg-accent-strong hover:after:opacity-100 active:after:h-12 active:after:bg-accent-strong active:after:opacity-100"
              role="separator"
              aria-orientation="vertical"
              aria-label={`调整${rightPanelName}宽度`}
              onMouseDown={startPanelDrag("right")}
              data-testid="right-panel-resizer"
            />
          ) : null}

          {showRightPanel ? (
            <aside
              className={cn(
                "flex min-h-0 flex-col overflow-hidden bg-surface p-0",
                renderRightCollapsed ? "w-0 shrink-0" : "w-[280px] shrink-0",
                isMobileWorkbench && "w-full flex-1 shrink",
              )}
              data-testid="workbench-right-panel"
              data-revision={screenView.revision}
              data-execution-status={screenView.executionDisplayStatus}
              style={
                !isMobileWorkbench && !rightPanelCollapsed
                  ? { width: rightPanelWidth, flex: "0 0 auto" }
                  : undefined
              }
            >
              {renderRightCollapsed ? (
                <div
                  className="flex h-full w-full items-center justify-center py-3 text-xs tracking-sidebar text-text-muted [writing-mode:vertical-rl]"
                  data-testid="right-panel-collapsed"
                >
                  {rightPanelName}
                </div>
              ) : researchDesignEditMode && isCanonicalResearchDraft ? (
                renderResearchDesignEditCollaboration()
              ) : (
                <WorkbenchInteractionPanel
                  taskId={m.taskId}
                  model={m}
                  artifacts={taskArtifacts}
                  screen={screenView}
                  collaboration={collaborationState.projection}
                  onCollaborationRefresh={collaborationState.refresh}
                  executionPolicy={executionPolicy}
                  executionPolicyUnavailable={executionPolicyUnavailable}
                  executionModeSaving={modeSaving}
                  showExecutionControls={
                    !isCanonicalResearchDraft && !planNotYetFormed
                  }
                  onExecutionModeChange={(mode) =>
                    void onExecutionModeChange(mode)
                  }
                  supplementNote={supplementNote}
                  onSupplementNoteChange={onSupplementNoteChange}
                  controllerPendingMessage={visibleControllerPendingMessage}
                  controllerReceipt={visibleControllerReceipt}
                  onSubmitControllerSupplement={onSubmitControllerSupplement}
                  controllerSubmitting={
                    userActions.controller.busy || collaborationSubmitting
                  }
                  onTaskReload={reloadTaskFromResearchActivity}
                  apiConnected={load.status === "ready" && loadSource === "api"}
                  onSelectArtifact={onSelectArtifactChip}
                  continuingResearch={runAction === "advance"}
                  onContinueResearch={
                    isCanonicalResearchDraft
                      ? undefined
                      : () => void onContinueCanonicalResearch()
                  }
                  canStopExecution={
                    !isCanonicalResearchDraft &&
                    hasWorkbenchTaskAction(
                      userActions.snapshot,
                      m.taskId,
                      m.revision,
                      "stop",
                    ) &&
                    loadSource === "api" &&
                    !actionUnavailableReason
                  }
                  stoppingExecution={runAction === "stop"}
                  onStopExecution={() => void runTaskAction()}
                  userActions={userActions}
                  recoveryProjection={recoveryProjection}
                  recoveryVisible={hasPendingRecoveryCache}
                  onRecoveryReconcile={() => void reconcileRecovery()}
                  planningAwaitingConfirmation={isPlanConfirmationPending}
                  planningRevisionRequired={isPlanRevisionRequired}
                  planningConfirming={runAction === "advance"}
                  planningConfirmDisabled={
                    !canConfirmDraftStart ||
                    loadSource !== "api" ||
                    Boolean(runAction)
                  }
                  planningModifyDisabled={planSaving || loadSource !== "api"}
                  onConfirmPlanning={() => void onConfirmDraftStart()}
                  onModifyPlanning={() => {
                    if (isCanonicalResearchDraft) beginResearchDesignEdit();
                    else setPlanEditorOpen(true);
                  }}
                  canStartConfirmedResearch={
                    isNotStartedConfirmed &&
                    activeTaskStartStatus?.startAllowed === true &&
                    !durableProjection.run &&
                    loadSource === "api" &&
                    !runAction
                  }
                  startingConfirmedResearch={runAction === "start"}
                  onStartConfirmedResearch={() => void onStartConfirmedTask()}
                  planningStatus={
                    planningStatusActive
                      ? (visiblePlanningStatus ??
                        (planGenerationFailed
                          ? {
                              sessionId: planningSessionId ?? m.taskId,
                              status: "failed",
                            }
                          : null))
                      : null
                  }
                  planningStatusReadUnavailable={
                    planningStatusActive && visiblePlanningStatusReadUnavailable
                  }
                  planningStatusActive={planningStatusActive}
                  showPvdFeedback={(m.skillPack ?? "").toLowerCase() === "pvd"}
                  workbenchUserState={workbenchUserState}
                  onRetryPlanning={
                    planningStatusActive
                      ? () => void handleRetryPlanning()
                      : undefined
                  }
                  planningRetrying={planningRetrying}
                  onRecheckPlanningSession={handleRecheckPlanningSession}
                />
              )}
            </aside>
          ) : null}
        </div>
        <ArtifactVersionDrawer
          open={versionDrawerOpen}
          artifactId={focusedArtifact?.id ?? null}
          artifactTitle={focusedArtifact?.title}
          taskId={m.taskId}
          editable={isFocusedArtifactEditable}
          canRegenerate={canRegenerateFocused}
          onClose={() => setVersionDrawerOpen(false)}
          onChanged={() => void load.reload()}
        />
      </div>
    </div>
  );
}
