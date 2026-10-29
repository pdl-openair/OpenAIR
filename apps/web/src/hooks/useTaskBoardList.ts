import type { Task } from "@openair/shared";
import { useEffect, useRef, useState, type SetStateAction } from "react";
import { fetchTaskListPage, type TaskListPage } from "../api/client";
import { classifyApiFailure, getApiBaseUrl } from "../api/http";
import { useAuth } from "../context/AuthContext";
import {
  invalidateTaskBoardCache,
  readTaskBoardCache,
  writeTaskBoardCache,
} from "../lib/taskBoardCache";

export const TASK_BOARD_TIMEOUT_MS = 15_000;

function failureMessage(error: unknown): string {
  switch (classifyApiFailure(error)) {
    case "unauthorized":
      return "会话已失效，请重新登录。";
    case "forbidden":
      return "你无权访问这些项目。";
    case "not_found":
      return "项目列表不存在，请刷新后重试。";
    case "contract":
      return "项目数据暂时不完整，请刷新后重试。";
    default:
      return error instanceof Error && error.name === "TimeoutError"
        ? "项目加载超时，请重试。"
        : "项目暂时无法加载，请稍后重试。";
  }
}

export function useTaskBoardList(
  page: number,
  pageSize: number,
  status: string,
) {
  const { user } = useAuth();
  const scope =
    user?.userId && user.ownerId && user.workspaceId
      ? JSON.stringify([
          getApiBaseUrl(),
          user.userId,
          user.ownerId,
          user.workspaceId,
        ])
      : "";
  const key = scope
    ? `${scope}:${JSON.stringify([page, pageSize, status])}`
    : "";
  const viewKey = `${scope}:${page}:${pageSize}:${status}`;
  const [state, setState] = useState<{
    viewKey: string;
    data: TaskListPage | null;
    loading: boolean;
    cached: boolean;
    error: string | null;
  }>(() => {
    const data = readTaskBoardCache(key);
    return { viewKey, data, loading: !data, cached: !!data, error: null };
  });
  const [revision, setRevision] = useState(0);
  const generation = useRef(0);

  useEffect(() => {
    const requestGeneration = ++generation.current;
    let active = true;
    const controller = new AbortController();
    const data = readTaskBoardCache(key);
    void Promise.resolve().then(() => {
      if (active && requestGeneration === generation.current)
        setState({
          viewKey,
          data,
          loading: !data,
          cached: !!data,
          error: null,
        });
    });
    let timer: ReturnType<typeof setTimeout>;
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        reject(
          Object.assign(new Error("Task list timed out"), {
            name: "TimeoutError",
          }),
        );
        controller.abort();
      }, TASK_BOARD_TIMEOUT_MS);
    });
    const current = () => active && requestGeneration === generation.current;
    void Promise.race([
      fetchTaskListPage({
        limit: pageSize,
        offset: (page - 1) * pageSize,
        statuses:
          status === "all"
            ? undefined
            : status === "running"
              ? [
                  "draft",
                  "not_started",
                  "running",
                  "awaiting_confirmation",
                  "cancelled",
                ]
              : [status],
        signal: controller.signal,
      }),
      timeout,
    ])
      .then((result) => {
        if (!current()) return;
        writeTaskBoardCache(key, result);
        setState({
          viewKey,
          data: result,
          loading: false,
          cached: false,
          error: null,
        });
      })
      .catch((error) => {
        if (!current()) return;
        const kind = classifyApiFailure(error);
        const denied = kind === "unauthorized" || kind === "forbidden";
        if (denied) invalidateTaskBoardCache(scope);
        setState({
          viewKey,
          data: denied ? null : data,
          loading: false,
          cached: !denied && !!data,
          error: failureMessage(error),
        });
      })
      .finally(() => clearTimeout(timer));
    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [key, viewKey, scope, page, pageSize, status, revision]);

  const visible =
    state.viewKey === viewKey
      ? state
      : {
          viewKey,
          data: null,
          loading: true,
          cached: false,
          error: null,
        };
  return {
    ...visible,
    setTasks: (update: SetStateAction<Task[]>) =>
      setState((previous) => {
        if (previous.viewKey !== viewKey || !previous.data) return previous;
        const tasks =
          typeof update === "function" ? update(previous.data.tasks) : update;
        return { ...previous, data: { ...previous.data, tasks } };
      }),
    setTotal: (total: number) =>
      setState((previous) =>
        previous.data && previous.viewKey === viewKey
          ? { ...previous, data: { ...previous.data, total } }
          : previous,
      ),
    reload: () => setRevision((value) => value + 1),
    invalidate: () => {
      invalidateTaskBoardCache(scope);
      ++generation.current;
      setState((previous) => ({
        ...previous,
        cached: false,
        loading: false,
        error: null,
      }));
    },
  };
}
