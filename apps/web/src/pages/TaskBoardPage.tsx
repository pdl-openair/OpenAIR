import { useEffect, useMemo, useRef, useState, type UIEvent } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Radio,
  Space,
  Table,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { EditOutlined, FileTextOutlined } from "@ant-design/icons";
import type { Task, TaskStage } from "@openair/shared";
import { stageDisplayName } from "@openair/shared";
import { cloneTask, deleteTask, renameTask } from "../api/client";
import {
  boardStatusPresentation,
  buildBoardRowsFromTasks,
  filterBoardRowsForView,
  type BoardTaskRow,
} from "../data/boardRows";
import { formatRelativeTime } from "../lib/relativeTime";
import {
  clearResearchStartPendingForTask,
  rememberCurrentTask,
} from "../lib/userRouteState";
import { FixtureDisclosureAlert } from "../components/FixtureDisclosureAlert";
import { cn } from "../lib/cn";
import { useTaskBoardList } from "../hooks/useTaskBoardList";
import { classifyApiFailure } from "../api/http";
import {
  acceptRenamedTask,
  createRenameSubmitGate,
  optimisticallyRenameTask,
  rollbackTaskTitle,
  type RenameSubmitGate,
} from "../lib/taskBoardState";

const DEFAULT_BOARD_PAGE_SIZE = 20;
const BOARD_PAGE_SIZE_OPTIONS = ["10", "20", "50"];

const MAX_BOARD_ARTIFACT_LINKS = 2;
const EMPTY_TASKS: Task[] = [];
const EMPTY_STATUS_TOTALS: Record<string, number> = {};

type BoardStatusFilter = "all" | "running" | "completed" | "failed";

const UNDERLYING_STAGES: readonly TaskStage[] = [
  "problem_modeling",
  "solution_design",
  "execution_validation",
  "insight_generation",
];

function boardStatusBucket(task: Task): Exclude<BoardStatusFilter, "all"> {
  const status = boardStatusPresentation(task).status;
  if (status === "completed") return "completed";
  if (status === "failed") return "failed";
  return "running";
}

function statusClass(status: string): string {
  return cn(
    "inline-block rounded px-2.5 py-0.5 text-xs font-medium leading-5",
    status === "running" && "bg-info-soft text-accent-strong",
    (status === "awaiting_confirmation" ||
      status === "needs_input" ||
      status === "external_result_unknown") &&
      "bg-warning-soft text-warning",
    status === "completed" && "bg-success-soft text-success",
    status === "failed" && "bg-danger-soft text-danger",
    status !== "running" &&
      status !== "awaiting_confirmation" &&
      status !== "needs_input" &&
      status !== "external_result_unknown" &&
      status !== "completed" &&
      status !== "failed" &&
      "bg-surface-muted text-text-secondary",
  );
}

/** Project-facing stage names for Board; remap shared display names only. */
function projectFacingStageLabel(currentStage: Task["currentStage"]): string {
  const stage = (UNDERLYING_STAGES as readonly string[]).includes(currentStage)
    ? (currentStage as TaskStage)
    : "problem_modeling";
  const shared = stageDisplayName(stage);
  if (shared === "问题定义") return "问题建模";
  if (shared === "结果总结") return "洞察生成";
  return shared;
}

