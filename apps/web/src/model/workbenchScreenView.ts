/**
 * Unique Workbench screen/view model.
 * Left stages, top status, middle path, and right collaboration must all read
 * the same Task revision's canonical projection (or an explicit legacy mode).
 *
 * Pure builders live in workbenchScreenCore.mjs so contract/unit tests can import them.
 */
import type { CollaborationProjection } from "../api/client";
import type { ProgressProjectionTrust, WorkbenchStageRow, WorkbenchTaskModel } from "./workbenchTask";
import {
  assertScreenPanesShareRevision as assertPanes,
  buildWorkbenchScreenView as buildScreen,
  collaborationMatchesTaskRevision as matchCollab,
  coerceStartedStageStatus as coerceStatus,
  panesShareSingleRevision as panesShare,
  progressStepsFromScreen as stepsFromScreen,
  projectScreenToPanes as projectPanes,
  stageStatusAfterExternalFailure as stageAfterFailure,
  userVisibleStatusLabel as statusLabel,
} from "./workbenchScreenCore.mjs";

export type WorkbenchLegalNextActionKind =
  | "view_failure_details"
  | "needs_input_confirm"
  | "query_external_result"
  | "scoped_durable_retry";

export type WorkbenchLegalNextAction = {
  kind: WorkbenchLegalNextActionKind;
  label: string;
  gateId?: string;
  scopedAction?: "start_revision_run";
  requiresUserConfirm: boolean;
};

export type WorkbenchPaneStatus = {
  revision: number;
  executionDisplayStatus: string;
  stageStatuses: Array<{ stageKey: string; status: string; label: string }>;
  collaborationStatuses: Array<{
    id: string;
    status: string;
    kind?: string | null;
    title?: string | null;
    detail?: string | null;
  }>;
};

export type WorkbenchScreenSubstage = {
  id: string;
  substageKey: string;
  label: string;
  instanceSequence?: number;
  objective?: string | null;
  status: string;
  statusLabel: string;
};

export type WorkbenchScreenViewModel = {
  taskId: string;
  revision: number;
  progressTrust: ProgressProjectionTrust;
  isLiveResearch: boolean;
  executionDisplayStatus: string;
  executionStatusLabel: string;
  stageCounts: { completed: number; total: number } | null;
  pane: WorkbenchPaneStatus;
  leftStages: Array<{
    stageKey: string;
    status: string;
    statusLabel: string;
    label: string;
    completedCount?: number;
    totalCount?: number;
    planState?: "planned" | "unplanned";
    substages?: WorkbenchScreenSubstage[];
  }>;
  middlePath: Array<{
    stageKey: string;
    status: string;
    statusLabel: string;
    label: string;
    planState?: "planned" | "unplanned";
    substages?: WorkbenchScreenSubstage[];
  }>;
  rightCollaboration: Array<{
    id: string;
    status: string;
    label: string;
    title?: string | null;
    detail?: string | null;
    kind?: string | null;
    workUnitId?: string | null;
    targetWorkUnitId?: string | null;
    agentId?: string | null;
    occurredAt?: string | null;
  }>;
  activeSubstages: Array<{
    id: string;
    substageKey: string;
    label: string;
    instanceSequence?: number;
    objective?: string | null;
    status: string;
    statusLabel: string;
    stageKey: string;
    stageLabel: string;
  }>;
  activeSubstage?: {
    id: string;
    substageKey: string;
    label: string;
    instanceSequence?: number;
    objective?: string | null;
    status: string;
    statusLabel: string;
    stageKey: string;
    stageLabel: string;
  } | null;
  /** Collaboration snapshot accepted only when revision matches; otherwise null. */
  matchedCollaboration: CollaborationProjection | null;
  failureNextActions: WorkbenchLegalNextAction[];
  forbidLegacyRetry: boolean;
  legacyCompatibilityNote: string | null;
};

export const userVisibleStatusLabel = statusLabel;
export const coerceStartedStageStatus = coerceStatus;
export const collaborationMatchesTaskRevision = matchCollab as (
  collaboration: CollaborationProjection | null | undefined,
  taskRevision: number,
) => boolean;

export function buildWorkbenchScreenView(
  model: WorkbenchTaskModel,
  collaboration?: CollaborationProjection | null,
): WorkbenchScreenViewModel {
  return buildScreen(model, collaboration) as WorkbenchScreenViewModel;
}

export function assertScreenPanesShareRevision(screen: WorkbenchScreenViewModel): boolean {
  return assertPanes(screen);
}

export function projectScreenToPanes(screen: WorkbenchScreenViewModel) {
  return projectPanes(screen);
}

export function panesShareSingleRevision(
  panes: ReturnType<typeof projectScreenToPanes>,
): boolean {
  return panesShare(panes);
}

export function stageStatusAfterExternalFailure(
  projectionStatus: string,
  statusReasonCode?: string | null,
  workUnitIds: string[] = ["wu-1"],
): string {
  return stageAfterFailure(projectionStatus, statusReasonCode, workUnitIds);
}

export function progressStepsFromScreen(
  screen: WorkbenchScreenViewModel,
  resolveStage: (stageKey: string) => { id: string; label: string },
) {
  return stepsFromScreen(screen, resolveStage);
}

export { mapScreenStatusToNav, phaseAggFromScreen, canonicalStageLabel, hasStartedVsNotStartedContradiction } from "./workbenchScreenCore.mjs";

export function stageRowDisplayStatus(stage: WorkbenchStageRow): string {
  return coerceStatus({
    status: stage.status,
    projectionStatus: stage.projectionStatus,
    statusReasonCode: stage.statusReasonCode,
    completedCount: stage.completedCount,
    totalCount: stage.totalCount,
    workUnitIds: stage.subStages
      .filter((sub) => sub.projectionStatus && !["not_started", "planned"].includes(sub.projectionStatus))
      .map((sub) => sub.id),
  });
}
