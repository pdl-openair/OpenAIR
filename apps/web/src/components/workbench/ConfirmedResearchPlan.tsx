import { Tag, Typography } from "antd";
import {
  STAGE_LABELS,
  type ResearchDesignVersion,
  type TaskStage,
} from "@openair/shared";
import { researchWorkProgressTitle } from "../../lib/researchDesignPresentation";

function PlanList({ title, values }: { title: string; values: string[] }) {
  if (!values.length) return null;
  return (
    <section className="grid gap-2">
      <Typography.Title level={4} className="m-0!">
        {title}
      </Typography.Title>
      <ul className="m-0 grid gap-2 pl-5 text-sm text-text-secondary">
        {values.map((value, index) => (
          <li key={index}>{value}</li>
        ))}
      </ul>
    </section>
  );
}

/** A read-only view of the confirmed execution authority, not the latest planning draft. */
export function ConfirmedResearchPlan({
  design,
}: {
  design: ResearchDesignVersion;
}) {
  const instances = [...design.instances].sort(
    (a, b) =>
      (a.sequenceHint ?? Number.MAX_SAFE_INTEGER) -
      (b.sequenceHint ?? Number.MAX_SAFE_INTEGER),
  );
  const workTitles = new Map(
    instances.map((instance) => [
      instance.substageInstanceId,
      researchWorkProgressTitle({
        subStage: instance.substageDefinitionId,
        label: instance.objective,
        objective: instance.objective,
      }),
    ]),
  );
  return (
    <section
      className="grid gap-7 py-4"
      data-testid="workbench-confirmed-research-plan"
      data-design-version={design.version}
    >
      <header className="flex items-center justify-between gap-3 border-b border-border pb-4">
        <Typography.Title level={3} className="m-0!">
          研究方案
        </Typography.Title>
        <div>
          <Tag>版本 {design.version}</Tag>
          <Tag color="success">已确认 · 只读</Tag>
        </div>
      </header>
      <section className="grid gap-2">
        <Typography.Title level={4} className="m-0!">
          研究目标
        </Typography.Title>
        <p className="m-0 whitespace-pre-wrap">{design.goal}</p>
      </section>
      <PlanList
        title="主研究问题"
        values={design.understanding.primaryResearchQuestions}
      />
      <PlanList
        title="关键目标产出"
        values={design.understanding.targetOutputs}
      />
      <PlanList
        title="证据边界"
        values={design.understanding.evidenceBoundaries}
      />
      <PlanList title="研究约束" values={design.constraints} />
      <PlanList
        title="范围边界"
        values={design.scopeBoundary.excludedActions}
      />
      <PlanList
        title="外部动作需确认"
        values={design.externalActionBoundary.confirmationRequiredKinds}
      />
      <section className="grid gap-4">
        <Typography.Title level={4} className="m-0!">
          研究工作安排
        </Typography.Title>
        {(
          [
            "problem_modeling",
            "solution_design",
            "execution_validation",
            "insight_generation",
          ] as TaskStage[]
        ).map((stage) => {
          const work = instances.filter(
            (instance) => instance.stageKey === stage,
          );
          return work.length ? (
            <section key={stage} className="grid gap-2">
              <Typography.Title level={5} className="m-0!">
                {STAGE_LABELS[stage]}
              </Typography.Title>
              <ol className="m-0 grid gap-3 pl-5">
                {work.map((instance) => (
                  <li key={instance.substageInstanceId}>
                    <p className="m-0">
                      {workTitles.get(instance.substageInstanceId)}
                    </p>
                    {instance.dependencies.length ||
                    instance.acceptanceCriteria.length ? (
                      <details className="mt-1 text-sm text-text-secondary">
                        <summary className="cursor-pointer w-fit hover:text-text-primary">
                          查看详细要求
                        </summary>
                        <div className="mt-2 grid gap-2">
                          {instance.dependencies.length ? (
                            <p className="m-0 text-sm text-text-secondary">
                              依赖：
                              {instance.dependencies
                                .map(
                                  (dependency) =>
                                    workTitles.get(
                                      dependency.upstreamInstanceRef
                                        .substageInstanceId,
                                    ) ?? "已确认的上游研究工作",
                                )
                                .join("；")}
                            </p>
                          ) : null}
                          {instance.acceptanceCriteria.length ? (
                            <ul className="m-0 grid gap-1 pl-5">
                              {instance.acceptanceCriteria.map((criterion) => (
                                <li key={criterion.criterionId}>
                                  {criterion.description}
                                </li>
                              ))}
                            </ul>
                          ) : null}
                        </div>
                      </details>
                    ) : null}
                  </li>
                ))}
              </ol>
            </section>
          ) : null;
        })}
      </section>
      <PlanList
        title="项目完成要求"
        values={design.projectCompletionRequirements.map(
          (requirement) => requirement.description,
        )}
      />
    </section>
  );
}
