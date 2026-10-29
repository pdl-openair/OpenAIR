import { Alert, Button, Skeleton } from "antd";
import { PackageOpen, SearchX, ShieldAlert, TriangleAlert } from "lucide-react";
import { cn } from "../../lib/cn";

function StatePanel({
  children,
  testId,
  className
}: {
  children: React.ReactNode;
  testId: string;
  className?: string;
}) {
  return (
    <section className={cn("w-full bg-surface", className)} data-testid={testId}>
      {children}
    </section>
  );
}

export function ResourcePlazaLoadingState() {
  return (
    <StatePanel testId="resource-plaza-loading">
      <h3 className="mb-1 mt-0 text-base font-semibold text-text">正在加载可发现资源</h3>
      <p className="mb-5 mt-0 text-sm text-text-secondary">正在核对当前安全态、筛选条件和可用版本。</p>
      <div className="grid gap-3">
        <Skeleton active paragraph={{ rows: 2 }} title={{ width: "34%" }} />
        <Skeleton active paragraph={{ rows: 2 }} title={{ width: "34%" }} />
      </div>
    </StatePanel>
  );
}

export function ResourcePlazaEmptyState({ onReset }: { onReset?: () => void }) {
  return (
    <StatePanel testId="resource-plaza-empty" className="flex flex-col items-center justify-center h-full">
      <SearchX className="mb-4 text-brand" size={28} aria-hidden="true" />
      <h3 className="mb-1 mt-0 text-lg font-semibold text-text">未找到同时满足这些条件的可发现资源</h3>
      <p className="mb-4 mt-0 text-sm text-text-secondary">
        当前目录没有满足全部搜索与筛选条件的资源。系统不会自动放宽条件或返回不匹配结果。
      </p>
      {onReset ? (
        <Button type="primary" onClick={onReset}>
          清除全部条件
        </Button>
      ) : null}
    </StatePanel>
  );
}

export function ResourcePlazaNoSupplyState({ onReload, onHome }: { onReload?: () => void; onHome?: () => void }) {
  return (
    <StatePanel testId="resource-plaza-no-supply" className="flex flex-col items-center justify-center h-full">
      <PackageOpen className="mb-4 text-brand" size={28} aria-hidden="true" />
      <h3 className="mb-1 mt-0 text-lg font-semibold text-text">暂无可发现资源</h3>
      <p className="mb-4 mt-0 text-sm text-text-secondary">
        当前安全域中尚无满足发布、许可和可见性要求的资源。系统不会显示草稿、待核实对象或无权资源。
      </p>
      <div className="flex flex-wrap gap-2">
        {onHome ? (
          <Button type="primary" onClick={onHome}>
            返回首页
          </Button>
        ) : null}
        {onReload ? <Button onClick={onReload}>重新加载</Button> : null}
      </div>
    </StatePanel>
  );
}

export interface ResourcePlazaSafetyErrorStateProps {
  description?: string;
  onRetry?: () => void;
  onHome?: () => void;
  homeLabel?: string;
}

export function ResourcePlazaSafetyErrorState({
  description = "为保护资源、权限和安全域信息，系统已停止显示结果。请稍后重试。",
  onRetry,
  onHome,
  homeLabel = "返回首页"
}: ResourcePlazaSafetyErrorStateProps) {
  return (
    <StatePanel testId="resource-plaza-safety-error" className="flex flex-col items-center justify-center h-full">
      <ShieldAlert className="mb-4 text-danger" size={28} aria-hidden="true" />
      <h3 className="mb-1 mt-0 text-lg font-semibold text-text">资源暂时无法加载</h3>
      <p className="mb-4 mt-0 text-sm text-danger">{description}</p>
      <Alert className="mb-4! w-fit" type="error" showIcon title="执行已阻断" />
      <div className="flex flex-wrap gap-2">
        {onRetry ? (
          <Button type="primary" onClick={onRetry}>
            重试
          </Button>
        ) : null}
        {onHome ? <Button onClick={onHome}>{homeLabel}</Button> : null}
      </div>
    </StatePanel>
  );
}

export interface ResourcePlazaLoadErrorStateProps {
  description?: string;
  onRetry?: () => void;
  onHome?: () => void;
  homeLabel?: string;
}

export function ResourcePlazaLoadErrorState({
  description = "内容暂时无法加载，请稍后重试。",
  onRetry,
  onHome,
  homeLabel = "返回资源广场"
}: ResourcePlazaLoadErrorStateProps) {
  return (
    <StatePanel testId="resource-plaza-load-error">
      <TriangleAlert className="mb-4 text-danger" size={28} aria-hidden="true" />
      <h3 className="mb-1 mt-0 text-lg font-semibold text-text">内容暂时无法加载</h3>
      <p className="mb-4 mt-0 text-sm text-text-secondary">{description}</p>
      <div className="flex flex-wrap gap-2">
        {onRetry ? (
          <Button type="primary" onClick={onRetry}>
            重新加载
          </Button>
        ) : null}
        {onHome ? <Button onClick={onHome}>{homeLabel}</Button> : null}
      </div>
    </StatePanel>
  );
}

export type ResourcePlazaResultStateProps =
  | { state: "loading" }
  | { state: "empty"; onReset?: () => void }
  | { state: "no_supply"; onReload?: () => void; onHome?: () => void }
  | { state: "load_error"; description?: string; onRetry?: () => void; onHome?: () => void; homeLabel?: string }
  | { state: "safety_error"; description?: string; onRetry?: () => void; onHome?: () => void; homeLabel?: string };

export function ResourcePlazaResultState(props: ResourcePlazaResultStateProps) {
  if (props.state === "loading") return <ResourcePlazaLoadingState />;
  if (props.state === "empty") return <ResourcePlazaEmptyState onReset={props.onReset} />;
  if (props.state === "no_supply")
    return <ResourcePlazaNoSupplyState onReload={props.onReload} onHome={props.onHome} />;
  if (props.state === "load_error") {
    return (
      <ResourcePlazaLoadErrorState
        description={props.description}
        onRetry={props.onRetry}
        onHome={props.onHome}
        homeLabel={props.homeLabel}
      />
    );
  }
  return (
    <ResourcePlazaSafetyErrorState
      description={props.description}
      onRetry={props.onRetry}
      onHome={props.onHome}
      homeLabel={props.homeLabel}
    />
  );
}
