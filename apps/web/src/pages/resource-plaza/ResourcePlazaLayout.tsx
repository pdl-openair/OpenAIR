import { Outlet } from "react-router-dom";
import ResourceSubnav from "./ResourceSubnav";
import { cn } from "../../lib/cn";

export default function ResourcePlazaLayout() {
  return (
    <div
      className={cn("w-full bg-bg h-full flex flex-col")}
      data-testid="resource-plaza-shell"
    >
      <ResourceSubnav />
      <main
        className={cn("px-6 py-6 max-[900px]:px-6 max-[560px]:px-4 flex-1 min-h-0")}
        data-testid="resource-plaza-outlet"
      >
        <Outlet />
      </main>
    </div>
  );
}
