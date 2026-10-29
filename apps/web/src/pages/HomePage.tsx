import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { App as AntdApp, Button, Mentions, Select, Typography } from "antd";
import type { TaskExecutionMode } from "@openair/shared";
import { ArrowRightOutlined } from "@ant-design/icons";
import {
  submitResearchStart,
  submitResearchStartStream,
  type ResearchStartAcceptedResponse,
} from "../api/client";
import { createClientRequestId, userFacingResearchStartError } from "../lib/researchStart";
import { projectResearchStartWaitingView } from "../lib/researchStartWaiting";
import { usePageHeader } from "../context/PageHeaderContext";
import { useUserNoticeOptional } from "../context/UserNoticeContext";
import {
  beginResearchStartPending,
  canReuseResearchStartClientRequest,
  clearResearchStartPending,
  readResearchStartPending,
  rememberResearchStartAccepted,
  rememberResearchStartPending,
  researchStartIntentFingerprint,
} from "../lib/userRouteState";
import {
  Atom,
  Beaker,
  BookOpen,
  Bot,
  Cpu,
  FlaskConical,
  GitBranch,
  Lightbulb,
  WandSparkles,
  type LucideIcon,
} from "lucide-react";
import {
  createResearchStartIntent,
  fetchResourceCatalog,
  promoteResearchStartIntent,
  RESOURCE_KIND_LABELS,
} from "../features/resource-plaza";
import type { ResourceKind, ResourcePlazaResource } from "../features/resource-plaza/types";

interface BoundCatalogResource {
  id: string;
  name: string;
  kind: ResourceKind;
}

type ResearchPath = "standard" | "evidence_analysis" | "pvd_parameter_recommendation" | "cs_reproduction" | "materials_discovery";

const BOUND_RESOURCE_ICONS = {
  agent: Bot,
  skill: WandSparkles,
  workflow: GitBranch,
} as const;

function BoundResourcePill({ resource }: { resource: BoundCatalogResource }) {
  const Icon = BOUND_RESOURCE_ICONS[resource.kind];
  const kindLabel = RESOURCE_KIND_LABELS[resource.kind];
  return (
    <div
      className="inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-full bg-info-soft py-1.5 pr-3 pl-2 text-brand"
      data-testid="starter-home-bound-resource"
      title={`${kindLabel}：${resource.name}`}
      aria-label={`${kindLabel} ${resource.name}`}
    >
      <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand text-surface">
        <Icon size={13} aria-hidden="true" />
      </span>
      <span className="truncate text-[13px] font-medium leading-none">{resource.name}</span>
    </div>
  );
}

function isResearchStartResponseUnknown(error: unknown): boolean {
  if (!error || typeof error !== "object") return true;
  const value = error as { code?: unknown; status?: unknown };
  if (value.code === "research_start_rejected") return false;
  if (typeof value.status === "number") return value.status >= 500;
  return true;
}

const HOME_RESEARCH_QUERY_SAMPLES = [
  "我在做 AlScN 薄膜，40% Sc 靶、Mo 衬底，下一轮最多做 10 组实验。Power、N₂、Ar 和沉积时间都能调，请你推荐比较合适的工艺窗口。",
  "LLM judge 会不会偏爱更长、更会解释的回答？我做评测时需要专门控制这个问题吗？",
  "我想复现一下 SimCSE 里 supervised BERT-base 的 STS 结果，尽量按作者公开的代码和设置跑一遍，看看能不能复现出来。",
] as const;

interface HomePromptSample {
  id: number;
  query: string;
}

const HOME_PROMPT_SAMPLES: readonly HomePromptSample[] = HOME_RESEARCH_QUERY_SAMPLES.map(
  (query, index) => ({ id: index, query }),
);

const HOME_AGENT_QUERY = {
  q: "",
  resourceType: "agent" as const,
  availability: "tryable" as const,
  sort: "relevance" as const,
  page: 1,
  pageSize: 100,
  more: false,
};

const HOME_RECOMMENDED_AGENT_IDS = [
  "agent:research_question_refinement",
  "agent:validation_design",
] as const;

const HOME_WORKFLOW_ENTRY_ICONS = {
  pvd_parameter_recommendation: Beaker,
  evidence_analysis: BookOpen,
  cs_reproduction: Cpu,
  materials_discovery: Atom,
} as const satisfies Record<Exclude<ResearchPath, "standard">, LucideIcon>;

