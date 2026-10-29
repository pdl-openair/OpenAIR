import type { TaskListPage } from "../api/client";

const PREFIX = "openair.task-board.v1:";
const MAX_AGE_MS = 5 * 60_000;

export function readTaskBoardCache(key: string): TaskListPage | null {
  if (!key) return null;
  try {
    const raw = sessionStorage.getItem(PREFIX + key);
    if (!raw) return null;
    const entry = JSON.parse(raw);
    if (
      typeof entry.savedAt !== "number" ||
      Date.now() - entry.savedAt > MAX_AGE_MS ||
      entry.savedAt > Date.now() ||
      !Array.isArray(entry.page?.tasks) ||
      typeof entry.page.total !== "number"
    ) {
      sessionStorage.removeItem(PREFIX + key);
      return null;
    }
    return entry.page as TaskListPage;
  } catch {
    return null;
  }
}

export function writeTaskBoardCache(key: string, page: TaskListPage): void {
  if (!key) return;
  try {
    sessionStorage.setItem(
      PREFIX + key,
      JSON.stringify({ savedAt: Date.now(), page }),
    );
  } catch {
    // Storage is optional: private mode or quota must not break the live list.
  }
}

export function invalidateTaskBoardCache(scope: string): void {
  if (!scope) return;
  try {
    for (const key of Object.keys(sessionStorage)) {
      if (key.startsWith(PREFIX + scope + ":")) sessionStorage.removeItem(key);
    }
  } catch {
    // Live data remains usable when browser storage is unavailable.
  }
}
