export interface AppRouteHandle {
  /** Breadcrumb label for this route segment. */
  breadcrumbTitle?: string;
  /**
   * Override link target for this segment (e.g. tasks parent → /workbench).
   * May include `:param` placeholders resolved from the route match params
   * (e.g. `/tasks/:taskId`).
   */
  breadcrumbLink?: string;
  /**
   * When the page sets a header title, append it as a new trailing crumb
   * (keeping this route's own crumb) instead of replacing the last crumb.
   * Used by detail pages where the route crumb is a category and the title
   * is the item name (e.g. 研究社区 / <publication title>).
   */
  breadcrumbAppendTitle?: boolean;
  headerTransparent?: boolean;
  /** Hide breadcrumb trail in the header (e.g. home page). */
  hideBreadcrumb?: boolean;
  /** Hide the global header entirely (e.g. 404 page). */
  hideHeader?: boolean;
  /** Hide the app sidebar (e.g. 404 page). */
  hideSider?: boolean;
}

declare module "react-router-dom" {
  // eslint-disable-next-line @typescript-eslint/no-empty-object-type
  interface RouteHandle extends AppRouteHandle {}
}