const HOME_AGENT_ENTRY_ICONS: Record<string, LucideIcon> = {
  "agent:research_question_refinement": Lightbulb,
  "agent:validation_design": FlaskConical,
};

function homeEntryClass(selected: boolean) {
  return `inline-flex h-10 min-w-0 flex-1 basis-0 items-center justify-center gap-2.5 whitespace-nowrap border-0 bg-transparent px-2 text-[14px] leading-none transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/20 ${
    selected ? "font-medium text-shell-text" : "text-shell-text-secondary hover:text-shell-text"
  }`;
}

const HOME_WORKFLOW_MENTIONS = [
  {
    label: "材料发现全流程",
    description: "文献调研、候选设计、制备评估、参数推荐、DFT 验证到报告生成",
    researchPath: "materials_discovery",
  },
  {
    label: "薄膜沉积（PVD）工艺优化",
    description: "结合设备约束和实验反馈，规划下一轮实验参数",
    researchPath: "pvd_parameter_recommendation",
  },
  {
    label: "科研证据综合分析",
    description: "综合多项研究，分析证据差异、争议与不确定性",
    researchPath: "evidence_analysis",
  },
  {
    label: "计算机论文实验复现",
    description: "准备代码与环境，运行实验并对比论文指标",
    researchPath: "cs_reproduction",
  },
] as const satisfies ReadonlyArray<{
  label: string;
  description: string;
  researchPath: Exclude<ResearchPath, "standard">;
}>;

const CS_REPRODUCTION_MENTION = "@计算机论文实验复现";

const HOME_ENTRY_EXAMPLE_QUERIES = {
  materials_discovery:
    "帮我发现一个简单的宽带隙氧化物半导体，限定二元体系 Mg-O，带隙在 3-6 eV，用于紫外探测器。为快速验证，从 Materials Project 检索导出 2 个候选作为种子，基于扩散模型演化出 4 个候选，并对综合排名第一的候选做一次 DFT 热力学稳定性验证（ehull 越低越好），并生成实验方案到云实验平台开展制备，最后给出完整分析报告。",
  pvd_parameter_recommendation:
    "用反应磁控溅射制备 Sc 含量 40% 的 AlScN 压电薄膜，想在 Mo 底电极上优化下一轮溅射工艺，主目标是提高薄膜 (002) 择优取向（XRD 摇摆曲线 FWHM 越小越好），同时保持沉积速率不低于 0.3 nm/s，单层膜厚控制在 1.0 μm 左右。\n\n设备与体系：中频磁控溅射台；Al-40%Sc 合金靶（Ø110 mm）；靶基距 110 mm；靶偏置角 30°；基片衬底为 Mo/Si(100)（Mo 底电极 200 nm）；直流电源设定温度 200 °C；工作气体 Ar，反应气体 N2；基片加热不开启。\n\n可调参数范围（本轮推荐必须落在范围内）：溅射功率 130–255 W；N2 流量 12–50 sccm；Ar 流量 27–60 sccm；沉积时间 1500–4600 s。\n硬约束：N2 占总气流量比例 28%–52%；总气流量 39–90 sccm；基片温度不得超过 350 °C（Mo/Si 工艺兼容要求）。\n待定条件参数：腔体是否加装 RF 偏压（-50 V）尚未确定，先作为待评估项处理，不要进入本轮正式推荐参数。\n\n已完成实验（同体系、同一靶）：\n- E1：功率 180 W，N2 20 sccm，Ar 35 sccm，时间 2400 s → 膜厚 820 nm，(002) FWHM 3.2°，沉积速率约 0.34 nm/s，表面均匀；\n- E2：功率 220 W，N2 30 sccm，Ar 40 sccm，时间 3000 s → 膜厚 1180 nm，(002) FWHM 2.1°，沉积速率约 0.39 nm/s，样片边缘有轻微雾斑。\n已知问题：N2 流量偏高时靶面氮化、沉积速率明显下降，偶尔打弧。\n\n本轮希望推荐不超过 3 组实验，按优先级排序，并说明每组要验证什么。",
  evidence_analysis:
    "LLM judge 会不会偏爱更长、更会解释的回答？我做评测时需要专门控制这个问题吗？",
  cs_reproduction:
    "我想复现一下 SimCSE 里 supervised BERT-base 的 STS 结果，尽量按作者公开的代码和设置跑一遍，看看能不能复现出来。",
  "agent:research_question_refinement":
    "导师让做 agent memory。我跑长任务时更烦的是：关键状态明明出现过，过一会儿又像没发生。这算 memory，还是上下文管理？题目还写不出来。",
  "agent:validation_design":
    "长程 tool-use 用 outcome reward，前面很多有用的步会因最后一错整段变负。想改成按 tool-call 做 turn-level credit，但 BrowseComp 够不够看出差别？logprob 当 value 稳不稳也不敢定。",
} as const;

