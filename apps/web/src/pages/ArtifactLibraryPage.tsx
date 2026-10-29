import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type HTMLAttributes } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Alert, Button, Drawer, Empty, Input, Modal, Select, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { ReloadOutlined, UploadOutlined, EditOutlined, HistoryOutlined } from "@ant-design/icons";
import type { TaskStage } from "@openair/shared";
import {
  fetchArtifactContentById,
  fetchArtifactDetail,
  fetchArtifactRelations,
  fetchArtifacts,
  fetchTaskTitleOptions,
  confirmArtifactScopeExpansion,
  reviewArtifact,
  reuseArtifact,
  uploadArtifact,
} from "../api/client";
import type { ArtifactContentResponse, ArtifactRelation, CatalogArtifact, TaskTitleOption } from "../api/client";
import { ArtifactRenderer } from "../components/artifacts/ArtifactRenderer";
import { ArtifactVersionDrawer } from "../components/artifacts/ArtifactVersionDrawer";
import type { WorkbenchArtifact } from "../model/workbenchTask";
import { artifactHumanGate, artifactTypeLabel, isResearchStartDraftArtifact } from "../lib/workbenchUi";
import { readCurrentTask, rememberCurrentArtifact, rememberCurrentTask } from "../lib/userRouteState";
import { userFacingErrorText } from "../lib/userFacingLanguage";

const ORIGIN_OPTIONS = [
  { value: "", label: "来源：全部" },
  { value: "generated", label: "生成" },
  { value: "imported", label: "导入/复用" },
  { value: "external", label: "外部引用" },
];

interface FilterState {
  q: string;
  kind: string;
  origin: string;
  format: string;
  sourceTaskId: string;
  searchInline: boolean;
}

const EMPTY_FILTERS: FilterState = { q: "", kind: "", origin: "", format: "", sourceTaskId: "", searchInline: false };

function isOwnershipConfirmationError(err: unknown): boolean {
  return err instanceof Error && err.message.includes("ownership_confirmation_required");
}

function toStorageType(value: string | null | undefined): WorkbenchArtifact["storageType"] {
  return value === "inline" || value === "local" || value === "external" ? value : undefined;
}

function catalogArtifactForWorkbench(artifact: CatalogArtifact): WorkbenchArtifact {
  return {
    id: artifact.id,
    taskId: artifact.taskId ?? artifact.sourceTaskId,
    sourceTaskId: artifact.sourceTaskId,
    type: artifact.kind,
    title: artifact.title,
    summary: artifact.summary ?? null,
    format: artifact.format ?? "",
    relatedStage: (artifact.relatedStage ?? artifact.stage ?? "insight_generation") as TaskStage,
    relatedSkillId: artifact.relatedSkillId ?? undefined,
    substage: artifact.substage ?? null,
    subStageInstanceId: artifact.subStageInstanceId ?? null,
    sourceCapabilityCallId: artifact.sourceCapabilityCallId ?? null,
    artifactState: artifact.artifactState ?? null,
    validatorResult: artifact.validatorResult as WorkbenchArtifact["validatorResult"],
    resultProtocol: artifact.resultProtocol ?? null,
    artifactQuality: artifact.artifactQuality ?? null,
    warnings: artifact.warnings ?? [],
    humanGate: artifact.humanGate as WorkbenchArtifact["humanGate"],
    artifactType: artifact.artifactType ?? artifact.kind,
    storageType: toStorageType(artifact.storageType),
    linkedMaterialIds: artifact.linkedMaterialIds,
    materialManifest: artifact.materialManifest,
    memoryRefs: artifact.memoryRefs,
  };
}

