export function hasMaterializedCanonicalCollaboration(view) {
  if (!view) return false;
  return view.currentWork?.some((group) => (group.items?.length ?? 0) > 0)
    || (view.waitingAndDependencies?.length ?? 0) > 0
    || (view.reviewAndHandoffs?.length ?? 0) > 0;
}

export function shouldRenderCanonicalCollaboration({
  apiConnected,
  progressTrustKind,
  loadStatus,
  view,
}) {
  return apiConnected === true
    && progressTrustKind === "canonical"
    && loadStatus === "ready"
    && hasMaterializedCanonicalCollaboration(view);
}

export function workbenchNextStepPresentation({ isPlanConfirmationPending, needsUserAction, isDraftUnconfirmed = false }) {
  if (isPlanConfirmationPending) return { label: "等待你确认研究方案", tone: "warning" };
  if (isDraftUnconfirmed) return { label: "正在准备研究方案", tone: "warning" };
  if (needsUserAction) return { label: "待你确认", tone: "warning" };
  return { label: "可继续", tone: "success" };
}
