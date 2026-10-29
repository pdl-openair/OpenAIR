import { useMemo, type ReactNode } from "react";
import { Link, useLocation, useMatches, useNavigate } from "react-router-dom";
import { Breadcrumb, Button, Dropdown, Space } from "antd";
import type { MenuProps } from "antd";
import { LogoutOutlined, MenuOutlined } from "@ant-design/icons";
import { FolderOpen, LayoutList, Settings, Sparkles, Home } from "lucide-react";
import { useAppPreferences } from "../context/AppPreferencesContext";
import { useAuth } from "../context/AuthContext";
import { usePageHeaderContext } from "../context/PageHeaderContext";
import { UserNoticeTrigger } from "./UserNoticeConsole";
import { buildBreadcrumbsFromMatches } from "../lib/breadcrumbs";
import type { BreadcrumbItem } from "../lib/breadcrumbs";
import { isPageWithoutBreadcrumb, isPageWithoutHeader } from "../lib/pageLayout";
import { cn } from "../lib/cn";
import type { AppRouteHandle } from "../router/types";

const COPY = {
  "zh-CN": {
    starters: "科研起点",
    workbench: "项目看板",
    artifacts: "产物库",
    settings: "设置",
    logout: "退出登录",
    openNav: "打开全局导航"
  },
  "en-US": {
    starters: "Starters",
    workbench: "Project Board",
    artifacts: "Artifacts",
    settings: "Settings",
    logout: "Sign out",
    openNav: "Open navigation"
  }
} as const;

function NavIcon({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center [&_svg]:h-4 [&_svg]:w-4">{children}</span>
  );
}

function toAntdBreadcrumbItems(breadcrumbs: BreadcrumbItem[]) {
  return breadcrumbs.map((item, index) => {
    const isLast = index === breadcrumbs.length - 1;
    const isHome = index === 0;
    const title = isHome ? (
      <div className="flex items-center gap-1">
        <Home size={16} />
        {item.title}
      </div>
    ) : (
      <span>{item.title}</span>
    );

    return {
      key: `${item.title}-${index}`,
      title:
        item.path && !isLast ? (
          <Link to={item.path} className="text-shell-text-muted transition-colors hover:text-brand">
            {title}
          </Link>
        ) : (
          <span
            className={cn(
              "inline-block max-w-[min(40vw,280px)] truncate",
              isLast ? "font-semibold text-brand" : "text-shell-text-muted"
            )}
          >
            {item.title}
          </span>
        )
    };
  });
}

export default function Header() {
  const location = useLocation();
  const matches = useMatches();
  const navigate = useNavigate();
  const { preferences } = useAppPreferences();
  const { logout } = useAuth();
  const { pageHeader } = usePageHeaderContext();
  const copy = COPY[preferences.language];

  const showHeader = !isPageWithoutHeader(location.pathname, matches);
  const showBreadcrumb = !isPageWithoutBreadcrumb(location.pathname, matches);
  const breadcrumbs = showBreadcrumb ? buildBreadcrumbsFromMatches(matches, pageHeader.title) : [];

  const routeHandle = matches.at(-1)?.handle as AppRouteHandle | undefined;
  const transparent = pageHeader.transparent ?? routeHandle?.headerTransparent ?? false;
  const action = pageHeader.action;

  const breadcrumbItems = useMemo(() => toAntdBreadcrumbItems(breadcrumbs), [breadcrumbs]);

  const selectedKey = useMemo(() => {
    const p = location.pathname;
    if (p === "/" || p.startsWith("/starters")) return "starters";
    if (p.startsWith("/settings")) return "settings";
    if (p.startsWith("/artifacts")) return "artifacts";
    if (p.startsWith("/tasks/") || p.startsWith("/workbench")) {
      return "workbench";
    }
    return "";
  }, [location.pathname]);

  const mobileNavItems: MenuProps["items"] = [
    {
      key: "starters",
      icon: (
        <NavIcon>
          <Sparkles />
        </NavIcon>
      ),
      label: copy.starters
    },
    {
      key: "workbench",
      icon: (
        <NavIcon>
          <LayoutList />
        </NavIcon>
      ),
      label: copy.workbench
    },
    {
      key: "artifacts",
      icon: (
        <NavIcon>
          <FolderOpen />
        </NavIcon>
      ),
      label: copy.artifacts
    },
    {
      key: "settings",
      icon: (
        <NavIcon>
          <Settings />
        </NavIcon>
      ),
      label: copy.settings
    },
    { type: "divider" },
    {
      key: "logout",
      icon: <LogoutOutlined />,
      label: copy.logout,
      danger: true
    }
  ];

  const onMobileNavClick: MenuProps["onClick"] = ({ key }) => {
    if (key === "starters") navigate("/starters");
    else if (key === "workbench") navigate("/workbench");
    else if (key === "artifacts") navigate("/artifacts");
    else if (key === "settings") navigate("/settings");
    else if (key === "logout") {
      logout();
      navigate("/login", { replace: true });
    }
  };

  if (!showHeader) {
    return null;
  }

  return (
    <div
      className={cn(
        "sticky top-0 z-resize flex min-h-16 items-center justify-between gap-4 px-4 max-[980px]:gap-2 max-[980px]:overflow-x-auto [&_.ant-btn]:rounded-md",
        transparent && "border-transparent"
      )}
      data-testid="app-shell-header"
    >
      <div className="min-w-0 flex-1">
        {showBreadcrumb && breadcrumbItems.length > 0 ? (
          <Breadcrumb
            items={breadcrumbItems}
            className="[&_.ant-breadcrumb-link]:text-sm [&_.ant-breadcrumb-separator]:text-shell-text-muted [&_.ant-breadcrumb]:text-sm"
          />
        ) : null}
      </div>
      <Space size={8} className="shrink-0">
        <div className="hidden max-[560px]:block">
          <Dropdown
            menu={{
              items: mobileNavItems,
              selectedKeys: selectedKey ? [selectedKey] : [],
              onClick: onMobileNavClick
            }}
            trigger={["click"]}
            placement="bottomRight"
          >
            <Button type="text" icon={<MenuOutlined />} aria-label={copy.openNav} data-testid="mobile-global-nav" />
          </Dropdown>
        </div>
        {action}
        <UserNoticeTrigger />
      </Space>
    </div>
  );
}