function artifactLifecycleStatus(state: string | null | undefined): { label: string; color: string } | null {
  const gate = artifactHumanGate({ artifactState: state } as WorkbenchArtifact);
  if (gate.state === "usable") return { label: "已核查入库", color: "success" };
  if (gate.state === "needs_review") return { label: "待核查", color: "warning" };
  if (gate.state === "needs_input") return { label: "待补充", color: "warning" };
  if (gate.state === "insufficient_materials") return { label: "材料不足", color: "warning" };
  if (gate.state === "failed") return { label: "未形成可用产物", color: "error" };
  if (gate.state === "partial_success") return { label: "需确认范围", color: "processing" };
  if (gate.state === "rejected_or_needs_revision") return { label: "需修订", color: "error" };
  switch (state) {
    case "raw_output":
      return { label: "原始输出", color: "default" };
    case "pending_review":
      return { label: "待核查", color: "warning" };
    case "verified_artifact":
      return { label: "已核查入库", color: "success" };
    case "rejected_or_needs_revision":
      return { label: "需修订", color: "error" };
    case "stage_summary":
      return { label: "阶段摘要", color: "processing" };
    default:
      return null;
  }
}

function artifactUserStatus(artifact: CatalogArtifact): { label: string; color: string } {
  if (isResearchStartDraftArtifact(catalogArtifactForWorkbench(artifact))) return { label: "研究起点草案", color: "processing" };
  const lifecycle = artifactLifecycleStatus(artifact.artifactState);
  if (lifecycle) return lifecycle;
  const text = `${artifact.title} ${artifact.summary ?? ""}`.toLowerCase();
  if (/失败|不可|failed|error/.test(text)) return { label: "不可引用", color: "error" };
  if (/缺|待补|readiness|gap|missing/.test(text)) return { label: "缺来源", color: "warning" };
  if (/next|下一步|继续/.test(text)) return { label: "可继续", color: "processing" };
  return { label: "待复核", color: "default" };
}

function canonicalStateConfirmsReview(
  artifact: CatalogArtifact,
  decision: "approved" | "rejected" | "needs_revision",
): boolean {
  const state = artifactHumanGate(catalogArtifactForWorkbench(artifact)).state;
  return decision === "approved"
    ? state === "usable"
    : state === "rejected_or_needs_revision";
}

