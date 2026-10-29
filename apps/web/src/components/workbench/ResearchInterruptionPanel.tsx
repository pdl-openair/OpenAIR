import type {
  WorkbenchArtifact,
  WorkbenchTaskModel,
} from "../../model/workbenchTask";
import { researchInterruptionPresentation } from "../../lib/workbenchInterruption.mjs";

/** Shows canonical interruption facts. Never invents a second whoActsNow story or diagnostics. */
export function ResearchInterruptionPanel({
  model,
  artifacts,
}: {
  model: WorkbenchTaskModel;
  artifacts: WorkbenchArtifact[];
}) {
  const view = researchInterruptionPresentation(model);
  if (!view) return null;

  const retainedNote = artifacts.length
    ? view.retainedSummary
    : "当前尚无可查看的研究产物；已有进度仍可在交互过程中查看。";

  if (view.presentationMode === "awaiting_user") {
    return (
      <section
        className="mb-5 grid gap-3 rounded-lg border border-border bg-surface-soft p-4 text-sm"
        aria-label="需要补充信息"
        data-testid="workbench-research-interruption"
        data-interruption-kind={view.kind}
        data-presentation-mode={view.presentationMode}
      >
        <div className="grid gap-1">
          <h2 className="m-0 text-base font-semibold">{view.summary}</h2>
          <p className="m-0 text-text-secondary">{view.impact}</p>
        </div>
        <dl className="m-0 grid gap-2">
          <div>
            <dt className="font-medium">你现在需要做什么</dt>
            <dd className="m-0 text-text-secondary">{view.userAction}</dd>
          </div>
          <div>
            <dt className="font-medium">已有成果</dt>
            <dd className="m-0 text-text-secondary">{retainedNote}</dd>
          </div>
        </dl>
      </section>
    );
  }

  return (
    <section
      className="mb-4 rounded-lg border border-border bg-surface-soft px-4 py-3 text-sm"
      aria-label="研究状态"
      data-testid="workbench-research-interruption"
      data-interruption-kind={view.kind}
      data-presentation-mode={view.presentationMode}
    >
      <p className="m-0 font-medium text-text">{view.briefStatus}</p>
      <p className="m-0 mt-1 text-text-secondary">{view.userAction}</p>
      <p className="m-0 mt-1 text-text-secondary">{retainedNote}</p>
      {view.controlSummary ? (
        <p
          className="m-0 mt-2 border-t border-border pt-2 text-text-secondary"
          data-testid="workbench-interruption-control-fact"
        >
          {view.controlSummary}
        </p>
      ) : null}
    </section>
  );
}
