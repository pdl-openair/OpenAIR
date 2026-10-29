import type { UIMatch } from "react-router-dom";
import type { AppRouteHandle } from "../router/types";

const PATHS_WITHOUT_HEADER = ["/login", "/register", "/resources"] as const;
const PATHS_WITHOUT_SIDER = ["/login", "/register"] as const;

/** Paths that should not render the global app header. Extend this list as needed. */
export function isPageWithoutHeader(
  pathname: string,
  matches: UIMatch[] = [],
): boolean {
  if (pathname === "/") return true;

  if (
    PATHS_WITHOUT_HEADER.some(
      (path) => pathname === path || pathname.startsWith(`${path}/`),
    )
  ) {
    return true;
  }

  return matches.some(
    (match) => (match.handle as AppRouteHandle | undefined)?.hideHeader,
  );
}

/** Whether the current route should hide the app sidebar. */
export function isPageWithoutSider(
  pathname: string,
  matches: UIMatch[] = [],
): boolean {
  if (
    PATHS_WITHOUT_SIDER.some(
      (path) => pathname === path || pathname.startsWith(`${path}/`),
    )
  ) {
    return true;
  }

  return matches.some(
    (match) => (match.handle as AppRouteHandle | undefined)?.hideSider,
  );
}

/** Whether the current route should hide the breadcrumb trail in the header. */
export function isPageWithoutBreadcrumb(
  pathname: string,
  matches: UIMatch[],
): boolean {
  if (pathname === "/") return true;
  return matches.some(
    (match) => (match.handle as AppRouteHandle | undefined)?.hideBreadcrumb,
  );
}
