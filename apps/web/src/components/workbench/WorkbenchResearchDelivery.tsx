import { EditOutlined, HistoryOutlined } from "@ant-design/icons";
import { Button, Tag } from "antd";
import type {
  WorkbenchArtifact,
  WorkbenchTaskModel,
} from "../../model/workbenchTask";
import { ArtifactRenderer } from "../artifacts/ArtifactRenderer";
import { FinalResultPanel } from "../finalResult/FinalResultPanel";

type ArtifactUnavailableReason = "not_found" | "transient";

export function WorkbenchPrimaryResearchDelivery({
  model,
  nodeProducts,
}: {
  model: WorkbenchTaskModel;
  nodeProducts: WorkbenchArtifact[];
}) {
  if (nodeProducts.length > 0) {
    return (
      <section
        className="mb-6 grid gap-4"
        data-testid="workbench-primary-node-products"
      >
        <DeliveryStatus model={model} />
        {nodeProducts.map((artifact) =>
          artifact.content ? (
            <article
              key={artifact.id}
              className="rounded-xl border border-border bg-surface p-5 shadow-sm"
              data-testid="workbench-primary-node-product"
              data-artifact-id={artifact.id}
            >
              <ArtifactRenderer
                artifact={artifact}
                taskId={model.taskId}
                model={model}
                bodyOnly
              />
            </article>
          ) : null,
        )}
      </section>
    );
  }

  return model.finalResult ? (
    <>
      <DeliveryStatus model={model} />
      <FinalResultPanel
        result={model.finalResult}
        artifacts={model.artifacts}
        className="mb-6"
      />
    </>
  ) : null;
}

export function WorkbenchSupportingArtifact({
  artifact,
  taskId,
  model,
  apiConnected,
  editable,
  loading,
  unavailableReason,
  showHeading,
  onOpenVersions,
  onRetry,
}: {
  artifact: WorkbenchArtifact;
  taskId: string;
  model: WorkbenchTaskModel;
  apiConnected: boolean;
  editable: boolean;
  loading: boolean;
  unavailableReason?: ArtifactUnavailableReason;
  showHeading: boolean;
  onOpenVersions: () => void;
  onRetry: () => void;
}) {
  return (
    <article className="min-w-0" data-testid="workbench-main-markdown-content">
      {showHeading ? (
        <div
          className="mb-2 text-sm font-medium text-text-secondary"
          data-testid="workbench-supporting-artifact-heading"
        >
          相关研究产物与追溯
        </div>
      ) : null}
      {apiConnected ? (
        <div
          className="mb-2 flex flex-wrap items-center justify-end gap-1"
          data-testid="workbench-artifact-version-actions"
        >
          {artifact.versionCount ? (
            <Tag className="m-0">v{artifact.versionCount}</Tag>
          ) : null}
          <Button
            type="text"
            size="small"
            icon={<EditOutlined />}
            disabled={!editable}
            onClick={onOpenVersions}
            title={
              editable ? "编辑产物（生成新版本）" : "仅 Markdown/文本产物可编辑"
            }
            data-testid="workbench-artifact-edit"
          >
            编辑
          </Button>
          <Button
            type="text"
            size="small"
            icon={<HistoryOutlined />}
            onClick={onOpenVersions}
            title="版本历史"
            data-testid="workbench-artifact-version-history"
          />
        </div>
      ) : null}
      <div
        data-testid="workbench-durable-artifact-projection"
        data-artifact-id={artifact.id}
      >
        <ArtifactRenderer
          artifact={artifact}
          taskId={taskId}
          model={model}
          loading={loading}
          unavailableReason={unavailableReason}
          onRetry={onRetry}
          bodyOnly
        />
      </div>
    </article>
  );
}

function DeliveryStatus({ model }: { model: WorkbenchTaskModel }) {
  const summary = model.completionDecision;
  if (
    !summary ||
    model.taskStatus !== "completed" ||
    !model.finalResult ||
    summary.completionDecisionId !== model.finalResult.completionDecisionId ||
    summary.scientificStateVersion !==
      model.finalResult.scientificStateVersion ||
    summary.goalAchieved === null ||
    summary.roundEndReason === null
  )
    return null;
  return (
    <div
      className="mb-3 flex flex-wrap gap-2"
      data-testid="workbench-delivery-status"
    >
      <Tag>本轮已结束</Tag>
      <Tag color={summary.goalAchieved ? "success" : "default"}>
        {summary.goalAchieved ? "原目标已达成" : "原目标未达成"}
      </Tag>
    </div>
  );
}
