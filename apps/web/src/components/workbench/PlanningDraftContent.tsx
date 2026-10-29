import { Typography } from "antd";
import type { TaskPlan } from "@openair/shared";
import type { ResearchStartSessionStatus } from "../../api/client";
import {
  projectPlanningWorkbench,
  type PlanningStructureState,
} from "../../lib/planningCollaboration";

function structureSymbol(state: PlanningStructureState): string {
  if (state === "formed") return "✓";
  return "○";
}

export function PlanningStructure({
  snapshot,
  plan = null,
}: {
  snapshot: ResearchStartSessionStatus | null;
  plan?: TaskPlan | null;
}) {
  const projection = projectPlanningWorkbench(snapshot, plan);
  return (
    <nav
      className="grid gap-1 py-2"
      aria-label="研究方案"
      data-testid="workbench-research-design-outline"
    >
      {projection.structure.map((item) => (
        <div
          key={item.key}
          className="flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm text-text"
          data-testid={`planning-structure-${item.key}`}
          data-state={item.state}
        >
          <span
            className={
              item.state === "formed"
                ? "w-4 shrink-0 text-center text-success"
                : "w-4 shrink-0 text-center text-text-muted"
            }
            aria-hidden
          >
            {structureSymbol(item.state)}
          </span>
          <span>{item.label}</span>
        </div>
      ))}
    </nav>
  );
}

export function PlanningFormationContent({
  snapshot,
}: {
  snapshot: ResearchStartSessionStatus | null;
}) {
  const projection = projectPlanningWorkbench(snapshot);
  if (!projection.draftSections.length) {
    return (
      <section
        className="mx-auto grid w-full max-w-2xl gap-2 py-12"
        data-testid="workbench-planning-empty-draft"
      >
        <Typography.Paragraph className="m-0! max-w-xl text-base! leading-7! text-text-secondary">
          方案形成后将显示在这里。
        </Typography.Paragraph>
      </section>
    );
  }
  return (
    <section
      className="mx-auto grid w-full max-w-2xl gap-7 py-6"
      data-testid="workbench-planning-partial-draft"
    >
      <div className="grid gap-1 border-b border-border pb-4">
        <Typography.Text type="secondary" className="text-xs">
          研究草案
        </Typography.Text>
        <Typography.Title level={3} className="m-0!">
          研究方案
        </Typography.Title>
      </div>
      {projection.draftSections.map((section) => (
        <section
          key={section.key}
          className="grid gap-2"
          data-testid={`planning-draft-section-${section.key}`}
        >
          <Typography.Title level={4} className="m-0!">
            {section.title}
          </Typography.Title>
          {section.values.length === 1 ? (
            <Typography.Paragraph className="m-0! leading-7! text-text-secondary">
              {section.values[0]}
            </Typography.Paragraph>
          ) : (
            <ul className="m-0 grid gap-1.5 pl-5 text-sm text-text-secondary">
              {section.values.map((value) => (
                <li key={value} className="leading-relaxed">
                  {value}
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </section>
  );
}
