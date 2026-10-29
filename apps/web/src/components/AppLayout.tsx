import { Outlet, useLocation, useMatches } from "react-router-dom";
import { Layout } from "antd";
import AppShellSider from "./AppShellSider";
import Header from "./Header";
import { QuotaExhaustedListener } from "./QuotaExhaustedListener";
import { UserNoticeProvider } from "../context/UserNoticeContext";
import { isPageWithoutSider } from "../lib/pageLayout";
import { cn } from "../lib/cn";
import type { UIMatch } from "react-router-dom";

const { Content } = Layout;

function isWorkbenchTaskPath(pathname: string): boolean {
  return pathname.startsWith("/workbench/") && pathname.length > "/workbench/".length;
}

function getContentClassName(pathname: string, matches: UIMatch[]): string {
  if (isPageWithoutSider(pathname, matches)) {
    return "min-h-0 min-w-0 flex-1 overflow-hidden bg-bg p-0";
  }

  const isWorkbench = isWorkbenchTaskPath(pathname);
  const isTaskDetail = pathname.startsWith("/tasks/");
  const isStarterHome =
    pathname === "/" ||
    pathname.startsWith("/starters") ||
    pathname.startsWith("/starter-samples");
  // 资源广场自带 ResourcePlazaLayout 边距；勿再套 *:px-6，否则精选/公共资源 tab 会对不齐
  const isResourcePlaza = pathname.startsWith("/resources");
  // 公共资源目录：壳层不滚动，由页内资源区独立滚动
  const isResourceCatalog = pathname === "/resources/catalog";
  // 仅智能体「会话详情」全幅；历史列表走与项目看板/产物库相同的 *:pt-4 边距
  const isAgentSessionDetail = /^\/agent-sessions\/[^/]+/.test(pathname);

  if (isWorkbench || isResourceCatalog || isAgentSessionDetail) {
    // Content sits below the fixed-height header; h-screen here would push its
    // lower portion outside the app shell, where root overflow clips it.
    return "flex min-h-0 flex-1 flex-col overflow-hidden bg-bg p-0";
  }

  return cn(
    "min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain bg-bg p-0 [scrollbar-gutter:stable]",
    !isStarterHome && !isResourcePlaza && "*:px-6 *:pt-4 *:pb-6",
    isTaskDetail && "*:mx-auto *:w-full *:max-w-1400",
  );
}

export default function AppLayout() {
  const location = useLocation();
  const matches = useMatches();
  const hideSider = isPageWithoutSider(location.pathname, matches);
  const isWorkbench = isWorkbenchTaskPath(location.pathname);

  return (
    <UserNoticeProvider>
      <Layout className="flex min-h-0 h-full flex-1 overflow-hidden bg-bg">
        <QuotaExhaustedListener />
        {!hideSider ? <AppShellSider /> : null}
        <Layout className="flex min-h-0! min-w-0! flex-1! flex-col! overflow-hidden! bg-bg">
          <Header />
          <Content
            className={getContentClassName(location.pathname, matches)}
            data-testid={isWorkbench ? undefined : "app-shell-scroll"}
          >
            <Outlet />
          </Content>
        </Layout>
      </Layout>
    </UserNoticeProvider>
  );
}
