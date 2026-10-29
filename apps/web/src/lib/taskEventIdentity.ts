import type { TaskEvent } from "../api/client";
import {
  DURABLE_LIFECYCLE_EVENT_TYPES as lifecycleTypes,
  classifyTaskEvent as classify,
  lifecycleBucketKey as bucketKey,
} from "./taskEventIdentityCore.mjs";

export const DURABLE_LIFECYCLE_EVENT_TYPES = lifecycleTypes as ReadonlySet<TaskEvent["type"]>;

export type AuthoritativeTaskEventScope = {
  taskId: string;
  ownerId: string;
  workspaceId: string;
  revision: number;
};

export type TaskEventClassification = {
  accepted: boolean;
  reason: string;
  refresh?: boolean;
  runId?: string | null;
};

export function classifyTaskEvent(
  event: TaskEvent,
  expected: AuthoritativeTaskEventScope,
  activeRunId: string | null,
): TaskEventClassification {
  return classify(event, expected, activeRunId) as TaskEventClassification;
}

export function lifecycleBucketKey(taskId: string, revision: number, runId: string): string {
  return bucketKey(taskId, revision, runId);
}
