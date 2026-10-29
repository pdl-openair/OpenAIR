import { type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { Button, Typography } from "antd";
import { Home, LayoutList, RouteOff, Sparkles } from "lucide-react";
import { useAppPreferences } from "../context/AppPreferencesContext";
import { cn } from "../lib/cn";

const COPY = {
  "zh-CN": {
    status: "404 · 路径未接入",
    title: "这条研究路径尚未接入",
    description:
      "当前地址不在 OpenAIR 可用页面中。可能是链接已变更，或路径输入有误。你可以从下方入口继续科研工作。",
    pathLabel: "未匹配路径",
    home: "返回首页",
    starters: "科研起点",
    board: "项目看板"
  },
  "en-US": {
    status: "404 · Route unavailable",
    title: "This research route is not connected",
    description:
      "The address is not part of OpenAIR's available pages. The link may have changed, or the path may be incorrect. Use the entries below to continue your research work.",
    pathLabel: "Unmatched path",
    home: "Back to home",
    starters: "Starters",
    board: "Project board"
  }
} as const;

function NavIcon({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center [&_svg]:h-4 [&_svg]:w-4">
      {children}
    </span>
  );
}

const NAV_ACTIONS = [
  {
    key: "home",
    to: "/",
    icon: Home,
    labelKey: "home" as const,
    primary: true
  },
  {
    key: "starters",
    to: "/starters",
    icon: Sparkles,
    labelKey: "starters" as const,
    primary: false
  },
  {
    key: "board",
    to: "/workbench",
    icon: LayoutList,
    labelKey: "board" as const,
    primary: false
  }
] as const;

export default function NotFoundPage() {
  const location = useLocation();
  const { preferences } = useAppPreferences();
  const copy = COPY[preferences.language];

  return (
    <div
      className="flex min-h-dvh w-full flex-col bg-shell-bg"
      data-testid="not-found-page"
    >
      <main className="flex flex-1 items-center justify-center px-6 pb-12 max-[560px]:px-4 max-[560px]:pb-8">
        <div className="w-full max-w-3xl">
          <section
            role="alert"
            aria-live="polite"
            className="overflow-hidden rounded-2xl border border-shell-border bg-surface shadow-md"
          >
            <div className="border-b border-shell-border bg-shell-surface-subtle-2 px-6 py-5 max-[560px]:px-4">
              <div className="flex items-start gap-4">
                <div
                  className={cn(
                    "flex h-12 w-12 shrink-0 items-center justify-center rounded-xl",
                    "border border-brand-soft-2 bg-brand-soft text-brand"
                  )}
                  aria-hidden
                >
                  <RouteOff className="h-6 w-6" strokeWidth={1.75} />
                </div>
                <div className="min-w-0 flex-1">
                  <Typography.Text className="mb-1 block text-xs font-medium tracking-wide text-shell-text-muted uppercase">
                    {copy.status}
                  </Typography.Text>
                  <Typography.Title
                    level={3}
                    className="mb-2! mt-0! text-shell-text-strong!"
                  >
                    {copy.title}
                  </Typography.Title>
                  <Typography.Paragraph className="mb-0! text-sm! leading-relaxed text-shell-text-secondary">
                    {copy.description}
                  </Typography.Paragraph>
                </div>
              </div>
            </div>

            <div className="px-6 py-5 max-[560px]:px-4">
              <div
                className="mb-6 rounded-lg border border-shell-border bg-shell-bg px-4 py-3"
                data-testid="not-found-path"
              >
                <Typography.Text className="mb-1 block text-xs text-shell-text-muted">
                  {copy.pathLabel}
                </Typography.Text>
                <Typography.Text
                  code
                  className="block text-sm leading-relaxed text-shell-text wrap-anywhere"
                >
                  {location.pathname}
                </Typography.Text>
              </div>

              <div className="flex flex-wrap items-center justify-center gap-3">
                {NAV_ACTIONS.map(
                  ({ key, to, icon: Icon, labelKey, primary }) => (
                    <Link key={key} to={to}>
                      <Button
                        type={primary ? "primary" : "default"}
                        size="large"
                        icon={
                          <NavIcon>
                            <Icon strokeWidth={1.75} />
                          </NavIcon>
                        }
                        className={cn("min-h-11 px-5")}
                      >
                        {copy[labelKey]}
                      </Button>
                    </Link>
                  )
                )}
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
