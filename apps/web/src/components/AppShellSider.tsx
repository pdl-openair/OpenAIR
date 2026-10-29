import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Avatar, Dropdown, Layout, Menu, Typography } from "antd";
import type { MenuProps } from "antd";
import { LogoutOutlined, UserOutlined } from "@ant-design/icons";
import {
  ChevronsUpDown,
  FolderOpen,
  Home,
  LayoutList,
  MessageSquare,
  PanelLeft,
  Settings,
  Shield,
  Sparkles,
  Users,
  Wallet
} from "lucide-react";
import { motion } from "motion/react";
import { BrandLogo } from "./BrandLogo";
import { useAppPreferences } from "../context/AppPreferencesContext";
import { useAuth } from "../context/AuthContext";
import { fetchMyQuota } from "../api/quota";
import { cn } from "../lib/cn";

const { Sider } = Layout;

/** 研究社区暂未对外开放：侧栏入口关闭。路由/页面保留，重新开放时改回 true。 */
const SHOW_COMMUNITY_NAV = false;

const COPY = {
  "zh-CN": {
    home: "首页",
    resources: "资源广场",
    community: "研究社区",
    history: "历史对话",
    workbench: "项目看板",
    artifacts: "产物库",
    settings: "设置",
    artifactsTitle: "跨项目产物库",
    logout: "退出登录",
    userInfo: "用户信息"
  },
  "en-US": {
    home: "Home",
    resources: "Resource Plaza",
    community: "Research Community",
    history: "Chat History",
    workbench: "Project Board",
    artifacts: "Artifacts",
    settings: "Settings",
    artifactsTitle: "Cross-project artifact library",
    logout: "Sign out",
    userInfo: "User info"
  }
} as const;

function MenuIcon({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center [&_svg]:h-5 [&_svg]:w-5">
      {children}
    </span>
  );
}

function isWorkbenchTaskPath(pathname: string): boolean {
  return pathname.startsWith("/workbench/") && pathname.length > "/workbench/".length;
}