function workflowEntryPrefill(workflow: (typeof HOME_WORKFLOW_MENTIONS)[number]) {
  return `@${workflow.label} ${HOME_ENTRY_EXAMPLE_QUERIES[workflow.researchPath]}`;
}

function isRunnableAgent(resource: ResourcePlazaResource): boolean {
  return (
    resource.kind === "agent" &&
    resource.availability === "tryable" &&
    resource.primaryAction === "try" &&
    resource.durableExecutable === true &&
    !resource.executionBlocked &&
    resource.dependencyGaps.length === 0
  );
}

function isRunnableHomeAgent(resource: ResourcePlazaResource): boolean {
  return resource.visibleOnHome && isRunnableAgent(resource);
}

export default function HomePage() {
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const notices = useUserNoticeOptional();
  const [searchParams] = useSearchParams();

  usePageHeader({ action: null });
  const [usingId, setUsingId] = useState<string | null>(null);
  const [goalSummary, setGoalSummary] = useState("");
  const [researchPath, setResearchPath] = useState<ResearchPath>("standard");
  const [executionMode, setExecutionMode] = useState<TaskExecutionMode>("automatic_research");
  const executionModeOptions: Array<{ label: string; value: TaskExecutionMode }> = [
    { label: "自动研究", value: "automatic_research" },
    { label: "分阶段确认", value: "stage_confirmation" },
  ];
  const [availableAgents, setAvailableAgents] = useState<ResourcePlazaResource[]>([]);
  const [boundCatalogResource, setBoundCatalogResource] = useState<BoundCatalogResource | null>(null);
  const [acceptedResearchStart, setAcceptedResearchStart] = useState<ResearchStartAcceptedResponse | null>(null);
  const activeStartRequestIdRef = useRef<string | null>(null);
  const currentIntentSessionIdRef = useRef<string | null>(null);
  const mentionWorkflowLabelRef = useRef<string | null>(null);
  const mentionAgentLabelRef = useRef<string | null>(null);
  const catalogPromotionRef = useRef<{
    catalogIntentId: string;
    promise: ReturnType<typeof promoteResearchStartIntent>;
  } | null>(null);

  const applyPromotedCatalogBinding = (result: Awaited<ReturnType<typeof promoteResearchStartIntent>>) => {
    currentIntentSessionIdRef.current = result.sessionId;
    setBoundCatalogResource({
      id: result.selectedResourceId,
      name: result.selectedResourceName,
      kind: result.selectedResourceKind,
    });
  };

  const clearCatalogBinding = () => {
    currentIntentSessionIdRef.current = null;
    setBoundCatalogResource(null);
  };

  const promoteCatalogIntentOnce = (catalogIntentId: string) => {
    const existing = catalogPromotionRef.current;
    if (existing?.catalogIntentId === catalogIntentId) return existing.promise;
    const promise = promoteResearchStartIntent(catalogIntentId);
    catalogPromotionRef.current = { catalogIntentId, promise };
    return promise;
  };

  const resetStartContinuation = () => {
    if (searchParams.get("intent") === "create" && searchParams.get("sessionId")) return;
    currentIntentSessionIdRef.current = null;
  };

  const ensureCatalogBindingBeforeSubmit = async (): Promise<boolean> => {
    const catalogIntentId = searchParams.get("intent") === "create"
      ? searchParams.get("sessionId")
      : null;
    if (!catalogIntentId) return true;
    const sameCatalogIntent = catalogPromotionRef.current?.catalogIntentId === catalogIntentId;
    if (sameCatalogIntent && currentIntentSessionIdRef.current) return true;
    if (!sameCatalogIntent) currentIntentSessionIdRef.current = null;
    setUsingId("__catalog_promote__");
    try {
      const result = await promoteCatalogIntentOnce(catalogIntentId);
      applyPromotedCatalogBinding(result);
      return true;
    } catch {
      if (catalogPromotionRef.current?.catalogIntentId === catalogIntentId) {
        catalogPromotionRef.current = null;
      }
      clearCatalogBinding();
      message.error("所选智能体暂时无法用于当前项目，请重新选择。");
      return false;
    } finally {
      setUsingId(current => current === "__catalog_promote__" ? null : current);
    }
  };

  const resolveResearchStartIdentity = (input: string, identityInput = input) => {
    const sourcePage = "home" as const;
    const presetQuery = searchParams.get("query") ?? undefined;
    const fingerprint = researchStartIntentFingerprint(identityInput, sourcePage, presetQuery);
    const pending = readResearchStartPending();
    const authoritativeCatalogSessionId = currentIntentSessionIdRef.current;
    const reusable = pending
      && canReuseResearchStartClientRequest(pending.status)
      && pending.intentFingerprint === fingerprint
      && pending.clientRequestId.trim()
      && (!authoritativeCatalogSessionId || pending.sessionId === authoritativeCatalogSessionId)
      ? pending
      : null;
    const clientRequestId = reusable?.clientRequestId ?? createClientRequestId();
    if (reusable) {
      rememberResearchStartPending({
        ...reusable,
        status: "pending",
        sessionId: authoritativeCatalogSessionId ?? reusable.sessionId,
      });
    } else {
      const next = beginResearchStartPending({ input, sourcePage, presetQuery, clientRequestId });
      const pendingWithIdentity = { ...next, intentFingerprint: fingerprint };
      if (authoritativeCatalogSessionId) {
        rememberResearchStartPending({ ...pendingWithIdentity, sessionId: authoritativeCatalogSessionId });
      } else {
        rememberResearchStartPending(pendingWithIdentity);
      }
    }
    return { clientRequestId, sessionId: authoritativeCatalogSessionId ?? reusable?.sessionId };
  };

  const clearKnownFailedResearchStart = (clientRequestId: string) => {
    const pending = readResearchStartPending();
    if (pending?.clientRequestId === clientRequestId) clearResearchStartPending();
  };

  const publishResearchStartNotice = (
    sessionKey: string,
    view: ReturnType<typeof projectResearchStartWaitingView>,
  ) => {
    notices?.upsertNotice({
      id: `research-start:${sessionKey}`,
      source: "research-start",
      title: view.title,
      message: view.message,
      level: view.level,
      status: view.shouldOpenWorkbench ? "done" : view.level === "error" ? "failed" : "active",
      elapsedSeconds: view.elapsedSeconds,
      nextActionHint: view.nextActionHint,
      openPanel: view.level === "error",
    });
  };

  const openAcceptedResearchTask = (accepted: ResearchStartAcceptedResponse) => {
    currentIntentSessionIdRef.current = accepted.sessionId;
    const activeClientRequestId = activeStartRequestIdRef.current;
    if (activeClientRequestId) {
      rememberResearchStartAccepted({
        clientRequestId: activeClientRequestId,
        sessionId: accepted.sessionId,
        reservedTaskId: accepted.reservedTaskId,
      });
      if (activeClientRequestId !== accepted.sessionId) {
        notices?.dismissNotice(`research-start:${activeClientRequestId}`);
      }
    }
    setAcceptedResearchStart(accepted);
    publishResearchStartNotice(
      accepted.sessionId,
      projectResearchStartWaitingView({
        sessionId: accepted.sessionId,
        status: accepted.status === "queued" ? "queued" : "running",
        reservedTaskId: accepted.reservedTaskId,
        currentPhase: "understanding",
      }),
    );
    navigate(
      `/workbench/${encodeURIComponent(accepted.reservedTaskId)}?planningSessionId=${encodeURIComponent(accepted.sessionId)}`,
      { replace: true },
    );
  };

  useEffect(() => {
    const preset = searchParams.get("query");
    if (preset !== null) setGoalSummary(preset);
    if (searchParams.get("intent") === "create" || preset !== null) {
      window.setTimeout(() => {
        document.getElementById("research-intent-input")?.focus();
      }, 0);
    }

    const catalogIntentId = searchParams.get("intent") === "create"
      ? searchParams.get("sessionId")
      : null;
    if (!catalogIntentId) {
      clearCatalogBinding();
      return;
    }

    if (catalogPromotionRef.current?.catalogIntentId !== catalogIntentId) {
      clearCatalogBinding();
    }
    const promotion = promoteCatalogIntentOnce(catalogIntentId);
    let active = true;
    setUsingId(current => current ?? "__catalog_promote__");
    void promotion
      .then(result => {
        if (!active) return;
        applyPromotedCatalogBinding(result);
      })
      .catch(() => {
        if (!active) return;
        clearCatalogBinding();
        if (catalogPromotionRef.current?.catalogIntentId === catalogIntentId) {
          catalogPromotionRef.current = null;
        }
        message.error("所选智能体暂时无法用于当前项目，请重新选择。");
      })
      .finally(() => {
        if (!active) return;
        setUsingId(current => current === "__catalog_promote__" ? null : current);
      });
    return () => {
      active = false;
    };
  }, [message, searchParams]);

  useEffect(() => {
    const controller = new AbortController();
    void fetchResourceCatalog(HOME_AGENT_QUERY, controller.signal)
      .then(page => {
        if (controller.signal.aborted) return;
        setAvailableAgents(page.resources.filter(isRunnableAgent));
      })
      .catch(() => {
        if (!controller.signal.aborted) setAvailableAgents([]);
      });
    return () => controller.abort();
  }, []);

  const focusResearchInput = () => {
    window.setTimeout(() => {
      document.getElementById("research-intent-input")?.focus();
    }, 0);
  };

  const fillHomeInput = (query: string) => {
    setGoalSummary(query);
    resetStartContinuation();
    focusResearchInput();
  };

  const clearMentionSelection = () => {
    mentionWorkflowLabelRef.current = null;
    mentionAgentLabelRef.current = null;
  };

  const applyWorkflowEntry = (workflow: (typeof HOME_WORKFLOW_MENTIONS)[number]) => {
    mentionWorkflowLabelRef.current = workflow.label;
    mentionAgentLabelRef.current = null;
    setResearchPath(workflow.researchPath);
    clearCatalogBinding();
    catalogPromotionRef.current = null;
    if (searchParams.get("intent") === "create" || searchParams.get("sessionId")) {
      navigate("/", { replace: true });
    }
    fillHomeInput(workflowEntryPrefill(workflow));
  };

  const selectPvdParameterRecommendation = () => {
    const workflow = HOME_WORKFLOW_MENTIONS.find(item => item.researchPath === "pvd_parameter_recommendation");
    if (workflow) applyWorkflowEntry(workflow);
  };

  const selectEvidenceAnalysis = () => {
    const workflow = HOME_WORKFLOW_MENTIONS.find(item => item.researchPath === "evidence_analysis");
    if (workflow) applyWorkflowEntry(workflow);
  };

  const selectCsReproduction = () => {
    const workflow = HOME_WORKFLOW_MENTIONS.find(item => item.researchPath === "cs_reproduction");
    if (workflow) applyWorkflowEntry(workflow);
  };

  const selectMaterialsDiscovery = () => {
    const workflow = HOME_WORKFLOW_MENTIONS.find(item => item.researchPath === "materials_discovery");
    if (workflow) applyWorkflowEntry(workflow);
  };

  const selectHomeAgent = async (resource: ResourcePlazaResource, prefillExample = true) => {
    if (usingId || activeStartRequestIdRef.current) return;
    if (prefillExample) clearMentionSelection();
    setResearchPath("standard");
    const exampleQuery =
      resource.id === "agent:research_question_refinement" ||
      resource.id === "agent:validation_design"
        ? HOME_ENTRY_EXAMPLE_QUERIES[resource.id]
        : null;
    if (prefillExample && exampleQuery) fillHomeInput(exampleQuery);
    setUsingId(resource.id);
    try {
      const result = await createResearchStartIntent(resource.id);
      const target = result.researchStartUrl.trim();
      navigate(target.startsWith("/") ? target : `/${target}`);
    } catch {
      message.error("该智能体暂时无法用于当前项目，请稍后重试。");
    } finally {
      setUsingId(current => current === resource.id ? null : current);
    }
  };

  const handleGoalSummaryChange = (value: string) => {
    setGoalSummary(value);
    const selectedWorkflow = HOME_WORKFLOW_MENTIONS
      .map(option => ({ option, index: value.lastIndexOf(`@${option.label}`) }))
      .filter(candidate => candidate.index >= 0)
      .sort((left, right) => right.index - left.index)[0]?.option;
    if (selectedWorkflow && mentionWorkflowLabelRef.current) {
      mentionWorkflowLabelRef.current = selectedWorkflow.label;
      setResearchPath(selectedWorkflow.researchPath);
    } else if (!selectedWorkflow && mentionWorkflowLabelRef.current) {
      mentionWorkflowLabelRef.current = null;
      setResearchPath("standard");
    }
    if (mentionAgentLabelRef.current && !value.includes(`@${mentionAgentLabelRef.current}`)) {
      mentionAgentLabelRef.current = null;
      clearCatalogBinding();
      catalogPromotionRef.current = null;
    }
    resetStartContinuation();
  };

  const handleMentionSelect = (value: string) => {
    const workflow = HOME_WORKFLOW_MENTIONS.find(option => option.label === value);
    if (workflow) {
      applyWorkflowEntry(workflow);
      return;
    }
    const agent = availableAgents.find(resource => resource.name === value);
    if (!agent) return;
    mentionWorkflowLabelRef.current = null;
    mentionAgentLabelRef.current = agent.name;
    void selectHomeAgent(agent, false);
  };

  const handleResearchIntentStart = async () => {
    if (usingId || activeStartRequestIdRef.current || acceptedResearchStart) {
      message.info("正在准备项目，请稍候。");
      notices?.setPanelOpen(true);
      return;
    }
    if (!(await ensureCatalogBindingBeforeSubmit())) return;
    const cleanedGoal = goalSummary.trim();
    if (!cleanedGoal) {
      message.warning("先描述你正在研究的问题。");
      focusResearchInput();
      return;
    }

    const startInput = {
      input: cleanedGoal,
      sourcePage: "home" as const,
      presetQuery: searchParams.get("query") ?? undefined,
      requestedWorkflow: researchPath === "pvd_parameter_recommendation"
        ? "pvd_process_optimization_workflow" as const
        : researchPath === "evidence_analysis"
          ? "opentrajectory_research_workflow" as const
          : researchPath === "materials_discovery"
            ? "materials_full_discovery_workflow" as const
            : researchPath === "cs_reproduction" || cleanedGoal.startsWith(CS_REPRODUCTION_MENTION)
              ? "cs_paper_reproduction_workflow" as const
              : undefined,
      executionMode,
      createClarificationDraft: true,
    };

    const { clientRequestId, sessionId: recoverableSessionId } = resolveResearchStartIdentity(
      cleanedGoal,
      `${researchPath}:${cleanedGoal}`,
    );
    activeStartRequestIdRef.current = clientRequestId;
    setUsingId("__direct_draft__");
    setAcceptedResearchStart(null);
    notices?.upsertNotice({
      id: `research-start:${clientRequestId}`,
      source: "research-start",
      title: "正在准备研究方案",
      message: "正在提交研究请求。",
      level: "progress",
      status: "active",
      nextActionHint: "准备完成后会在工作台内显示研究方案。",
    });
    let workbenchOpened = false;
    try {
      const accepted = await submitResearchStartStream(
        {
          ...startInput,
          clientRequestId,
          sessionId: currentIntentSessionIdRef.current ?? recoverableSessionId ?? undefined,
        },
        event => {
          window.dispatchEvent(
            new CustomEvent("openair:research-start-progress", { detail: event }),
          );
          if (event.type === "accepted" && !workbenchOpened) {
            workbenchOpened = true;
            openAcceptedResearchTask(event);
          }
        },
      );
      if (!workbenchOpened) {
        workbenchOpened = true;
        openAcceptedResearchTask(accepted);
      }
    } catch (error) {
      if (!workbenchOpened && isResearchStartResponseUnknown(error)) {
        try {
          const recovered = await submitResearchStart({
            ...startInput,
            clientRequestId,
            sessionId: currentIntentSessionIdRef.current ?? recoverableSessionId ?? undefined,
          });
          workbenchOpened = true;
          openAcceptedResearchTask(recovered);
        } catch (recoveryError) {
          if (!isResearchStartResponseUnknown(recoveryError)) {
            clearKnownFailedResearchStart(clientRequestId);
          }
          const recoveryText = userFacingResearchStartError(recoveryError);
          notices?.upsertNotice({
            id: `research-start:${clientRequestId}`,
            source: "research-start",
            title: "任务准备未完成",
            message: recoveryText,
            level: "error",
            status: "failed",
            nextActionHint: "请检查输入后重试。",
            openPanel: true,
          });
          message.error(recoveryText);
        }
      } else if (!workbenchOpened) {
        clearKnownFailedResearchStart(clientRequestId);
        const startErrorText = userFacingResearchStartError(error);
        notices?.upsertNotice({
          id: `research-start:${clientRequestId}`,
          source: "research-start",
          title: "任务准备未完成",
          message: startErrorText,
          level: "error",
          status: "failed",
          nextActionHint: "请检查输入后重试。",
          openPanel: true,
        });
        message.error(startErrorText);
      }
    } finally {
      setUsingId(null);
      activeStartRequestIdRef.current = null;
    }
  };

  const selectWorkflowEntry = {
    pvd_parameter_recommendation: selectPvdParameterRecommendation,
    evidence_analysis: selectEvidenceAnalysis,
    cs_reproduction: selectCsReproduction,
    materials_discovery: selectMaterialsDiscovery,
  } as const;
  const visibleHomeAgents = HOME_RECOMMENDED_AGENT_IDS.flatMap(resourceId => {
    const resource = availableAgents.find(candidate => candidate.id === resourceId);
    return resource && isRunnableHomeAgent(resource) ? [resource] : [];
  });
  const mentionOptions = [
    ...HOME_WORKFLOW_MENTIONS.map(option => ({
      key: `workflow:${option.researchPath}`,
      value: option.label,
      label: (
        <span className="flex min-w-[320px] items-center justify-between gap-4 py-1">
          <span className="min-w-0">
            <span className="block font-medium text-shell-text">{option.label}</span>
            <span className="block truncate text-xs text-shell-text-secondary">{option.description}</span>
          </span>
          <span className="shrink-0 text-xs text-shell-text-secondary">工作流</span>
        </span>
      ),
    })),
    ...availableAgents.map(resource => ({
      key: resource.id,
      value: resource.name,
      label: (
        <span className="flex min-w-[320px] items-center justify-between gap-4 py-1">
          <span className="min-w-0 truncate font-medium text-shell-text">{resource.name}</span>
          <span className="shrink-0 text-xs text-shell-text-secondary">智能体</span>
        </span>
      ),
    })),
  ];
  const startPreparing = Boolean(acceptedResearchStart);

  return (
    <div
      className="h-full min-h-0 overflow-y-auto overscroll-contain bg-surface"
      data-testid="starter-home-page"
    >
      <main className="mx-auto w-full bg-surface px-[clamp(16px,2vw,28px)]">
        <section className="relative mx-auto w-full max-w-[964px] pb-12 pt-[clamp(100px,calc(8vh+32px),124px)] min-[1600px]:max-w-[1120px] min-[2200px]:max-w-[1240px] max-[720px]:pt-6">
          <header className="text-center max-[720px]:text-left">
            <Typography.Title
              className="m-0 text-[clamp(30px,2.6vw,44px)]! font-semibold leading-[1.3]! tracking-[-0.02em] text-shell-text-strong! max-[720px]:text-[30px]! max-[560px]:text-[26px]!"
            >
              从一个科研问题开始
            </Typography.Title>
            <Typography.Paragraph className="mb-0! mt-3! text-[16px]! leading-7! text-shell-text-secondary! max-[720px]:text-[15px]!">
              把正在研究、想验证或卡住的问题交给 OpenAIR，帮你梳理研究路径并启动项目。
            </Typography.Paragraph>
          </header>

          <section
            className="mt-[clamp(32px,4.3vh,44px)] flex h-[clamp(180px,19vh,196px)] w-full flex-col overflow-hidden rounded-xl border border-border bg-surface max-[420px]:h-[272px]"
            aria-label="开始项目"
          >
            <Mentions
              id="research-intent-input"
              data-testid="research-intent-input"
              value={goalSummary}
              onChange={handleGoalSummaryChange}
              onSelect={option => handleMentionSelect(String(option.value))}
              options={mentionOptions}
              prefix="@"
              split=" "
              placeholder="直接描述你的研究问题，或输入 @ 选择工作流 / 智能体"
              autoSize={false}
              rows={4}
              maxLength={1200}
              className="min-h-0! flex-1 border-0! bg-surface! shadow-none! [&_textarea]:h-full! [&_textarea]:resize-none! [&_textarea]:px-[21px]! [&_textarea]:py-[22px]! [&_textarea]:text-[16px]! [&_textarea]:leading-7! [&_textarea]:text-text! [&_textarea]:placeholder:text-shell-text-secondary"
              aria-label="研究问题输入"
            />

            <div className="flex min-h-[74px] items-center justify-between gap-3 px-[21px] pb-[21px] max-[560px]:pb-4 max-[420px]:min-h-[126px] max-[420px]:flex-col max-[420px]:items-stretch max-[420px]:pt-2">
              <div className="flex min-w-0 items-center gap-2 max-[420px]:w-full">
                {boundCatalogResource ? <BoundResourcePill resource={boundCatalogResource} /> : null}
                <Select<TaskExecutionMode>
                  value={executionMode}
                  onChange={value => setExecutionMode(value)}
                  options={executionModeOptions}
                  aria-label="研究推进方式"
                  popupMatchSelectWidth={false}
                  className="w-[138px] shrink-0 max-[420px]:w-full [&_.ant-select-selector]:h-[50px]! [&_.ant-select-selector]:items-center! [&_.ant-select-selector]:rounded-lg! [&_.ant-select-selector]:border-border! [&_.ant-select-selection-item]:leading-[48px]!"
                />
              </div>

              <Button
                type="primary"
                size="middle"
                loading={usingId === "__direct_draft__" || usingId === "__catalog_promote__" || startPreparing}
                onClick={() => void handleResearchIntentStart()}
                data-testid="starter-home-main-create"
                className="h-12! w-[142px] shrink-0 rounded-lg! bg-brand! px-5! text-[16px]! font-medium! shadow-none! max-[560px]:w-[132px] max-[420px]:w-full"
              >
                <span className="inline-flex items-center gap-3">
                  开始项目
                  <ArrowRightOutlined />
                </span>
              </Button>
            </div>
          </section>

          <section className="mt-[clamp(32px,4.3vh,44px)]" aria-label="问题示例">
            <Typography.Text strong className="text-[17px]! text-shell-text-strong!">
              问题示例
            </Typography.Text>

            <div className="mt-3 border-b border-border">
              {HOME_PROMPT_SAMPLES.map(sample => (
                <button
                  key={sample.id}
                  type="button"
                  className="group flex h-[clamp(64px,7.4vh,76px)] w-full items-center gap-6 border-0 border-t border-border bg-transparent px-0 text-left outline-none transition-colors first:border-t-0 hover:bg-surface-soft focus-visible:bg-surface-soft focus-visible:outline-none"
                  onClick={() => fillHomeInput(sample.query)}
                >
                  <span className="min-w-0 flex-1 truncate text-[15px] leading-6 text-shell-text-secondary transition-colors group-hover:text-shell-text">
                    {sample.query}
                  </span>
                  <ArrowRightOutlined className="mr-2 shrink-0 text-[15px] text-shell-text-secondary transition-transform group-hover:translate-x-0.5 group-hover:text-brand" />
                </button>
              ))}
            </div>
          </section>

          <section className="mt-0 pt-[clamp(28px,3.5vh,36px)]" aria-label="智能体与工作流入口">
            <Typography.Text strong className="text-[17px]! text-shell-text-strong!">
              推荐入口
            </Typography.Text>

            <div className="mt-5 flex w-full items-center gap-4 max-[720px]:flex-col max-[720px]:items-stretch">
              {HOME_WORKFLOW_MENTIONS.map(workflow => {
                const selected = researchPath === workflow.researchPath && !boundCatalogResource;
                const Icon = HOME_WORKFLOW_ENTRY_ICONS[workflow.researchPath];
                return (
                  <button
                    key={workflow.researchPath}
                    type="button"
                    aria-pressed={selected}
                    className={homeEntryClass(selected)}
                    onClick={selectWorkflowEntry[workflow.researchPath]}
                  >
                    <Icon size={20} strokeWidth={1.75} className="shrink-0" aria-hidden />
                    <span>{workflow.label}</span>
                  </button>
                );
              })}

              {visibleHomeAgents.map(resource => {
                const selected = boundCatalogResource?.id === resource.id;
                const Icon = HOME_AGENT_ENTRY_ICONS[resource.id] ?? Bot;
                return (
                  <button
                    key={resource.id}
                    type="button"
                    aria-pressed={selected}
                    aria-busy={usingId === resource.id}
                    className={homeEntryClass(selected)}
                    onClick={() => void selectHomeAgent(resource)}
                  >
                    <Icon size={20} strokeWidth={1.75} className="shrink-0" aria-hidden />
                    <span>{resource.name}</span>
                  </button>
                );
              })}
            </div>

            <div className="mt-5 flex justify-center">
              <Button
                type="link"
                size="small"
                icon={<ArrowRightOutlined />}
                iconPlacement="end"
                className="px-0! text-[14px]! font-normal! text-shell-text-secondary! hover:text-brand!"
                onClick={() => navigate("/resources")}
              >
                浏览全部资源
              </Button>
            </div>
          </section>
        </section>
      </main>
    </div>
  );
}
