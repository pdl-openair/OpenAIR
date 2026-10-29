import type { RuntimeVersion } from "../api/client";

const startedAt = new Date().toISOString();

function clean(value: string | undefined): string | null {
  const normalized = value?.trim();
  return normalized ? normalized : null;
}

export function webRuntimeVersion(): RuntimeVersion & { mode: string } {
  const commit = clean(import.meta.env.VITE_OPENAIR_GIT_COMMIT);
  return {
    startedAt,
    buildTime: clean(import.meta.env.VITE_OPENAIR_WEB_BUILD_TIME) ?? (import.meta.env.DEV ? "dev" : null),
    gitCommit: commit,
    gitCommitShort: clean(import.meta.env.VITE_OPENAIR_GIT_COMMIT_SHORT) ?? commit?.slice(0, 12) ?? null,
    gitBranch: clean(import.meta.env.VITE_OPENAIR_GIT_BRANCH),
    mode: import.meta.env.MODE,
  };
}
