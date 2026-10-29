import { Tag, Typography } from "antd";
import type { Task, UiLanguage } from "@openair/shared";
import type { WorkbenchArtifact } from "../../model/workbenchTask";
import { ArtifactRenderer } from "../artifacts/ArtifactRenderer";
import { acceptedFinalResultArtifacts } from "./acceptedFinalResultArtifacts";

type PublicFinalResult = NonNullable<Task["finalResult"]>;

interface FinalResultPanelProps {
  result: PublicFinalResult;
  language?: UiLanguage;
  className?: string;
  artifacts?: WorkbenchArtifact[];
}

function TextList({ values }: { values: string[] }) {
  if (values.length === 0) return null;
  return (
    <ul className="m-0 grid gap-1.5 pl-5 text-sm leading-relaxed text-text-secondary">
      {values.map((value, index) => (
        <li key={`${value}-${index}`}>{value}</li>
      ))}
    </ul>
  );
}

export function FinalResultPanel({
  result,
  language = "zh-CN",
  className = "",
  artifacts = [],
}: FinalResultPanelProps) {
  const en = language === "en-US";
  const acceptedArtifacts = acceptedFinalResultArtifacts(result, artifacts);
  const dispositionLabel = en
    ? (
        {
          full: "Complete",
          partial: "Partial",
          degraded: "Degraded",
          evidence_gap: "Evidence gap",
        } as const
      )[result.disposition]
    : (
        {
          full: "完整结果",
          partial: "部分结果",
          degraded: "降级结果",
          evidence_gap: "证据仍有缺口",
        } as const
      )[result.disposition];

  return (
    <section
      className={`rounded-xl border border-border bg-surface p-5 shadow-sm ${className}`.trim()}
      aria-labelledby="formal-final-result-heading"
      data-testid="final-result-panel"
    >
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-border pb-4">
        <div>
          <Typography.Title
            id="formal-final-result-heading"
            level={3}
            className="m-0! text-xl! font-semibold! text-text!"
          >
            {en ? "Final research result" : "最终科研结果"}
          </Typography.Title>
          <Typography.Text className="text-sm! text-text-muted!">
            {en
              ? "Current accepted research deliverables"
              : "当前已接受的研究交付"}
          </Typography.Text>
        </div>
        <Tag className="m-0">{dispositionLabel}</Tag>
      </div>

      <div className="grid gap-5">
        {result.directAnswer ? (
          <div
            className="rounded-lg bg-surface-soft px-4 py-3"
            data-testid="final-result-direct-answer"
          >
            <div className="mb-1 text-xs font-medium text-text-muted">
              {en ? "Answer" : "直接回答"}
            </div>
            <div className="whitespace-pre-wrap text-lg font-medium leading-relaxed text-text">
              {result.directAnswer}
            </div>
          </div>
        ) : null}

        {result.keyFindings.length > 0 ? (
          <div>
            <div className="mb-2 text-sm font-semibold text-text">
              {en ? "Key findings" : "关键结论"}
            </div>
            <TextList values={result.keyFindings} />
          </div>
        ) : null}
        {acceptedArtifacts.map((artifact) => (
          <article
            key={artifact.id}
            data-testid="final-result-accepted-product"
          >
            <ArtifactRenderer artifact={artifact} bodyOnly />
          </article>
        ))}
      </div>
    </section>
  );
}