function formatArtifactTime(value: string | null | undefined): string {
  if (!value) return "未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

export default function ArtifactLibraryPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const routeSourceTaskId = searchParams.get("sourceTaskId")?.trim() ?? "";
  const [artifacts, setArtifacts] = useState<CatalogArtifact[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<FilterState>(() => ({
    ...EMPTY_FILTERS,
    sourceTaskId: searchParams.get("sourceTaskId")?.trim() ?? "",
  }));
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [tasks, setTasks] = useState<TaskTitleOption[]>([]);
  const [taskOptionsLoading, setTaskOptionsLoading] = useState(false);
  const [taskOptionsLoaded, setTaskOptionsLoaded] = useState(false);

  // 预览抽屉
  const [previewArtifact, setPreviewArtifact] = useState<CatalogArtifact | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewContent, setPreviewContent] = useState<ArtifactContentResponse | null>(null);
  const [previewUnavailable, setPreviewUnavailable] = useState<"not_found" | "transient" | null>(null);
  const [previewRelations, setPreviewRelations] = useState<ArtifactRelation[]>([]);
  const [reviewingArtifact, setReviewingArtifact] = useState(false);
  const [versionDrawerOpen, setVersionDrawerOpen] = useState(false);
  const artifactListRequestGenerationRef = useRef(0);
  const previewRequestGenerationRef = useRef(0);
  const activeSourceTaskIdRef = useRef(routeSourceTaskId);

  // issue #302: 产物版本管理可用性（基于目录行 format/kind 推断，避免 WorkbenchArtifact 强转）
  const previewArtifactFormat = (previewArtifact?.format ?? "").toLowerCase();
  const isPreviewDraft =
    !!previewArtifact
    && (previewArtifact.kind === "research_start_draft"
      || previewArtifact.relatedSkillId === "research_start_draft");
  const isPreviewEditable = Boolean(
    previewArtifact
    && (["markdown", "md", "text", "txt"].includes(previewArtifactFormat) || isPreviewDraft),
  );
  // issue #302: regenerate is substage-scoped (only stage_summary outputs have a
  // resolvable re-run path); hide the button for uploads / reused / chat artifacts.
  const canPreviewRegenerate = Boolean(previewArtifact && previewArtifact.kind === "stage_summary");

  // 复用弹窗
  const [reuseArtifactId, setReuseArtifactId] = useState<string | null>(null);
  const [reuseTarget, setReuseTarget] = useState<string | null>(null);
  const [reusing, setReusing] = useState(false);

  // 上传弹窗（Artifact-02）
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadTarget, setUploadTarget] = useState<string | null>(null);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    const uploadTaskId = searchParams.get("uploadTaskId")?.trim();
    if (!uploadTaskId) return;
    setUploadTarget(uploadTaskId);
    setUploadOpen(true);
    const next = new URLSearchParams(searchParams);
    next.delete("uploadTaskId");
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  useLayoutEffect(() => {
    if (
      filters.sourceTaskId === routeSourceTaskId
      && activeSourceTaskIdRef.current === routeSourceTaskId
    ) return;
    artifactListRequestGenerationRef.current += 1;
    previewRequestGenerationRef.current += 1;
    activeSourceTaskIdRef.current = routeSourceTaskId;
    window.queueMicrotask(() => {
      if (activeSourceTaskIdRef.current !== routeSourceTaskId) return;
      setArtifacts([]);
      setTotal(0);
      setError(null);
      setLoading(true);
      setPreviewArtifact(null);
      setPreviewContent(null);
      setPreviewUnavailable(null);
      setPreviewRelations([]);
      setPreviewLoading(false);
      setFilters((current) => ({ ...current, sourceTaskId: routeSourceTaskId }));
      setPage(1);
    });
  }, [filters.sourceTaskId, routeSourceTaskId]);

  const ensureTaskOptions = useCallback(() => {
    if (taskOptionsLoaded || taskOptionsLoading) return;
    setTaskOptionsLoading(true);
    (async () => {
      const limit = 200;
      const first = await fetchTaskTitleOptions({ limit });
      const pages = [first.tasks];
      for (let offset = first.tasks.length; offset < first.total; offset += limit) {
        const next = await fetchTaskTitleOptions({ limit, offset });
        pages.push(next.tasks);
        if (next.tasks.length === 0) break;
      }
      return pages.flat();
    })()
      .then((allTasks) => {
        setTasks(allTasks);
        setTaskOptionsLoaded(true);
      })
      .catch(() => {
        setTasks([]);
        setTaskOptionsLoaded(true);
      })
      .finally(() => setTaskOptionsLoading(false));
  }, [taskOptionsLoaded, taskOptionsLoading]);

  const loadArtifacts = useCallback(() => {
    const requestedSourceTaskId = filters.sourceTaskId;
    if (requestedSourceTaskId !== routeSourceTaskId) return;
    const requestGeneration = artifactListRequestGenerationRef.current + 1;
    artifactListRequestGenerationRef.current = requestGeneration;
    const isCurrentRequest = () =>
      artifactListRequestGenerationRef.current === requestGeneration
      && activeSourceTaskIdRef.current === requestedSourceTaskId;
    setLoading(true);
    setError(null);
    fetchArtifacts({
      q: filters.q || undefined,
      kind: filters.kind || undefined,
      origin: filters.origin || undefined,
      format: filters.format || undefined,
      sourceTaskId: filters.sourceTaskId || undefined,
      searchInline: filters.searchInline || undefined,
      page,
      pageSize,
    })
      .then((res) => {
        if (!isCurrentRequest()) return;
        if (
          requestedSourceTaskId
          && res.artifacts.some((artifact) => artifact.sourceTaskId !== requestedSourceTaskId)
        ) {
          setArtifacts([]);
          setTotal(0);
          setError("返回的产物不属于当前项目，已停止显示。");
          return;
        }
        setArtifacts(res.artifacts);
        setTotal(res.total);
      })
      .catch((err: unknown) => {
        if (!isCurrentRequest()) return;
        setError(userFacingErrorText(err instanceof Error ? err.message : String(err), "产物暂时无法加载，请稍后重试。"));
        setArtifacts([]);
        setTotal(0);
      })
      .finally(() => {
        if (isCurrentRequest()) setLoading(false);
      });
  }, [filters, page, pageSize, routeSourceTaskId]);

  useEffect(() => {
    loadArtifacts();
  }, [loadArtifacts]);

  const openPreview = useCallback((artifact: CatalogArtifact, syncUrl = true) => {
    const requestGeneration = previewRequestGenerationRef.current + 1;
    previewRequestGenerationRef.current = requestGeneration;
    const isCurrentRequest = () =>
      previewRequestGenerationRef.current === requestGeneration
      && (!activeSourceTaskIdRef.current || activeSourceTaskIdRef.current === artifact.sourceTaskId);
    setPreviewArtifact(artifact);
    setPreviewContent(null);
    setPreviewUnavailable(null);
    setPreviewRelations([]);
    setPreviewLoading(true);
    if (syncUrl) {
      const next = new URLSearchParams(searchParams);
      next.set("artifactId", artifact.id);
      setSearchParams(next, { replace: true });
    }
    fetchArtifactContentById(artifact.id)
      .then((content) => {
        if (!isCurrentRequest()) return;
        if (content === null) setPreviewUnavailable("not_found");
        else setPreviewContent(content);
      })
      .catch(() => {
        if (isCurrentRequest()) setPreviewUnavailable("transient");
      })
      .finally(() => {
        if (isCurrentRequest()) setPreviewLoading(false);
      });
    fetchArtifactRelations(artifact.id)
      .then((relations) => {
        if (isCurrentRequest()) setPreviewRelations(relations);
      })
      .catch(() => {
        if (isCurrentRequest()) setPreviewRelations([]);
      });
  }, [searchParams, setSearchParams]);

  const closePreview = useCallback(() => {
    previewRequestGenerationRef.current += 1;
    setPreviewArtifact(null);
    setPreviewContent(null);
    setPreviewUnavailable(null);
    setPreviewRelations([]);
    const next = new URLSearchParams(searchParams);
    next.delete("artifactId");
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  useEffect(() => {
    const artifactId = searchParams.get("artifactId")?.trim();
    if (!artifactId) return;
    const sourceTaskId = searchParams.get("sourceTaskId")?.trim();
    if (
      previewArtifact?.id === artifactId &&
      (!sourceTaskId || previewArtifact.sourceTaskId === sourceTaskId)
    ) return;
    let cancelled = false;
    if (previewArtifact && sourceTaskId && previewArtifact.sourceTaskId !== sourceTaskId) {
      window.queueMicrotask(() => {
        if (cancelled) return;
        setPreviewArtifact(null);
        setPreviewContent(null);
        setPreviewUnavailable(null);
        setPreviewRelations([]);
        setPreviewLoading(false);
      });
    }
    fetchArtifactDetail(artifactId)
      .then((artifact) => {
        if (cancelled || !artifact) return;
        if (sourceTaskId && artifact.sourceTaskId !== sourceTaskId) {
          const next = new URLSearchParams(searchParams);
          next.delete("artifactId");
          setSearchParams(next, { replace: true });
          return;
        }
        openPreview(artifact, false);
      })
      .catch(() => {
        if (cancelled) return;
        const next = new URLSearchParams(searchParams);
        next.delete("artifactId");
        setSearchParams(next, { replace: true });
      });
    return () => {
      cancelled = true;
    };
  }, [openPreview, previewArtifact, searchParams, setSearchParams]);

  const handleReviewArtifact = useCallback((decision: "approved" | "rejected" | "needs_revision") => {
    if (!previewArtifact) return;
    setReviewingArtifact(true);
    reviewArtifact(
      previewArtifact.id,
      decision,
      decision === "approved" ? "User confirmed this artifact for formal use." : "User requested revision before formal use.",
    )
      .then((updated) => {
        setPreviewArtifact((current) => (current?.id === updated.id ? { ...current, ...updated } : current));
        setArtifacts((rows) => rows.map((row) => (row.id === updated.id ? { ...row, ...updated } : row)));
        if (canonicalStateConfirmsReview(updated, decision)) {
          message.success(decision === "approved" ? "已标记为核查通过" : "已标记为需要修订");
        } else {
          message.warning("服务器尚未确认核查状态，请刷新后重试。");
        }
      })
      .catch((err: unknown) => {
        message.error(userFacingErrorText(err instanceof Error ? err.message : "", "核查状态更新失败，请稍后重试。"));
      })
      .finally(() => setReviewingArtifact(false));
  }, [previewArtifact]);

  const handleConfirmScopeExpansion = useCallback(() => {
    if (!previewArtifact) return;
    setReviewingArtifact(true);
    confirmArtifactScopeExpansion(
      previewArtifact.id,
      "User confirmed the expanded research scope.",
    )
      .then((updated) => {
        setPreviewArtifact((current) => (current?.id === updated.id ? { ...current, ...updated } : current));
        setArtifacts((rows) => rows.map((row) => (row.id === updated.id ? { ...row, ...updated } : row)));
        const updatedGate = artifactHumanGate(catalogArtifactForWorkbench(updated));
        if (updatedGate.state === "needs_input" && !updatedGate.requiresScopeExpansionConfirmation) {
          message.success("已确认扩大研究范围");
        } else {
          message.warning("服务器尚未确认范围状态，请刷新后重试。");
        }
      })
      .catch((err: unknown) => {
        message.error(userFacingErrorText(err instanceof Error ? err.message : "", "范围确认失败，请稍后重试。"));
      })
      .finally(() => setReviewingArtifact(false));
  }, [previewArtifact]);

  const confirmReuse = useCallback(() => {
    if (!reuseArtifactId || !reuseTarget) return;
    const source = artifacts.find((row) => row.id === reuseArtifactId);
    if (source && !artifactHumanGate(catalogArtifactForWorkbench(source)).canUseDownstream) {
      message.error("当前产物还不能复用，请先完成必要的核查或补充。 ".trim());
      setReuseArtifactId(null);
      setReuseTarget(null);
      return;
    }
    setReusing(true);
    reuseArtifact(reuseArtifactId, reuseTarget)
      .then(() => {
        message.success("已复用到所选项目");
        setReuseArtifactId(null);
        setReuseTarget(null);
        loadArtifacts();
      })
      .catch((err: unknown) => {
        message.error(userFacingErrorText(err instanceof Error ? err.message : "", "复用失败，请稍后重试。"));
      })
      .finally(() => setReusing(false));
  }, [artifacts, reuseArtifactId, reuseTarget, loadArtifacts]);

  const confirmUpload = useCallback(() => {
    if (!uploadTarget || !uploadFile) return;
    const currentTaskId = readCurrentTask();
    const runUpload = (ownershipConfirmed = false) =>
      uploadArtifact(uploadTarget, uploadFile, {
        currentTaskId,
        ownershipConfirmed,
      });
    setUploading(true);
    runUpload()
      .then(() => {
        message.success("上传成功");
        setUploadOpen(false);
        setUploadTarget(null);
        setUploadFile(null);
        setPage(1);
        loadArtifacts();
      })
      .catch((err: unknown) => {
        if (isOwnershipConfirmationError(err)) {
          Modal.confirm({
            title: "确认上传位置",
            content: "该文件将上传到与当前研究不同的项目。确认继续吗？",
            okText: "继续上传",
            cancelText: "取消",
            onOk: () =>
              runUpload(true)
                .then(() => {
                  message.success("上传成功");
                  setUploadOpen(false);
                  setUploadTarget(null);
                  setUploadFile(null);
                  setPage(1);
                  loadArtifacts();
                })
                .catch((confirmErr: unknown) => {
                  message.error(userFacingErrorText(confirmErr instanceof Error ? confirmErr.message : "", "上传失败，请稍后重试。"));
                  throw confirmErr;
                }),
          });
          return;
        }
        message.error(userFacingErrorText(err instanceof Error ? err.message : "", "上传失败，请稍后重试。"));
      })
      .finally(() => setUploading(false));
  }, [uploadTarget, uploadFile, loadArtifacts]);

  const taskOptions = useMemo(() => {
    const byId = new Map<string, string>();
    for (const artifact of artifacts) {
      const id = artifact.sourceTaskId?.trim();
      if (!id) continue;
      if (!byId.has(id)) {
        byId.set(id, artifact.sourceTaskTitle?.trim() || "未命名项目");
      }
    }
    for (const task of tasks) {
      byId.set(task.taskId, task.title || "未命名项目");
    }
    if (filters.sourceTaskId && !byId.has(filters.sourceTaskId)) {
      byId.set(filters.sourceTaskId, filters.sourceTaskId);
    }
    return [
      { value: "", label: "所属项目：全部" },
      ...Array.from(byId.entries()).map(([value, label]) => ({ value, label })),
    ];
  }, [artifacts, tasks, filters.sourceTaskId]);

  const pickerOptions = useMemo(
    () => tasks.map((t) => ({ value: t.taskId, label: t.title || "未命名项目" })),
    [tasks],
  );

  const columns: ColumnsType<CatalogArtifact> = [
    {
      title: "产物标题",
      dataIndex: "title",
      key: "title",
      width: 240,
      ellipsis: true,
      onCell: () => ({ className: "max-w-[240px] overflow-hidden!" }),
      render: (_: string, record) => (
        <Button
          type="link"
          className="h-auto! max-w-full! p-0! text-left!"
          title={record.title}
          onClick={() => openPreview(record)}
        >
          <span className="block max-w-full truncate">{record.title}</span>
        </Button>
      ),
    },
    {
      title: "所属项目",
      dataIndex: "sourceTaskTitle",
      key: "sourceTaskTitle",
      width: 180,
      ellipsis: true,
      onCell: () => ({ className: "max-w-[180px] overflow-hidden!" }),
      render: (v: string) => (
        <Typography.Text className="block max-w-full" ellipsis={{ tooltip: v || "未命名项目" }}>
          {v || "未命名项目"}
        </Typography.Text>
      ),
    },
    {
      title: "产物类型",
      dataIndex: "kind",
      key: "kind",
      width: 120,
      render: (v: string) => <Tag>{artifactTypeLabel(v || "")}</Tag>,
    },
    {
      title: "当前状态",
      key: "userStatus",
      width: 110,
      render: (_, record) => {
        const status = artifactUserStatus(record);
        return <Tag color={status.color}>{status.label}</Tag>;
      },
    },
    {
      title: "最近更新时间",
      dataIndex: "createdAt",
      key: "createdAt",
      width: 160,
      render: (v: string) => formatArtifactTime(v),
    },
    {
      title: "操作",
      key: "actions",
      width: 140,
      render: (_: unknown, record) => (
        <div className="-ml-[8px]">
          <Button size="small" type="link" onClick={() => openPreview(record)}>
            查看
          </Button>
          <Button
            size="small"
            type="link"
            onClick={() => {
              rememberCurrentTask(record.sourceTaskId);
              rememberCurrentArtifact(record.id);
              navigate(`/workbench/${record.sourceTaskId}`);
            }}
          >
            进入工作台
          </Button>
        </div>
      ),
    },
  ];

  const previewWorkbenchArtifact: WorkbenchArtifact | null = previewArtifact
    ? {
        ...catalogArtifactForWorkbench(previewArtifact),
        content: previewContent?.content ?? null,
        sourceMode: previewContent?.sourceMode,
        evidenceRefs: previewContent?.evidenceRefs,
      }
    : null;
  const previewGate = previewWorkbenchArtifact ? artifactHumanGate(previewWorkbenchArtifact) : null;

  return (
    <div className="pt-0! h-full min-h-0">
      <div className="flex h-full min-h-0 flex-col gap-6 rounded-xl bg-surface px-6 pt-6">
        <div className="flex-none">
          <Space wrap size="middle">
            <Input.Search
              placeholder="搜索标题 / 摘要"
              allowClear
              className="w-60"
              value={filters.q}
              onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value }))}
              onSearch={() => setPage(1)}
            />
            <Select
              className="w-[150px]"
              value={filters.origin}
              options={ORIGIN_OPTIONS}
              onChange={(v) => {
                setFilters((f) => ({ ...f, origin: v }));
                setPage(1);
              }}
            />
            <Select
              className="w-[220px]"
              value={filters.sourceTaskId}
              options={taskOptions}
              loading={taskOptionsLoading}
              onOpenChange={(open) => {
                if (open) ensureTaskOptions();
              }}
              onChange={(v) => {
                setFilters((f) => ({ ...f, sourceTaskId: v }));
                setPage(1);
                const next = new URLSearchParams(searchParams);
                if (v) next.set("sourceTaskId", v);
                else next.delete("sourceTaskId");
                next.delete("artifactId");
                setSearchParams(next, { replace: true });
              }}
            />
            <Button
              icon={<ReloadOutlined />}
              onClick={() => {
                setFilters(EMPTY_FILTERS);
                setPage(1);
                const next = new URLSearchParams(searchParams);
                next.delete("sourceTaskId");
                next.delete("artifactId");
                setSearchParams(next, { replace: true });
                setPreviewArtifact(null);
                setPreviewContent(null);
                setPreviewUnavailable(null);
                setPreviewRelations([]);
              }}
            >
              重置
            </Button>
            <Button
              type="primary"
              icon={<UploadOutlined />}
              onClick={() => {
                ensureTaskOptions();
                setUploadTarget(null);
                setUploadFile(null);
                setUploadOpen(true);
              }}
            >
              上传
            </Button>
          </Space>
        </div>

        {error ? <Alert type="error" showIcon message={`加载失败：${error}`} /> : null}

        <div className="vertical-scroll-table min-h-0 flex-1">
          <Table<CatalogArtifact>
            rowKey="id"
            columns={columns}
            dataSource={artifacts}
            loading={loading}
            tableLayout="fixed"
            scroll={{ y: "calc(100% - 55px)" }}
            onRow={(record) => ({ "data-testid": `artifact-library-row-${record.sourceTaskId}-${record.id}` } as HTMLAttributes<HTMLTableRowElement>)}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无产物" /> }}
            pagination={{
              current: page,
              pageSize,
              total,
              position: ["bottomRight"],
              showSizeChanger: true,
              pageSizeOptions: [10, 20, 50],
              showTotal: (t) => `共 ${t} 条`,
              onChange: (p, ps) => {
                setPage(p);
                setPageSize(ps);
              },
            }}
          />
        </div>

        <Drawer
          title={previewArtifact?.title ?? "产物预览"}
          open={previewArtifact !== null}
          onClose={closePreview}
          width="60%"
          destroyOnClose
          extra={
            previewArtifact ? (
              <Space>
                <Button
                  size="small"
                  icon={<EditOutlined />}
                  disabled={!isPreviewEditable}
                  onClick={() => setVersionDrawerOpen(true)}
                  title={isPreviewEditable ? "编辑产物（生成新版本）" : "仅 Markdown/文本产物可编辑"}
                >
                  编辑
                </Button>
                <Button
                  size="small"
                  icon={<HistoryOutlined />}
                  onClick={() => setVersionDrawerOpen(true)}
                  title="版本历史"
                />
                {previewGate?.canConfirmPass && !isResearchStartDraftArtifact(previewWorkbenchArtifact) ? (
                  <Button
                    type="primary"
                    loading={reviewingArtifact}
                    onClick={() => handleReviewArtifact("approved")}
                  >
                    核查通过
                  </Button>
                ) : null}
                {previewGate?.canRequestRevision && !isResearchStartDraftArtifact(previewWorkbenchArtifact) ? (
                  <Button
                    danger
                    loading={reviewingArtifact}
                    onClick={() => handleReviewArtifact("needs_revision")}
                  >
                    退回修改
                  </Button>
                ) : null}
                {previewGate?.requiresScopeExpansionConfirmation ? (
                  <Button
                    type="primary"
                    loading={reviewingArtifact}
                    onClick={handleConfirmScopeExpansion}
                  >
                    确认扩大范围
                  </Button>
                ) : null}
                <Button
                  data-testid={`artifact-library-reuse-${previewArtifact.id}`}
                  style={{ display: previewGate?.canUseDownstream ? undefined : "none" }}
                  disabled={!previewGate?.canUseDownstream}
                  onClick={() => {
                    ensureTaskOptions();
                    setReuseArtifactId(previewArtifact.id);
                  }}
                >
                  复用到项目
                </Button>
                <Button
                  onClick={() => {
                    rememberCurrentTask(previewArtifact.sourceTaskId);
                    rememberCurrentArtifact(previewArtifact.id);
                    navigate(`/workbench/${previewArtifact.sourceTaskId}`);
                  }}
                >
                  进入工作台
                </Button>
              </Space>
            ) : null
          }
        >
          {previewArtifact ? (
            <Alert
              style={{ marginBottom: 12 }}
              showIcon
              type={
                previewGate?.state === "usable"
                  ? "success"
                  : previewArtifact.artifactState === "rejected_or_needs_revision"
                    ? "error"
                    : previewGate?.requiresHumanReview
                      ? "warning"
                      : "info"
              }
              message={`产物状态：${artifactUserStatus(previewArtifact).label}`}
            />
          ) : null}
          {previewArtifact?.reusedFrom ? (
            <Typography.Text type="secondary" className="mb-3 block">
              来源产物：{previewArtifact.reusedFrom.taskTitle || "未命名项目"} / {previewArtifact.reusedFrom.artifactTitle || "未命名产物"}
            </Typography.Text>
          ) : null}
          {previewWorkbenchArtifact ? (
            <ArtifactRenderer
              artifact={previewWorkbenchArtifact}
              taskId={previewArtifact?.sourceTaskId}
              loading={previewLoading}
              unavailableReason={previewUnavailable ?? undefined}
              onRetry={previewArtifact ? () => openPreview(previewArtifact) : undefined}
            />
          ) : null}

          {/* 关联产物 */}
          <div className="mt-4 border-t border-border pt-3">
            <Typography.Text strong>关联产物</Typography.Text>
            {previewRelations.length === 0 ? (
              <Typography.Text type="secondary" className="mt-1 block">
                暂无关联产物
              </Typography.Text>
            ) : (
              <Space direction="vertical" size={6} className="mt-2 w-full">
                {previewRelations.map((r, i) => (
                  <Space key={`${r.artifactId}-${r.relationType}-${i}`} size={6} wrap>
                    <Tag color={r.direction === "outgoing" ? "processing" : "default"}>
                      {r.direction === "outgoing" ? "可追溯" : "被继续使用"}
                    </Tag>
                    <Typography.Text>{r.title || "关联产物"}</Typography.Text>
                    {r.kind ? <Tag>{artifactTypeLabel(r.kind)}</Tag> : null}
                  </Space>
                ))}
              </Space>
            )}
          </div>
        </Drawer>

        <ArtifactVersionDrawer
          open={versionDrawerOpen}
          artifactId={previewArtifact?.id ?? null}
          artifactTitle={previewArtifact?.title}
          editable={isPreviewEditable}
          canRegenerate={canPreviewRegenerate}
          onClose={() => setVersionDrawerOpen(false)}
          onChanged={() => {
            loadArtifacts();
            if (previewArtifact) openPreview(previewArtifact);
          }}
        />

        <Modal
          title="复用产物到项目"
          open={reuseArtifactId !== null}
          onOk={confirmReuse}
          onCancel={() => {
            setReuseArtifactId(null);
            setReuseTarget(null);
          }}
          confirmLoading={reusing}
          okButtonProps={{ disabled: !reuseTarget }}
        >
          <Typography.Paragraph type="secondary">在所选项目中保留一份可继续查看和复核的材料副本。</Typography.Paragraph>
          <Select
            showSearch
            placeholder="选择目标项目"
            className="w-full"
            value={reuseTarget}
            options={pickerOptions}
            loading={taskOptionsLoading}
            onOpenChange={(open) => {
              if (open) ensureTaskOptions();
            }}
            onChange={setReuseTarget}
            optionFilterProp="label"
          />
        </Modal>

        <Modal
          title="上传产物到项目"
          open={uploadOpen}
          onOk={confirmUpload}
          onCancel={() => setUploadOpen(false)}
          confirmLoading={uploading}
          okButtonProps={{ disabled: !uploadTarget || !uploadFile }}
        >
          <Typography.Paragraph type="secondary">
            选择目标项目并上传文件。
          </Typography.Paragraph>
          <div className="flex flex-col gap-3">
            <Select
              showSearch
              placeholder="选择目标项目"
              className="w-full"
              value={uploadTarget}
              options={pickerOptions}
              loading={taskOptionsLoading}
              onOpenChange={(open) => {
                if (open) ensureTaskOptions();
              }}
              onChange={setUploadTarget}
              optionFilterProp="label"
            />
            <input type="file" aria-label="选择文件" title="选择文件" onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)} />
          </div>
        </Modal>
      </div>
    </div>
  );
}
