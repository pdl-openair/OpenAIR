import type { UIMatch } from "react-router-dom";
import type { AppRouteHandle } from "../router/types";

export interface BreadcrumbItem {
  title: string;
  /** When set, the segment is clickable and navigates to this path. */
  path?: string;
  icon?: React.ReactNode;
}

const ROOT_CRUMB: BreadcrumbItem = {
  title: "首页",
  path: "/"
};

function handleForMatch(match: UIMatch): AppRouteHandle | undefined {
  return match.handle as AppRouteHandle | undefined;
}

/** Resolve `:param` placeholders in breadcrumbLink using the match params. */
function resolveBreadcrumbPath(match: UIMatch, link?: string): string {
  const template = link ?? match.pathname;
  return template.replace(/:([A-Za-z0-9_]+)/g, (_, key: string) => {
    const value = match.params[key];
    return value != null ? encodeURIComponent(value) : `:${key}`;
  });
}

/**
 * Build breadcrumb items from the current route match chain.
 * The first item is always OpenAIR linking to the home page.
 *
 * `lastTitleOverride` (the page header title) replaces the last route-derived
 * segment by default. A route can opt into APPENDING it as a new trailing
 * segment instead (keeping the route's own last crumb) by setting
 * `breadcrumbAppendTitle: true` on its handle — used by detail pages where the
 * route crumb is a category (e.g. 研究社区) and the page title is the item name.
 */
export function buildBreadcrumbsFromMatches(
  matches: UIMatch[],
  lastTitleOverride?: string
): BreadcrumbItem[] {
  const crumbs: BreadcrumbItem[] = [ROOT_CRUMB];
  let appendTitle = false;

  for (const match of matches) {
    const handle = handleForMatch(match);
    if (handle?.breadcrumbAppendTitle) appendTitle = true;
    if (!handle?.breadcrumbTitle) continue;

    crumbs.push({
      title: handle.breadcrumbTitle,
      path: resolveBreadcrumbPath(match, handle.breadcrumbLink)
    });
  }

  if (crumbs.length === 0) {
    return [ROOT_CRUMB];
  }

  const lastIndex = crumbs.length - 1;
  const last = crumbs[lastIndex];
  if (lastTitleOverride && appendTitle) {
    // Keep the route's last crumb; add the dynamic title as a new segment.
    crumbs.push({ title: lastTitleOverride });
  } else {
    crumbs[lastIndex] = { title: lastTitleOverride ?? last.title };
  }

  return crumbs;
}
