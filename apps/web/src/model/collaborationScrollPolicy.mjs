export const COLLABORATION_BOTTOM_THRESHOLD_PX = 48;

export function isCollaborationScrollNearBottom(metrics, thresholdPx = COLLABORATION_BOTTOM_THRESHOLD_PX) {
  const scrollTop = Number(metrics?.scrollTop ?? 0);
  const clientHeight = Number(metrics?.clientHeight ?? 0);
  const scrollHeight = Number(metrics?.scrollHeight ?? 0);
  const threshold = Number.isFinite(thresholdPx) ? Math.max(0, thresholdPx) : COLLABORATION_BOTTOM_THRESHOLD_PX;
  if (![scrollTop, clientHeight, scrollHeight].every(Number.isFinite)) return true;
  return scrollHeight - (scrollTop + clientHeight) <= threshold;
}

export function shouldFollowCollaborationScroll({
  userNearBottom,
  previousScrollHeight,
  nextScrollHeight,
}) {
  if (!userNearBottom) return false;
  const previous = Number(previousScrollHeight ?? 0);
  const next = Number(nextScrollHeight ?? 0);
  if (!Number.isFinite(previous) || !Number.isFinite(next)) return false;
  return next > previous;
}
