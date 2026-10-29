export type ResearchActivityRecord = {
  eventId?: string | null;
  taskId?: string | null;
  sequence?: number | null;
  revision?: number | null;
  eventType?: string | null;
  presentationLevel?: "primary" | "detail" | string | null;
  workUnitId?: string | null;
  headline?: string | null;
  summary?: string | null;
  occurredAt?: string | null;
  startedAt?: string | null;
  aggregateCount?: number | null;
  activityKey?: string | null;
  phase?: string | null;
  artifactRefs?: string[];
  resultRefs?: string[];
  scientificStateVersions?: number[];
  invalidateScopes?: string[];
};

export type CanonicalResearchCurrentState = {
  revision?: number | null;
  taskStatus?: string | null;
  taskFinishedAt?: string | null;
  workUnits?: Array<{
    workUnitId: string;
    status: string;
    startedAt?: string | null;
    finishedAt?: string | null;
  }>;
};

export type ResearchActivityState = {
  bindingKey: string;
  lastEventId: string | null;
  primaryActivities: ResearchActivityRecord[];
  collapsedDetails: ResearchActivityRecord[];
  invalidateScopes: string[];
  seenEventIds: string[];
};

export function assertSafeResearchActivity<T>(value: T): T;
export function createResearchActivityState(
  bindingKey?: string,
): ResearchActivityState;
export function filterResearchActivityArtifactRefs<
  T extends ResearchActivityRecord = ResearchActivityRecord,
>(
  rows?: T[],
  visibleArtifactIds?: string[],
): Array<T & { artifactRefs: string[] }>;
export function distinctResearchActivities<
  T extends ResearchActivityRecord = ResearchActivityRecord,
>(rows?: T[]): T[];
export function mergeResearchActivityHistory<
  T extends object = ResearchActivityRecord,
>(
  durableActivities?: T[],
  livePrimaryActivities?: T[],
  liveCollapsedDetails?: T[],
  canonicalCurrentState?: CanonicalResearchCurrentState | null,
): {
  primaryActivities: ResearchActivityRecord[];
  collapsedDetails: ResearchActivityRecord[];
};

export function reduceResearchActivityEvent(
  state: ResearchActivityState,
  event: ResearchActivityRecord,
  bindingKey?: string,
): ResearchActivityState;

export function foldResearchActivityPresentation<
  T extends object = ResearchActivityRecord,
>(
  primaryActivities?: T[],
  collapsedDetails?: T[],
  canonicalCurrentState?: CanonicalResearchCurrentState | null,
): {
  blocks: Array<{
    workUnitId: string;
    rows: T[];
    processActivities: T[];
    currentStatus: string | null;
    terminalActivity: T | null;
    artifactRefs: string[];
    resultRefs: string[];
    scientificStateVersions: number[];
    startedAt: string | null;
    endedAt: string | null;
    latestOccurredAt: string | null;
  }>;
  unscoped: T[];
  startedAt: string | null;
  endedAt: string | null;
  latestOccurredAt: string | null;
};

export function coalesceResearchActivityBlocks<
  T extends ReturnType<
    typeof foldResearchActivityPresentation
  >["blocks"][number],
>(
  blocks?: T[],
  objectiveByWorkUnit?:
    ReadonlyMap<string, string | null> | Record<string, string | null>,
  goalByWorkUnit?: ReadonlyMap<string, string>,
): T[];

export function researchWorkUnitContexts(
  substages: Array<{
    substageInstanceId: string;
    substageDefinitionId?: string | null;
    objective?: string | null;
    label?: string | null;
    workUnitIds?: string[];
  }>,
): Array<{ workUnitId: string; objective: string; goalKey: string }>;
