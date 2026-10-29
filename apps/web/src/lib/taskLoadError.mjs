/**
 * Task Detail / Workbench load-error surface mapping.
 * Classification is based only on structured transport metadata. Raw error
 * text is never parsed for HTTP status or authorization decisions.
 */

export const TASK_LOAD_ZH = {
  unauthorized: "未登录或登录状态已失效",
  forbidden: "无权限访问",
  notFound: "未找到任务",
  conflict: "任务状态已更新，请刷新后继续。",
  precondition: "当前操作条件尚未满足，请按页面提示处理。",
  rateLimited: "请求较多，系统会按服务端建议稍后继续。",
  serviceUnavailable: "服务暂时不可用，请稍后继续。",
  loadFailed: "加载失败",
};

export const TASK_LOAD_EN = {
  unauthorized: "Not logged in or session expired.",
  forbidden: "You do not have permission to access this task.",
  notFound: "Task not found",
  conflict: "The task changed. Refresh before continuing.",
  precondition: "This action is not currently available. Follow the page guidance.",
  rateLimited: "Too many requests. The page will respect the server retry window.",
  serviceUnavailable: "The service is temporarily unavailable. Try again later.",
  loadFailed: "Failed to load task.",
};

const UNAUTHORIZED_CODES = new Set(["unauthorized", "authentication_required", "session_expired"]);
const FORBIDDEN_CODES = new Set(["forbidden", "permission_denied", "task_scope_denied"]);
const NOT_FOUND_CODES = new Set(["not_found", "task_not_found"]);
const CONFLICT_CODES = new Set(["conflict", "task_revision_conflict", "revision_conflict"]);
const PRECONDITION_CODES = new Set(["precondition_required", "capability_blocked", "action_not_allowed"]);
const RATE_LIMIT_CODES = new Set(["rate_limited", "too_many_requests"]);
const SERVICE_UNAVAILABLE_CODES = new Set(["service_unavailable", "upstream_unavailable"]);

function structuredStatus(err) {
  if (!err || typeof err !== "object") return null;
  const value = Number(err.status);
  return Number.isInteger(value) && value >= 100 && value <= 599 ? value : null;
}

function structuredCode(err) {
  if (!err || typeof err !== "object" || typeof err.code !== "string") return null;
  const value = err.code.trim().toLowerCase();
  return value || null;
}

/**
 * @param {unknown} err
 * @returns {"unauthorized" | "forbidden" | "notFound" | "conflict" | "precondition" | "rateLimited" | "serviceUnavailable" | "loadFailed"}
 */
export function classifyTaskLoadFailure(err) {
  const status = structuredStatus(err);
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 404) return "notFound";
  if (status === 409) return "conflict";
  if (status === 428) return "precondition";
  if (status === 429) return "rateLimited";
  if (status !== null && status >= 500) return "serviceUnavailable";

  const code = structuredCode(err);
  if (code && UNAUTHORIZED_CODES.has(code)) return "unauthorized";
  if (code && FORBIDDEN_CODES.has(code)) return "forbidden";
  if (code && NOT_FOUND_CODES.has(code)) return "notFound";
  if (code && CONFLICT_CODES.has(code)) return "conflict";
  if (code && PRECONDITION_CODES.has(code)) return "precondition";
  if (code && RATE_LIMIT_CODES.has(code)) return "rateLimited";
  if (code && SERVICE_UNAVAILABLE_CODES.has(code)) return "serviceUnavailable";
  return "loadFailed";
}

/**
 * @param {unknown} err
 * @returns {boolean}
 */
export function isTaskLoadScopeDenial(err) {
  const kind = classifyTaskLoadFailure(err);
  return kind === "unauthorized" || kind === "forbidden";
}

/**
 * @param {unknown} err
 * @param {"zh-CN" | "en-US" | string} [language]
 * @returns {string}
 */
export function userFacingTaskLoadError(err, language = "zh-CN") {
  const kind = classifyTaskLoadFailure(err);
  const table = language === "en-US" ? TASK_LOAD_EN : TASK_LOAD_ZH;
  return table[kind];
}