export default function TaskBoardPage() {
  const navigate = useNavigate();
  const [editingTaskId, setEditingTaskId] = useState<string | null>(null);
  const [editingTitleDraft, setEditingTitleDraft] = useState("");
  const renameRequestsRef = useRef(new Map<string, Promise<boolean>>());
  const lastCommittedTitlesRef = useRef(new Map<string, string>());
  const [statusFilter, setStatusFilter] = useState<BoardStatusFilter>("all");
  const renameSessionRef = useRef<{
    taskId: string;
    token: number;
    gate: RenameSubmitGate;
  } | null>(null);
  const renameTokenRef = useRef(0);
  const [cloneSource, setCloneSource] = useState<{
    taskId: string;
    title: string;
  } | null>(null);
  const [cloneIncludeArtifacts, setCloneIncludeArtifacts] = useState(true);
  const [cloneTitleDraft, setCloneTitleDraft] = useState("");
  const [cloning, setCloning] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_BOARD_PAGE_SIZE);
  const summaryKey = `${page}:${pageSize}:${statusFilter}`;
  const [summaryState, setSummaryState] = useState({
    key: summaryKey,
    collapsed: false,
  });
  const statusSummaryCollapsed =
    summaryState.key === summaryKey && summaryState.collapsed;
  const setStatusSummaryCollapsed = (collapsed: boolean) =>
    setSummaryState({ key: summaryKey, collapsed });
  const lastTableScrollTopRef = useRef(0);
  const statusSummaryWrapRef = useRef<HTMLDivElement>(null);
  const statusSummaryHeightRef = useRef(0);
  const tableScrollLayoutLockUntilRef = useRef(0);

  const list = useTaskBoardList(page, pageSize, statusFilter);
  const { loading, setTasks, setTotal: setListTotal } = list;
  const tasks = list.data?.tasks ?? EMPTY_TASKS;
  const listTotal = list.data?.total ?? 0;
  const statusTotals = list.data?.statusTotals ?? EMPTY_STATUS_TOTALS;
  const fixtureReason = list.data?.tasks.length === 0 ? "empty_tasks" : null;
  const loadError = list.data ? null : list.error;

  useEffect(() => {
    lastTableScrollTopRef.current = 0;
  }, [page, pageSize, statusFilter]);

  const handleTableScroll = (event: UIEvent<HTMLDivElement>) => {
    const body = event.currentTarget;
    const top = body.scrollTop;
    const delta = top - lastTableScrollTopRef.current;
    lastTableScrollTopRef.current = top;
    if (Date.now() < tableScrollLayoutLockUntilRef.current) return;
    if (top <= 8) {
      setStatusSummaryCollapsed(false);
      return;
    }
    if (delta > 6) {
      const summary = statusSummaryWrapRef.current;
      const measured = summary?.getBoundingClientRect().height ?? 0;
      if (measured > 0) statusSummaryHeightRef.current = measured + 20;
      const overflow = body.scrollHeight - body.clientHeight;
      // Hiding the summary grows the table. If leftover overflow cannot cover
      // that height, scrollTop snaps to 0 and the cards bounce back.
      if (overflow <= statusSummaryHeightRef.current + 8) return;
      tableScrollLayoutLockUntilRef.current = Date.now() + 220;
      setStatusSummaryCollapsed(true);
      return;
    }
    if (delta < -6) {
      tableScrollLayoutLockUntilRef.current = Date.now() + 220;
      setStatusSummaryCollapsed(false);
    }
  };

  const reloadTasks = list.reload;

  const mutationErrorMessage = (error: unknown, action: "删除" | "重命名") => {
    switch (classifyApiFailure(error)) {
      case "unauthorized":
        return "会话已失效，请重新登录。";
      case "forbidden":
        return `你无权${action}该项目。`;
      case "not_found":
        return "项目已不存在，请刷新项目列表。";
      default:
        return `${action}失败，请稍后重试。`;
    }
  };

  const handleDeleteTask = async (taskId: string) => {
    try {
      await deleteTask(taskId);
      list.invalidate();
      clearResearchStartPendingForTask(taskId);
      message.success("项目已删除");
      const remainingOnPage = tasks.filter(
        (task) => task.taskId !== taskId,
      ).length;
      const nextTotal = Math.max(0, listTotal - 1);
      setListTotal(nextTotal);
      if (remainingOnPage === 0 && page > 1) {
        setPage((prev) => Math.max(1, prev - 1));
        return;
      }
      setTasks((prev) => prev.filter((task) => task.taskId !== taskId));
    } catch (error) {
      message.error(mutationErrorMessage(error, "删除"));
    }
  };

  const openCloneModal = (task: Task) => {
    setCloneSource({ taskId: task.taskId, title: task.title ?? "" });
    setCloneIncludeArtifacts(true);
    setCloneTitleDraft("");
  };

  const closeCloneModal = () => {
    if (cloning) return;
    setCloneSource(null);
    setCloneTitleDraft("");
  };

  const handleCloneTask = async () => {
    if (!cloneSource) return;
    const sourceTaskId = cloneSource.taskId;
    const trimmedTitle = cloneTitleDraft.trim();
    setCloning(true);
    try {
      const created = await cloneTask(sourceTaskId, {
        includeArtifacts: cloneIncludeArtifacts,
        title: trimmedTitle || undefined,
      });
      list.invalidate();
      message.success(
        cloneIncludeArtifacts ? "已克隆项目" : "已从模板创建项目",
      );
      setCloneSource(null);
      setCloneTitleDraft("");
      rememberCurrentTask(created.taskId);
      navigate(`/tasks/${encodeURIComponent(created.taskId)}`);
    } catch (error) {
      switch (classifyApiFailure(error)) {
        case "unauthorized":
          message.error("会话已失效，请重新登录。");
          break;
        case "forbidden":
          message.error("你无权克隆该项目。");
          break;
        case "not_found":
          message.error("源项目已不存在，请刷新项目列表。");
          break;
        default:
          message.error("克隆失败，请稍后重试。");
      }
    } finally {
      setCloning(false);
    }
  };

  const handleBoardRename = async (taskId: string, newTitle: string) => {
    const existing = renameRequestsRef.current.get(taskId);
    if (existing) return existing;
    const trimmed = newTitle.trim();
    if (!trimmed) {
      message.error("标题不能为空");
      return false;
    }
    const current = tasks.find((task) => task.taskId === taskId);
    if (
      !current ||
      trimmed === current.title ||
      trimmed === lastCommittedTitlesRef.current.get(taskId)
    )
      return true;
    const previousTitle = current.title;
    setTasks((prev) => optimisticallyRenameTask(prev, taskId, trimmed));
    try {
      const returnedTask = await renameTask(taskId, trimmed);
      list.invalidate();
      lastCommittedTitlesRef.current.set(taskId, trimmed);
      setTasks((prev) => acceptRenamedTask(prev, returnedTask));
      return true;
    } catch (error) {
      setTasks((prev) => rollbackTaskTitle(prev, taskId, previousTitle));
      message.error(mutationErrorMessage(error, "重命名"));
      return false;
    }
  };

  const commitBoardRename = async (
    session: { taskId: string; token: number; gate: RenameSubmitGate },
    draft: string,
  ) => {
    const isCurrent = () =>
      renameSessionRef.current?.token === session.token &&
      renameSessionRef.current.taskId === session.taskId;
    if (!isCurrent() || !session.gate.tryStart()) return;
    const ok = await handleBoardRename(session.taskId, draft);
    if (!isCurrent()) return;
    if (ok) {
      renameSessionRef.current = null;
      setEditingTaskId(null);
      return;
    }
    session.gate.reset();
  };

  const rows = useMemo(() => buildBoardRowsFromTasks(tasks, false), [tasks]);
  const baseRows = useMemo(
    () => filterBoardRowsForView(rows, "all", "all", "all"),
    [rows],
  );
  const statusCounts = useMemo(
    () => ({
      all: Object.values(statusTotals).reduce(
        (total, count) => total + count,
        0,
      ),
      running:
        (statusTotals.draft ?? 0) +
        (statusTotals.not_started ?? 0) +
        (statusTotals.running ?? 0) +
        (statusTotals.awaiting_confirmation ?? 0) +
        (statusTotals.cancelled ?? 0),
      completed: statusTotals.completed ?? 0,
      failed: statusTotals.failed ?? 0,
    }),
    [statusTotals],
  );
  const visibleRows = useMemo(
    () =>
      statusFilter === "all"
        ? baseRows
        : baseRows.filter(
            (row) => boardStatusBucket(row.task) === statusFilter,
          ),
    [baseRows, statusFilter],
  );

  const statusSummary: Array<{
    key: BoardStatusFilter;
    label: string;
    count: number;
    countClass: string;
    selectedClass: string;
    barClass: string;
  }> = [
    {
      key: "all",
      label: "全部项目",
      count: statusCounts.all,
      countClass: "text-text-secondary",
      selectedClass: "border-border-strong bg-surface-muted shadow-sm",
      barClass: "bg-text-muted",
    },
    {
      key: "running",
      label: "进行中项目",
      count: statusCounts.running,
      countClass: "text-accent-strong",
      selectedClass: "border-info-border bg-info-soft shadow-sm",
      barClass: "bg-accent-strong",
    },
    {
      key: "completed",
      label: "已完成项目",
      count: statusCounts.completed,
      countClass: "text-success",
      selectedClass: "border-success bg-success-soft shadow-sm",
      barClass: "bg-success",
    },
    {
      key: "failed",
      label: "已失败项目",
      count: statusCounts.failed,
      countClass: "text-danger",
      selectedClass: "border-danger bg-danger-soft shadow-sm",
      barClass: "bg-danger",
    },
  ];

  const columns: ColumnsType<BoardTaskRow> = [
    {
      title: "项目",
      key: "project",
      render: (_, row) => {
        const isEditing = editingTaskId === row.task.taskId;
        const goal = row.task.goalSummary?.trim() ?? "";
        const editSession = renameSessionRef.current;
        return (
          <div className="min-w-0">
            {isEditing && editSession?.taskId === row.task.taskId ? (
              <Input
                size="small"
                value={editingTitleDraft}
                onChange={(event) => {
                  if (renameSessionRef.current?.token === editSession.token) {
                    setEditingTitleDraft(event.target.value);
                  }
                }}
                onPressEnter={(event) => {
                  event.preventDefault();
                  void commitBoardRename(
                    editSession,
                    event.currentTarget.value,
                  );
                }}
                onKeyDown={(event) => {
                  if (event.key !== "Escape") return;
                  event.preventDefault();
                  if (renameSessionRef.current?.token !== editSession.token)
                    return;
                  editSession.gate.cancel();
                  renameSessionRef.current = null;
                  setEditingTaskId(null);
                }}
                onBlur={(event) =>
                  void commitBoardRename(editSession, event.currentTarget.value)
                }
                autoFocus
                maxLength={200}
                className="text-sm"
              />
            ) : (
              <Space size={4} className="max-w-full">
                <Typography.Text
                  strong
                  className="truncate text-sm block"
                  title={row.task.title}
                >
                  {row.task.title}
                </Typography.Text>
                <EditOutlined
                  aria-label={`重命名项目 ${row.task.title}`}
                  className="cursor-pointer shrink-0 text-xs text-text-muted"
                  onClick={(event) => {
                    event.stopPropagation();
                    renameSessionRef.current = {
                      taskId: row.task.taskId,
                      token: ++renameTokenRef.current,
                      gate: createRenameSubmitGate(),
                    };
                    setEditingTaskId(row.task.taskId);
                    setEditingTitleDraft(row.task.title);
                  }}
                />
              </Space>
            )}
            {goal ? (
              <Typography.Text
                type="secondary"
                className="mt-1 block! text-xs leading-5"
                ellipsis={{ tooltip: goal }}
              >
                {goal}
              </Typography.Text>
            ) : null}
          </div>
        );
      },
    },
    {
      title: "进展",
      key: "progress",
      width: "16%",
      render: (_, row) => {
        const status = boardStatusPresentation(row.task);
        return (
          <div className="flex flex-col items-start gap-1.5">
            <span className="text-sm font-medium text-text">
              {projectFacingStageLabel(row.task.currentStage)}
            </span>
            <span
              className={statusClass(status.status)}
              data-execution-status={status.status}
            >
              {status.label}
            </span>
          </div>
        );
      },
    },
    {
      title: "产物",
      key: "artifacts",
      width: "8%",
      align: "center",
      render: (_, row) => {
        const summary = row.task.catalogArtifacts;
        const fallbackArtifacts = row.task.artifacts ?? [];
        // Catalog (artifact library) is the source of truth — use its items
        // for the icon links + its count for the "+N" overflow. Fall back to
        // the payload mirror only if the backend didn't supply a summary.
        const count = summary?.count ?? fallbackArtifacts.length;
        const items =
          summary?.items ??
          fallbackArtifacts.slice(0, MAX_BOARD_ARTIFACT_LINKS).map((a) => ({
            artifactId: a.id,
            title: a.title,
          }));
        const visible = items.slice(0, MAX_BOARD_ARTIFACT_LINKS);
        const hiddenCount = Math.max(0, count - visible.length);
        if (count === 0) return <span className="text-text-muted">—</span>;
        return (
          <div className="flex items-center justify-center gap-0.5 whitespace-nowrap">
            {visible.map((item) => (
              <Button
                key={item.artifactId}
                type="text"
                size="small"
                icon={<FileTextOutlined />}
                className="h-7! w-7! min-w-7! p-0! text-accent-strong"
                title={item.title || "查看产物"}
                aria-label={`查看产物 ${item.title || item.artifactId}`}
                onClick={() => {
                  rememberCurrentTask(row.task.taskId);
                  navigate(
                    `/artifacts?sourceTaskId=${encodeURIComponent(row.task.taskId)}&artifactId=${encodeURIComponent(item.artifactId)}`,
                  );
                }}
              />
            ))}
            {hiddenCount > 0 ? (
              <Button
                type="link"
                size="small"
                className="h-7! min-w-0! px-1! text-xs!"
                title={`还有 ${hiddenCount} 个产物`}
                onClick={() => {
                  rememberCurrentTask(row.task.taskId);
                  navigate(
                    `/artifacts?sourceTaskId=${encodeURIComponent(row.task.taskId)}`,
                  );
                }}
              >
                +{hiddenCount}
              </Button>
            ) : null}
          </div>
        );
      },
    },
    {
      title: "最近更新",
      key: "updated",
      width: "13%",
      render: (_, row) => (
        <Typography.Text type="secondary" className="text-sm">
          {formatRelativeTime(row.updatedAt)}
        </Typography.Text>
      ),
    },
    {
      title: "操作",
      key: "action",
      width: "17%",
      render: (_, r) => (
        <div className="-ml-[8px]">
          <Button
            type="link"
            size="small"
            onClick={() => {
              rememberCurrentTask(r.task.taskId);
              navigate(`/tasks/${encodeURIComponent(r.task.taskId)}`);
            }}
          >
            继续项目
          </Button>
          <Button
            type="link"
            size="small"
            title="克隆项目"
            aria-label={`克隆项目 ${r.task.title ?? ""}`}
            onClick={() => openCloneModal(r.task)}
          >
            克隆
          </Button>
          <Popconfirm
            title="确认删除"
            description={`确定删除项目「${r.task.title ?? "未命名项目"}」？此操作不可恢复。`}
            onConfirm={() => void handleDeleteTask(r.task.taskId)}
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
          >
            <Button type="text" size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </div>
      ),
    },
  ];

  return (
    <div className="w-full h-full min-h-0 overflow-hidden pt-0!">
      <div className="flex h-full min-h-0 w-full flex-col rounded-xl bg-surface px-6 pt-6 space-y-4">
        {list.cached ? (
          <Alert
            type={list.error ? "warning" : "info"}
            showIcon
            message={
              list.error
                ? "更新失败，当前显示上次加载的项目"
                : "正在更新，当前显示上次加载的项目"
            }
            description={list.error ?? undefined}
            action={
              list.error ? (
                <Button size="small" onClick={reloadTasks}>
                  重试
                </Button>
              ) : undefined
            }
          />
        ) : null}
        {fixtureReason ? (
          <FixtureDisclosureAlert reason={fixtureReason} />
        ) : null}
        {loadError ? (
          <Alert
            type="error"
            showIcon
            message="无法加载项目"
            description={loadError}
            className="mb-4"
            action={
              <Button size="small" onClick={reloadTasks}>
                重试
              </Button>
            }
          />
        ) : null}

        {!loadError ? (
          <div
            ref={statusSummaryWrapRef}
            className={cn(
              "grid flex-none overflow-hidden transition-[grid-template-rows,margin] duration-200 ease-out",
              statusSummaryCollapsed
                ? "mb-0 grid-rows-[0fr]"
                : "mb-5 grid-rows-[1fr]",
            )}
            aria-hidden={statusSummaryCollapsed}
          >
            <section
              className="min-h-0 overflow-hidden grid grid-cols-4 gap-3 max-md:grid-cols-2"
              aria-label="项目状态统计"
              data-testid="task-board-status-summary"
            >
              {statusSummary.map((item) => {
                const selected = statusFilter === item.key;
                return (
                  <button
                    key={item.key}
                    type="button"
                    aria-pressed={selected}
                    aria-label={`${item.label}，${item.count} 个`}
                    className={cn(
                      "group relative flex min-h-11 min-w-0 flex-col justify-center gap-1 overflow-hidden rounded-xl border px-4 py-3.5 text-left transition-[background-color,border-color,box-shadow] duration-150",
                      "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-strong",
                      selected
                        ? item.selectedClass
                        : "border-border bg-surface hover:border-border-strong hover:bg-surface-soft hover:shadow-sm",
                    )}
                    onClick={() => {
                      setStatusFilter(item.key);
                      setPage(1);
                    }}
                  >
                    <span
                      className={cn(
                        "absolute inset-y-0 left-0 w-1 rounded-l-xl transition-opacity duration-150",
                        item.barClass,
                        selected
                          ? "opacity-100"
                          : "opacity-0 group-hover:opacity-50",
                      )}
                      aria-hidden
                    />
                    <span
                      className={cn(
                        "text-xl font-semibold tabular-nums leading-tight",
                        item.countClass,
                      )}
                    >
                      {item.count}
                    </span>
                    <span
                      className={cn(
                        "truncate text-sm font-medium",
                        selected
                          ? item.countClass
                          : "text-text-secondary group-hover:text-text",
                      )}
                    >
                      {item.label}
                    </span>
                  </button>
                );
              })}
            </section>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-3 flex-none">
          <Typography.Title
            id="project-list-heading"
            level={5}
            className="m-0! text-md! font-semibold! text-text!"
          >
            项目列表
          </Typography.Title>
          <Button type="primary" onClick={() => navigate("/?intent=create")}>
            新建项目
          </Button>
        </div>
        <section
          className="vertical-scroll-table min-h-0 flex-1"
          aria-labelledby="project-list-heading"
        >
          {loadError ? null : (
            <Table<BoardTaskRow>
              rowKey={(row) => row.task.taskId}
              columns={columns}
              dataSource={visibleRows}
              loading={loading}
              scroll={{ y: "calc(100% - 55px)" }}
              onScroll={handleTableScroll}
              locale={{
                emptyText: (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description={
                      statusFilter === "all" ? "暂无项目" : "暂无符合条件的项目"
                    }
                  />
                ),
              }}
              pagination={{
                current: page,
                pageSize,
                total: listTotal,
                position: ["bottomRight"],
                showSizeChanger: true,
                pageSizeOptions: BOARD_PAGE_SIZE_OPTIONS,
                showTotal: (total) => `共 ${total} 个项目`,
                onChange: (nextPage, nextPageSize) => {
                  if (nextPageSize !== pageSize) {
                    setPageSize(nextPageSize);
                    setPage(1);
                    return;
                  }
                  setPage(nextPage);
                },
              }}
            />
          )}
        </section>

        <Modal
          open={cloneSource !== null}
          title="克隆项目"
          okText={cloneIncludeArtifacts ? "克隆项目" : "从模板创建"}
          cancelText="取消"
          confirmLoading={cloning}
          onCancel={closeCloneModal}
          onOk={() => void handleCloneTask()}
          destroyOnClose
        >
          {cloneSource ? (
            <div className="flex flex-col gap-3 py-1">
              <Typography.Text type="secondary" className="text-sm">
                基于「{cloneSource.title || "未命名项目"}
                」的研究规格创建新项目；执行进度不带入。
              </Typography.Text>
              <div className="flex flex-col gap-1">
                <Typography.Text className="text-sm">
                  新项目标题
                </Typography.Text>
                <Input
                  value={cloneTitleDraft}
                  onChange={(event) => setCloneTitleDraft(event.target.value)}
                  placeholder={
                    cloneSource.title
                      ? `${cloneSource.title}（副本）`
                      : "未命名项目（副本）"
                  }
                  maxLength={200}
                  onPressEnter={() => void handleCloneTask()}
                />
              </div>
              <div className="flex flex-col gap-1">
                <Typography.Text className="text-sm">复用方式</Typography.Text>
                <Radio.Group
                  value={cloneIncludeArtifacts}
                  onChange={(event) =>
                    setCloneIncludeArtifacts(event.target.value)
                  }
                >
                  <Space direction="vertical">
                    <Radio value={true}>
                      <span className="text-sm">
                        完整克隆（带入全部产物，内容寻址零拷贝）
                      </span>
                    </Radio>
                    <Radio value={false}>
                      <span className="text-sm">
                        仅规格模板（不含产物，从空开始）
                      </span>
                    </Radio>
                  </Space>
                </Radio.Group>
              </div>
            </div>
          ) : null}
        </Modal>
      </div>
    </div>
  );
}
