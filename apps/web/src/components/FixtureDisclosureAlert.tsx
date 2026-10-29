import { Alert } from "antd";

export type FixtureDisclosureReason = "api_unavailable" | "empty_tasks" | "local_fixture";

const COPY: Record<FixtureDisclosureReason, { message: string; description: string }> = {
  api_unavailable: {
    message: "当前暂时无法加载项目",
    description: "项目列表无法加载。请稍后重试，或从首页开始一个新项目。",
  },
  empty_tasks: {
    message: "当前还没有项目",
    description: "开始项目后，会在这里查看阶段进展、产物和最近更新。",
  },
  local_fixture: {
    message: "当前展示参考流程",
    description: "参考流程只用于了解工作台结构，不会写入你的项目列表。",
  },
};

export function FixtureDisclosureAlert({ reason }: { reason: FixtureDisclosureReason }) {
  const copy = COPY[reason];
  return (
    <div data-testid="fixture-disclosure" className="my-3">
      <Alert type="warning" showIcon message={copy.message} description={copy.description} />
    </div>
  );
}
