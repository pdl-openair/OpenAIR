import type { Task, UiLanguage } from "@openair/shared";
import { fetchTask } from "../api/client";
import {
  CS_RESEARCH_OPE_FIXTURE_TASK_ID,
  isB2DemoFixtureTask,
  loadB2DemoWorkbenchFixture,
  loadCsResearchOpeWorkbenchFixture,
} from "../data/csResearchOpeFixture";
import { taskFromWorkbenchModel } from "../model/taskFromFixture";
import { workbenchModelFromApiTask, type WorkbenchTaskModel } from "../model/workbenchTask";

export type TaskDataSource = "api" | "fixture";

export type LoadedTask = {
  task: Task;
  model: WorkbenchTaskModel;
  source: TaskDataSource;
};

export type TaskLoadOptions = {
  allowFixtureFallback?: boolean;
};

export class TaskIdentityMismatchError extends Error {
  readonly code = "task_identity_mismatch";

  constructor(
    readonly requestedTaskId: string,
    readonly receivedTaskId: string,
  ) {
    super("The loaded Task identity does not match the requested route.");
    this.name = "TaskIdentityMismatchError";
  }
}

/**
 * Load a task for detail/workbench pages.
 *
 * Typed transport failures are preserved so the page coordinator can enforce
 * shared Retry-After/circuit behavior. 404 returns null (or a fixture only when
 * explicitly allowed for a known local demo id). No thrown error can fall back.
 */
export async function loadTaskForPages(
  taskId: string,
  language: UiLanguage = "zh-CN",
  { allowFixtureFallback = false }: TaskLoadOptions = {},
): Promise<LoadedTask | null> {
  try {
    const remote = await fetchTask(taskId);
    if (remote) {
      if (remote.taskId !== taskId) {
        throw new TaskIdentityMismatchError(taskId, remote.taskId);
      }
      return {
        task: remote,
        model: workbenchModelFromApiTask(remote),
        source: "api",
      };
    }
  } catch (err) {
    // Preserve ApiError status/code/detail/Retry-After for the page-level
    // coordinator. User-safe copy is selected only at the final UI boundary.
    // Any thrown transport error exits before fixture fallback.
    throw err;
  }

  // fetchTask returned null → authoritative 404.
  if (!allowFixtureFallback) return null;

  if (taskId === CS_RESEARCH_OPE_FIXTURE_TASK_ID) {
    const model = loadCsResearchOpeWorkbenchFixture(language);
    return {
      task: taskFromWorkbenchModel(model),
      model,
      source: "fixture",
    };
  }

  if (isB2DemoFixtureTask(taskId)) {
    const model = loadB2DemoWorkbenchFixture(taskId, language);
    return {
      task: taskFromWorkbenchModel(model),
      model,
      source: "fixture",
    };
  }

  // Unknown id with fixture fallback still means not found (do not invent data).
  return null;
}
