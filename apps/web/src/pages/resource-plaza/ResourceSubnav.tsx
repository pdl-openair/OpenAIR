import { NavLink } from "react-router-dom";
import { cn } from "../../lib/cn";

const ITEMS = [
  {
    to: "/resources",
    end: true,
    label: "精选资源",
    testId: "resource-subnav-home",
  },
  {
    to: "/resources/catalog",
    end: false,
    label: "公共资源",
    testId: "resource-subnav-catalog",
  },
  {
    to: "/resources/mine",
    end: false,
    label: "我的资源",
    testId: "resource-subnav-mine",
  },
  {
    to: "/resources/equipment",
    end: false,
    label: "设备资源",
    testId: "resource-subnav-equipment",
  },
  {
    to: "/resources/compute",
    end: false,
    label: "算力资源",
    testId: "resource-subnav-compute",
  },
] as const;

/**
 * D1 ResourceSubnav
 * Variant：精选 / 公共资源 / 我的资源 / 设备资源 / 算力资源，由路由驱动。
 * 本轮隐藏上传入口，不保留假按钮。
 */
export default function ResourceSubnav() {
  return (
    <nav
      className="flex h-[52px] shrink-0 items-center gap-6 border-b border-border px-6 max-[900px]:px-6 max-[560px]:px-4"
      aria-label="资源广场二级导航"
      data-testid="resource-subnav"
    >
      {ITEMS.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          data-testid={item.testId}
          className={({ isActive }) =>
            cn(
              "relative flex h-[52px] items-center text-sm transition-colors hover:no-underline!",
              isActive
                ? "font-semibold text-brand! after:absolute after:inset-x-0 after:bottom-0 after:h-0.5 after:bg-brand"
                : "font-medium text-text! hover:text-brand",
            )
          }
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}
