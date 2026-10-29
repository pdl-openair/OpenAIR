import type { Task } from "@openair/shared";
import type { WorkbenchArtifact } from "../../model/workbenchTask";
import {
  WORKFLOW_NODE_RESULT_PRESENTATION_ORDER,
  workflowNodeResultPresentationKind,
} from "../artifacts/WorkflowNodeResultPresentation";

function asObject(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/** Use accepted content with its durable artifact identity, never render the state carrier. */
export function acceptedFinalResultArtifacts(
  result: NonNullable<Task["finalResult"]>,
  artifacts: WorkbenchArtifact[],
): WorkbenchArtifact[] {
  const accepted = new Map<string, WorkbenchArtifact>();
  for (const value of Object.values(result.scientificResult ?? {})) {
    const carrier = asObject(value);
    const provenance = asObject(carrier?.provenance);
    const document = asObject(carrier?.validatedArtifact);
    const sourceId = provenance?.sourceArtifactId;
    if (typeof sourceId !== "string" || !document) continue;
    const matches = artifacts.filter((artifact) => artifact.id === sourceId);
    if (matches.length !== 1) continue;
    const artifact = matches[0];
    const kind = workflowNodeResultPresentationKind(artifact);
    if (!kind || !WORKFLOW_NODE_RESULT_PRESENTATION_ORDER.includes(kind))
      continue;
    accepted.set(sourceId, {
      ...artifact,
      format: "json",
      content: JSON.stringify(document),
    });
  }
  return WORKFLOW_NODE_RESULT_PRESENTATION_ORDER.flatMap((kind) =>
    [...accepted.values()].filter(
      (artifact) => workflowNodeResultPresentationKind(artifact) === kind,
    ),
  );
}