export default function AppShellSider() {
  const location = useLocation();
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(() => isWorkbenchTaskPath(location.pathname));
  const wasWorkbenchTaskRef = useRef(isWorkbenchTaskPath(location.pathname));
  const { preferences, effectiveTheme } = useAppPreferences();
  const { user, logout, isAdmin } = useAuth();
  const copy = COPY[preferences.language];
  const [remainingText, setRemainingText] = useState<string | null>(null);
  const [quotaLoading, setQuotaLoading] = useState(false);

  const refreshQuota = useCallback(() => {
    if (!user) {
      setRemainingText(null);
      return;
    }
    setQuotaLoading(true);
    fetchMyQuota()
      .then((q) =>
        setRemainingText(
          q.unlimited || q.remaining === null ? "不限量" : q.remaining.toLocaleString("zh-CN"),
        ),
      )
      .catch(() => setRemainingText(null))
      .finally(() => setQuotaLoading(false));
  }, [user]);

  // 进入工作台任务页时默认折叠侧栏（从首页「开始项目」等入口导航时组件不会重挂载）
  useEffect(() => {
    const onWorkbenchTask = isWorkbenchTaskPath(location.pathname);
    if (onWorkbenchTask && !wasWorkbenchTaskRef.current) {
      setCollapsed(true);
    }
    wasWorkbenchTaskRef.current = onWorkbenchTask;
  }, [location.pathname]);

  // Initial snapshot when auth user becomes available.
  useEffect(() => {
    refreshQuota();
  }, [refreshQuota]);

  const onUserMenuOpenChange = (open: boolean) => {
    if (open) refreshQuota();
  };
  const selectedKey = useMemo(() => {
    const p = location.pathname;
    if (p === "/") return "home";
    if (p.startsWith("/resources") || p.startsWith("/starters")) return "resources";
    if (p.startsWith("/community")) return "community";
    if (p.startsWith("/agent-sessions")) return "history";
    if (p.startsWith("/settings")) return "settings";
    if (p.startsWith("/admin/quotas")) return "admin-quotas";
    if (p.startsWith("/admin/governance")) return "admin-governance";
    if (p.startsWith("/artifacts")) return "artifacts";
    if (p.startsWith("/workbench") || p.startsWith("/tasks/"))
      return "workbench";
    return "";
  }, [location.pathname]);

  const onMenuClick: MenuProps["onClick"] = e => {
    const key = String(e.key);
    if (key === "home") navigate("/");
    else if (key === "resources") navigate("/resources");
    else if (key === "community") navigate("/community");
    else if (key === "history") navigate("/agent-sessions");
    else if (key === "workbench") navigate("/workbench");
    else if (key === "artifacts") navigate("/artifacts");
    else if (key === "settings") navigate("/settings");
    else if (key === "admin-quotas") navigate("/admin/quotas");
    else if (key === "admin-governance") navigate("/admin/governance");
  };

  const userInitial = (
    user?.displayName ??
    user?.username ??
    user?.ownerId ??
    "U"
  )
    .charAt(0)
    .toUpperCase();
  const userDisplayName = user?.displayName ?? user?.username ?? "unknown";

  const userMenuItems: MenuProps["items"] = [
    {
      key: "user-info",
      icon: <UserOutlined />,
      label: userDisplayName,
      disabled: true
    },
    {
      key: "user-email",
      label: (
        <Typography.Text type="secondary" className="text-xs">
          {user?.email ?? ""}
        </Typography.Text>
      ),
      disabled: true
    },
    {
      key: "user-quota",
      label: (
        <Typography.Text type="secondary" className="text-xs">
          剩余额度：{quotaLoading ? "刷新中…" : (remainingText ?? "—")}
        </Typography.Text>
      ),
      disabled: true
    },
    { type: "divider" as const },
    {
      key: "logout",
      icon: <LogoutOutlined />,
      label: copy.logout,
      danger: true,
      onClick: () => {
        logout();
        navigate("/login", { replace: true });
      }
    }
  ];

  const items: MenuProps["items"] = [
    {
      key: "home",
      icon: (
        <MenuIcon>
          <Home />
        </MenuIcon>
      ),
      label: copy.home
    },
    {
      key: "resources",
      icon: (
        <MenuIcon>
          <Sparkles />
        </MenuIcon>
      ),
      label: copy.resources
    },
    {
      key: "workbench",
      icon: (
        <MenuIcon>
          <LayoutList />
        </MenuIcon>
      ),
      label: copy.workbench
    },
    {
      key: "artifacts",
      icon: (
        <MenuIcon>
          <FolderOpen />
        </MenuIcon>
      ),
      label: copy.artifacts
    },
    {
      key: "history",
      icon: (
        <MenuIcon>
          <MessageSquare />
        </MenuIcon>
      ),
      label: copy.history
    },
    {
      key: "settings",
      icon: (
        <MenuIcon>
          <Settings />
        </MenuIcon>
      ),
      label: copy.settings
    },
    ...(isAdmin
      ? [
          {
            key: "admin-quotas",
            icon: (
              <MenuIcon>
                <Wallet />
              </MenuIcon>
            ),
            label: "额度管理"
          },
          {
            key: "admin-governance",
            icon: (
              <MenuIcon>
                <Shield />
              </MenuIcon>
            ),
            label: "用户与治理"
          }
        ]
      : [])
  ];

  return (
    <Sider
      collapsible
      collapsed={collapsed}
      onCollapse={setCollapsed}
      width={200}
      theme={effectiveTheme}
      className={cn(
        "app-shell-sider h-full shrink-0 overflow-hidden border-r border-shell-border bg-surface! max-[1100px]:w-[80px]! max-[1100px]:min-w-[80px]! max-[1100px]:max-w-[80px]! max-[1100px]:flex-[0_0_80px]!",
        // flex column: main menu grows; optional community entry / user box stick to bottom
        "[&_.ant-layout-sider-children]:flex [&_.ant-layout-sider-children]:flex-col",
        // Mobile: hide sider entirely (Ant Design sets width via inline style, hence ! overrides)
        "max-[560px]:hidden! max-[560px]:w-0! max-[560px]:min-w-0! max-[560px]:max-w-0! max-[560px]:flex-[0_0_0px]! max-[560px]:border-0!"
      )}
      trigger={null}
      style={{ background: "var(--wb-surface)" }}
    >
      <div
        className={cn(
          "flex h-[96px] shrink-0 items-center justify-between px-3 max-[1100px]:justify-center max-[1100px]:px-0",
          collapsed && "justify-center px-0"
        )}
      >
        <button
          type="button"
          onClick={() => navigate("/")}
          className={cn(
            "flex min-w-0 appearance-none items-center gap-2 border-0 bg-transparent p-0 text-left outline-none focus:outline-none cursor-pointer",
            collapsed && "justify-center"
          )}
          aria-label="OpenAIR 首页"
        >
          <picture>
            {!collapsed ? (
              <source media="(max-width: 1100px)" srcSet="/brand/openair-icon.png" />
            ) : null}
            {collapsed ? (
              <img
                src="/brand/openair-icon.png"
                alt="OpenAIR"
                draggable={false}
                className="block h-6 w-6 shrink-0 object-contain"
              />
            ) : (
              <BrandLogo className="h-8 w-auto max-w-[146px] shrink-0 max-[1100px]:h-8 max-[1100px]:w-8" />
            )}
          </picture>
        </button>

        <button
          type="button"
          className="inline-flex h-[38px] w-[36px] shrink-0 appearance-none items-center justify-center rounded-md border-none bg-surface text-shell-text outline-none hover:bg-surface-soft focus:outline-none max-[1100px]:min-[901px]:hidden max-[900px]:hidden cursor-pointer"
          onClick={() => setCollapsed(prev => !prev)}
          aria-label="收起侧边栏"
        >
          <PanelLeft className="h-4 w-4" strokeWidth={2} />
        </button>
      </div>

      <Menu
        mode="inline"
        selectedKeys={selectedKey ? [selectedKey] : []}
        className="flex-1 overflow-y-auto border-0! bg-surface! px-3! pt-6! text-[16px]! [&_.ant-menu-item]:mx-0! [&_.ant-menu-item]:my-1! [&_.ant-menu-item]:h-[50px]! [&_.ant-menu-item]:rounded-lg! [&_.ant-menu-item]:px-2.5! [&_.ant-menu-item]:leading-[50px]! [&_.ant-menu-item]:text-shell-text! [&_.ant-menu-item-selected]:bg-brand-soft! [&_.ant-menu-item-selected]:font-medium! [&_.ant-menu-item-selected]:text-brand! [&_.ant-menu-item_.ant-menu-item-icon]:mr-3! max-[1100px]:px-2! max-[1100px]:[&_.ant-menu-item]:justify-center max-[1100px]:[&_.ant-menu-item]:px-0! max-[1100px]:[&_.ant-menu-item_.ant-menu-item-icon]:mr-0! max-[1100px]:min-[901px]:[&_.ant-menu-title-content]:hidden max-[900px]:[&_.ant-menu-title-content]:hidden"
        items={items}
        onClick={onMenuClick}
      />

      {/* 研究社区入口：独立区块，位于主菜单下方、用户框上方；暂不对外开放 */}
      {SHOW_COMMUNITY_NAV ? (
        <div className="mt-auto shrink-0 px-3 pb-2 max-[1100px]:min-[901px]:px-2 max-[900px]:px-2">
          <button
            type="button"
            onClick={() => navigate("/community")}
            aria-label={copy.community}
            title={copy.community}
            className={cn(
              "flex h-[46px] w-full items-center gap-3 rounded-lg border-0 px-2.5 text-left text-[15px] outline-none transition-colors",
              collapsed && "justify-center px-0",
              selectedKey === "community"
                ? "bg-shell-bg text-text font-medium"
                : "bg-transparent text-shell-text hover:bg-shell-bg hover:text-text"
            )}
          >
            <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center [&_svg]:h-5 [&_svg]:w-5">
              <Users />
            </span>
            <span className={cn("truncate max-[1100px]:min-[901px]:hidden max-[900px]:hidden", collapsed && "hidden")}>
              {copy.community}
            </span>
          </button>
        </div>
      ) : (
        <div className="mt-auto shrink-0" aria-hidden />
      )}

      <div
        className={cn(
          "shrink-0 px-3 pb-8 pt-2 max-[1100px]:min-[901px]:px-2 max-[900px]:px-2"
        )}
      >
        <Dropdown
          menu={{ items: userMenuItems }}
          placement="topRight"
          trigger={["click"]}
          onOpenChange={onUserMenuOpenChange}
        >
          <motion.div
            whileHover={{ backgroundColor: "var(--wb-shell-bg)" }}
            transition={{ duration: 0.2 }}
            className={cn(
              "flex cursor-pointer items-center gap-3 rounded-lg p-0",
              collapsed && "justify-center",
              "max-[1100px]:min-[901px]:justify-center",
              "max-[900px]:justify-center"
            )}
          >
            <Avatar
              size={collapsed ? 32 : 40}
              className="shrink-0 cursor-pointer rounded-lg! bg-brand-soft! text-brand!"
            >
              {userInitial}
            </Avatar>
            {!collapsed && (
              <>
                <div className="flex min-w-0 flex-1 flex-col overflow-hidden max-[1100px]:min-[901px]:hidden max-[900px]:hidden">
                  <Typography.Text
                    strong
                    ellipsis
                    className="text-[15px] leading-snug text-shell-text-strong"
                  >
                    {userDisplayName}
                  </Typography.Text>
                  <Typography.Text
                    type="secondary"
                    ellipsis
                    className="text-xs leading-4"
                  >
                    {user?.email ?? ""}
                  </Typography.Text>
                </div>
                <ChevronsUpDown
                  className="h-4 w-4 shrink-0 text-shell-text-muted max-[1100px]:min-[901px]:hidden max-[900px]:hidden"
                  strokeWidth={2}
                />
              </>
            )}
          </motion.div>
        </Dropdown>
      </div>
    </Sider>
  );
}
